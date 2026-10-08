"""Reproducible provider comparison using the application's real answer pipeline.

All writes go to provider-specific copies; source bank and the normal bank stay untouched.
No reference answers are sent to the models. Self-verification is not ground-truth accuracy.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import html
import json
import os
from pathlib import Path
import shutil
import sqlite3
from threading import Event
import time
import uuid

import aivariant as ai
import aianswers as aa
import banklib


def save_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_source(source):
    source = Path(source).resolve()
    manifest = json.loads((source / 'manifest.json').read_text(encoding='utf-8'))
    ids = manifest['question_ids']
    if len(ids) != manifest['question_count'] or len(set(ids)) != len(ids):
        raise ValueError('manifest 题号数量不一致或有重复')
    for name, expected in manifest['files'].items():
        path = (source / name).resolve()
        if not path.is_relative_to(source) or digest(path) != expected['sha256']:
            raise ValueError('benchmark 文件校验失败：' + name)
    # Immutable read-only connection: never creates a journal beside the source.
    with sqlite3.connect((source / 'bank.sqlite').as_uri() + '?mode=ro&immutable=1', uri=True) as con:
        actual = {r[0] for r in con.execute('SELECT id FROM questions')}
    if actual != set(ids):
        raise ValueError('benchmark 库必须只包含 manifest 中的题号')
    return manifest


def settings(provider):
    if provider == 'kimi':
        return {'vision_model': ai._kimi_flash, 'text_model': ai._kimi_pro,
                'max_tokens': ai._config_int('KIMI_MAX_TOKENS', 32768),
                'reasoning': os.environ.get('KIMI_REASONING_EFFORT', 'high')
                if ai._kimi_flash.startswith('kimi-k3') else 'thinking enabled (K2.x)',
                'timeout': ai._config_int('KIMI_TIMEOUT', 300, 30, 900)}
    return {'vision_model': ai._ds_flash, 'text_model': ai._ds_pro,
            'max_tokens': ai._config_int('DEEPSEEK_MAX_TOKENS', 32768),
            'reasoning': os.environ.get('DEEPSEEK_REASONING_EFFORT', 'high'),
            'timeout': ai._config_int('DEEPSEEK_TIMEOUT', 300, 30, 900)}


def fingerprint(manifest, config):
    code = {p: digest(Path(__file__).parent / p) for p in
            ('chem_benchmark.py', 'aivariant.py', 'aianswers.py', 'banklib.py', 'question_analysis.py')}
    value = {'files': manifest['files'], 'question_ids': manifest['question_ids'], 'settings': config, 'code': code}
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def connect(path):
    con = sqlite3.connect(str(path), timeout=30)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA busy_timeout=30000')
    return con


def prepare(source, target, manifest, config):
    signature = fingerprint(manifest, config)
    meta = target / 'run.json'
    if meta.exists():
        info = json.loads(meta.read_text(encoding='utf-8'))
        if signature not in (info['fingerprint'], info.get('classification_fingerprint')):
            raise ValueError('代码、输入或参数已变化；请使用新的 --output 目录以免混合结果')
        return info
    if target.exists():
        raise ValueError('输出子目录已存在但无 run.json，请使用新的 --output 目录')
    target.mkdir(parents=True)
    shutil.copy2(source / 'bank.sqlite', target / 'bank.sqlite')
    shutil.copytree(source / 'media', target / 'media')
    (target / 'results').mkdir()
    with connect(target / 'bank.sqlite') as con:
        aa.ensure_schema(con)
        # Imported active jobs belong to the source machine, not this benchmark.
        con.execute("UPDATE answer_jobs SET status='cancelled',phase='cancelled' WHERE status IN ('queued','running')")
        if con.execute("SELECT 1 FROM sqlite_master WHERE name='ai_jobs'").fetchone():
            con.execute("UPDATE ai_jobs SET status='cancelled' WHERE status IN ('queued','running')")
        placeholders = ','.join('?' for _ in manifest['question_ids'])
        con.execute('DELETE FROM paper_items WHERE paper_id=1 AND question_id NOT IN (' + placeholders + ')',
                    manifest['question_ids'])
    info = {'fingerprint': signature, 'settings': config, 'source': str(source),
            'question_ids': manifest['question_ids'], 'prompt_version': aa.PROMPT_VERSION,
            'started_at_utc': datetime.now(timezone.utc).isoformat(),
            'policy': 'fresh plan; 3 independent solutions; same-provider judge; no fallback; no prior answers'}
    save_json(meta, info)
    return info


def evaluate_question(provider, target, qid, baseline, retry_service_errors=False, stop_event=None):
    destination = target / 'results' / ('%s.json' % qid)
    previous = None
    if destination.exists():
        previous = json.loads(destination.read_text(encoding='utf-8'))
        recent = previous['calls'][previous.get('last_attempt_call_offset', 0):]
        service_failed = not previous['pipeline_complete'] and (previous.get('blocked_by_provider') or
                         any(c['http_status'] != 200 for c in recent))
        # A successful HTTP response followed by a failed plan is an input/schema
        # issue, not an old rate-limit error to retry indefinitely.
        if previous['status'] == 'error' and recent and recent[-1]['http_status'] == 200:
            service_failed = False
        if not (retry_service_errors and service_failed):
            return previous
    if stop_event is not None and stop_event.is_set():
        return {'question_id': qid, 'status': 'waiting_provider'}
    con = connect(target / 'bank.sqlite')
    started = time.monotonic()
    calls = list(previous['calls']) if previous else []
    if previous:
        attempts = target / 'attempts'
        attempts.mkdir(exist_ok=True)
        save_json(attempts / ('%s-%s.json' % (qid, uuid.uuid4().hex)), previous)

    def observe(call):
        calls.append(call)
        if stop_event is not None and call['http_status'] in (401, 402, 403):
            stop_event.set()
    row = con.execute('SELECT * FROM questions WHERE id=?', (qid,)).fetchone()
    snapshot = {'body': row['body'], 'qtype': row['qtype'],
                'segments': json.loads(row['segments'] or '[]'), 'previous': None}
    job_id = con.execute("""INSERT INTO answer_jobs(question_id,paper_id,status,phase,snapshot,created_at)
        VALUES(?,1,'running','plan',?,?)""", (qid, aa._json(snapshot), aa._now())).lastrowid
    con.commit()

    def progress(phase):
        con.execute('UPDATE answer_jobs SET phase=? WHERE id=?', (phase, job_id))
        con.commit()
        print('%s q%s %s' % (provider, qid, phase), flush=True)

    result = {'question_id': qid, 'provider': provider, 'baseline': baseline,
              'has_images': bool(ai.collect_imgs(snapshot['segments'])), 'calls': calls,
              'last_attempt_call_offset': len(calls)}
    try:
        with ai.provider_scope(provider, observe, stop_event):
            payload = aa.evaluate(snapshot, progress, resume=(previous or {}).get('payload'))
        counts = Counter(p['status'] for p in payload['parts'])
        result.update(status='done', confirmed=counts['CONFIRMED'],
                      suspect=len(payload['parts']) - counts['CONFIRMED'], parts_total=len(payload['parts']),
                      fully_confirmed=bool(payload['parts']) and counts['CONFIRMED'] == len(payload['parts']),
                      pipeline_complete=not payload['failures'] and not payload['judge_error'], payload=payload)
        with con:
            aa._write_record(con, row, payload)
            con.execute("UPDATE answer_jobs SET status='done',phase='done',result=?,finished_at=? WHERE id=?",
                        (aa._json(payload), aa._now(), job_id))
    except Exception as exc:
        result.update(status='error', error=ai._safe_text(exc), confirmed=0, suspect=0,
                      parts_total=0, fully_confirmed=False, pipeline_complete=False)
        with con:
            con.execute("UPDATE answer_jobs SET status='error',phase='error',error=?,finished_at=? WHERE id=?",
                        (result['error'], aa._now(), job_id))
    finally:
        con.close()
    result['elapsed_seconds'] = round(time.monotonic() - started, 3) + (previous or {}).get('elapsed_seconds', 0)
    result['blocked_by_provider'] = bool(stop_event is not None and stop_event.is_set())
    if previous:
        result['resume'] = {'method': 'reuse valid plan and successful independent solutions; retry failed service steps',
                            'reused_solution_attempts': [s['attempt'] for s in (previous.get('payload') or {}).get('solutions', [])]}
    if (target / 'raw-results').exists():
        save_json(target / 'raw-results' / destination.name, result)
    save_json(destination, result)
    print('%s q%s %s confirmed=%s suspect=%s' %
          (provider, qid, result['status'], result['confirmed'], result['suspect']), flush=True)
    return result


def recheck_provider(provider, target, manifest):
    """Reclassify recorded responses with current local guards, without API calls."""
    meta = target / 'run.json'
    if not meta.exists():
        return
    paths = list((target / 'results').glob('*.json'))
    if {int(p.stem) for p in paths} != set(manifest['question_ids']):
        raise ValueError('请等该服务全部题目完成，再用 --recheck；不混合新旧规则的未完成结果')
    raw = target / 'raw-results'
    raw.mkdir(exist_ok=True)
    changed = []
    with connect(target / 'bank.sqlite') as con:
        for path in paths:
            original = raw / path.name
            if not original.exists():
                shutil.copy2(path, original)
            result = json.loads(original.read_text(encoding='utf-8'))
            if result['status'] != 'done':
                continue
            row = con.execute('SELECT * FROM questions WHERE id=?', (result['question_id'],)).fetchone()
            snapshot = {'body': row['body'], 'qtype': row['qtype'],
                        'segments': json.loads(row['segments'] or '[]'), 'previous': None}
            payload = aa.recheck_evidence(snapshot, result['payload'])
            confirmed = sum(p['status'] == 'CONFIRMED' for p in payload['parts'])
            result['classification'] = {'method': 'local evidence replay; no API requests',
                                        'original_confirmed': result['confirmed'],
                                        'aianswers_sha256': digest(Path(aa.__file__)),
                                        'rechecked_at_utc': aa._now()}
            if confirmed != result['confirmed']:
                changed.append(result['question_id'])
            result.update(payload=payload, confirmed=confirmed, suspect=len(payload['parts'])-confirmed,
                          parts_total=len(payload['parts']),
                          fully_confirmed=bool(payload['parts']) and confirmed == len(payload['parts']))
            aa._write_record(con, row, payload)
            con.execute("UPDATE answer_jobs SET result=? WHERE id=(SELECT MAX(id) FROM answer_jobs WHERE question_id=?)",
                        (aa._json(payload), result['question_id']))
            con.commit()
            save_json(path, result)
    info = json.loads(meta.read_text(encoding='utf-8'))
    info['classification_fingerprint'] = fingerprint(manifest, info['settings'])
    info['classification_changed_question_ids'] = changed
    info['classification_policy'] = 'Original API evidence preserved in raw-results; current application guards replayed offline'
    save_json(meta, info)
    print('%s local recheck: %s changed questions; zero API calls' % (provider, changed), flush=True)


def summarize(output, manifest, providers):
    count = len(manifest['question_ids'])
    report = {'source_question_count': count,
              'original_source_question_count': manifest.get('original_source_question_count', count),
              'question_ids': manifest['question_ids'],
              'stem_incomplete_ids': manifest['stem_incomplete_ids'], 'providers': {}}
    rows = []
    for provider in providers:
        target = output / provider
        run = json.loads((target / 'run.json').read_text(encoding='utf-8')) if (target / 'run.json').exists() else {}
        records = [json.loads(p.read_text(encoding='utf-8')) for p in sorted((target / 'results').glob('*.json'))]
        records = [r for r in records if r['question_id'] in manifest['question_ids']]
        normal = [r for r in records if r['question_id'] not in manifest['stem_incomplete_ids']]
        totals = {key: sum(r.get(key, 0) for r in records) for key in
                  ('confirmed', 'suspect', 'parts_total', 'fully_confirmed', 'pipeline_complete', 'elapsed_seconds')}
        tokens = Counter()
        for r in records:
            for call in r['calls']:
                for key, value in (call.get('usage') or {}).items():
                    if isinstance(value, int):
                        tokens[key] += value
        info = {'completed': len(records), 'errors': sum(r['status'] == 'error' for r in records),
                **totals, 'usage': dict(tokens), 'settings': run.get('settings') or settings(provider),
                'classification_changed_question_ids': run.get('classification_changed_question_ids', []),
                'complete_stem_questions': len(normal),
                'complete_stem_fully_confirmed': sum(r['fully_confirmed'] for r in normal),
                'status': ('complete' if not any(r['status'] == 'error' or not r['pipeline_complete'] for r in records)
                           else 'finished_with_errors') if len(records) == len(manifest['question_ids']) else 'partial_or_waiting_for_key'}
        report['providers'][provider] = info
        save_json(target / 'summary.json', info) if target.exists() else None
        for r in records:
            rows.append('<tr><td>%s</td><td>%s%s</td><td>%s</td><td>%s/%s</td><td>%s</td></tr>' %
                        (provider, r['question_id'], '（题干残缺）' if r['baseline']['stem_incomplete'] else '',
                         r['status'], r['confirmed'], r['parts_total'], html.escape(r.get('error') or
                         '；'.join(p['label'] + '：' + p['reason'] for p in r.get('payload', {}).get('parts', [])
                                  if p['status'] != 'CONFIRMED'))))
    save_json(output / 'comparison.json', report)
    cards = ''.join('<p><b>%s</b>：已跑 %s/%s；流程完整 %s；整题自检通过 %s；小问通过 %s/%s；错误 %s；'
                    '完整题干通过 %s/%s；tokens %s。</p>' %
                    (p, s['completed'], count, s['pipeline_complete'], s['fully_confirmed'], s['confirmed'],
                     s['parts_total'], s['errors'], s['complete_stem_fully_confirmed'],
                     s['complete_stem_questions'], s['usage'].get('total_tokens', '未返回'))
                    for p, s in report['providers'].items())
    document = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>46 道疑难化学题 · 模型对比</title><style>body{font:16px "Microsoft YaHei",sans-serif;margin:40px;color:#18334b}
table{border-collapse:collapse;width:100%}td,th{padding:12px;border:1px solid #ccd7e0;text-align:left}td:last-child{max-width:700px}
</style><h1>46 道疑难化学题 · 模型对比</h1><p>这是模型自检通过率，不是人工标注正确率。
基准只提供原有存疑记录，没有标准答案。每组重新读题，三次独立作答并核对；没有跨服务回退，也不保留旧答案。
374、375、381、9024 题干残缺，单独统计。两组自动拆出的小问数可能不同，不能直接视为正确率比较。</p>'''
    document = document.replace('46 道疑难化学题', '%s 道疑难化学题' % count)
    document += '<p>原始副本 %s 题；本次对比 %s 题，题号：%s。</p>' % (
        report['original_source_question_count'], count, '、'.join(map(str, manifest['question_ids'])))
    document += cards + '<table><tr><th>服务</th><th>题号</th><th>状态</th><th>通过小问/全部小问</th><th>待核对原因</th></tr>' + ''.join(rows) + '</table></html>'
    (output / 'comparison.html').write_text(document, encoding='utf-8')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description='在独立副本中复用项目补答案流程做模型对比')
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--scope', type=Path, help='JSON 文件中的 question_ids 指定本次对比范围')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--providers', nargs='+', choices=['deepseek', 'kimi'], default=['deepseek', 'kimi'])
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--report-only', action='store_true')
    parser.add_argument('--recheck', action='store_true', help='用当前本地规则重判已完成的原始证据，不调用 API')
    parser.add_argument('--retry-service-errors', action='store_true', help='只续跑曾遇到 HTTP 错误的未完成题，复用成功步骤')
    args = parser.parse_args(argv)
    if not 1 <= args.workers <= 4:
        parser.error('--workers 必须为 1 至 4')
    source, output = args.source.resolve(), args.output.resolve()
    if output == source or output.is_relative_to(source) or source.is_relative_to(output):
        parser.error('输出必须放在输入目录以外的独立目录')
    manifest = validate_source(source)
    if args.scope:
        scope = json.loads(args.scope.read_text(encoding='utf-8'))
        ids = scope['question_ids']
        if not ids or any(type(qid) is not int for qid in ids) or len(set(ids)) != len(ids) or not set(ids) <= set(manifest['question_ids']):
            parser.error('--scope 题号必须非空、无重复且全部来自输入 manifest')
        manifest = dict(manifest, original_source_question_count=manifest['question_count'],
                        question_count=len(ids), question_ids=ids,
                        questions=[q for q in manifest['questions'] if q['id'] in ids])
    ai._load_provider_config()
    output.mkdir(parents=True, exist_ok=True)
    report_providers = list(dict.fromkeys([p for p in ('deepseek', 'kimi') if (output/p/'run.json').exists()] + args.providers))
    if args.report_only:
        print(json.dumps(summarize(output, manifest, report_providers), ensure_ascii=False, indent=2))
        return 0
    lock = output / '.run.lock'
    descriptor = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.close(descriptor)
    try:
        baseline = {r['id']: r for r in manifest['questions']}
        if args.recheck:
            for provider in args.providers:
                recheck_provider(provider, output / provider, manifest)
            summarize(output, manifest, report_providers)
            validate_source(source)
            return 0
        for provider in args.providers:
            if not (ai._kimi_key if provider == 'kimi' else ai._ds_key):
                print('%s: waiting for %s_API_KEY in keys.local' % (provider, provider.upper()), flush=True)
                continue
            target = output / provider
            prepare(source, target, manifest, settings(provider))
            banklib.MEDIA = str(target / 'media')
            stop_event = Event()
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                futures = [pool.submit(evaluate_question, provider, target, qid, baseline[qid],
                                       args.retry_service_errors, stop_event)
                           for qid in manifest['question_ids']]
                for future in as_completed(futures):
                    future.result()
                    summarize(output, manifest, report_providers)
        validate_source(source)
        report = summarize(output, manifest, report_providers)
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
        return 0 if all(p['status'] == 'complete' and not p['errors']
                        and p['pipeline_complete'] == len(manifest['question_ids'])
                        for p in report['providers'].values()) else 2
    finally:
        lock.unlink(missing_ok=True)


if __name__ == '__main__':
    raise SystemExit(main())
