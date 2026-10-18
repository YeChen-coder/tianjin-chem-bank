"""Private acceptance runner. No production database, no writes to other sessions."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import shutil
import sys
from zipfile import ZipFile
from xml.etree import ElementTree as ET

from question_content import compile_formula, validate_question, validate_bundle
from word_export_v2 import export_paper
from .workflow import generate_candidate
from .glm_transport import GLMClient, read_glm_config
from .protocol import CONTENT_FIELDS, answer_targets

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT / '.work' / 'session-3'


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def nodes(value):
    if isinstance(value, dict):
        if value.get('type'): yield value
        for child in value.values(): yield from nodes(child)
    elif isinstance(value, list):
        for child in value: yield from nodes(child)


def bundle_of(questions, assets, title):
    return {'schema_version': 1, 'paper': {'id': 'paper-ai-'+hashlib.sha256(title.encode()).hexdigest()[:16],
             'title': title, 'question_ids': [q['id'] for q in questions], 'source_sha256': None},
            'questions': questions, 'assets': assets, 'issues': []}


def verify_export(bundle, path, resolver):
    before = json.dumps(bundle, sort_keys=True)
    exported = export_paper(bundle, path, {'include_answers': True, 'include_explanations': True}, resolver)
    ns = {'m': 'http://schemas.openxmlformats.org/officeDocument/2006/math',
          'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
    with ZipFile(path) as archive:
        xml = ET.fromstring(archive.read('word/document.xml'))
        count = len(xml.findall('.//m:oMath', ns))
        expected = sum(n['type'] == 'formula' for q in bundle['questions'] for n in nodes(q))
        images = sum(n['type'] == 'image' for q in bundle['questions'] for n in nodes(q))
        embedded = len(xml.findall('.//w:drawing', ns))
        tables = len(xml.findall('.//w:tbl', ns))
        expected_tables = sum(n['type'] == 'table' for q in bundle['questions'] for n in nodes(q))
        actual_media = {hashlib.sha256(archive.read(name)).hexdigest() for name in archive.namelist() if name.startswith('word/media/')}
        image_ids = {n['asset_id'] for q in bundle['questions'] for n in nodes(q) if n['type'] == 'image'}
        expected_media = {hashlib.sha256(Path(resolver(aid)).read_bytes()).hexdigest() for aid in image_ids}
        media_ok = expected_media <= actual_media
    return {'ok': count == expected and embedded == images and tables == expected_tables and media_ok and before == json.dumps(bundle, sort_keys=True),
            'native_formula_count': count, 'expected_formula_count': expected,
            'embedded_image_count': embedded, 'expected_image_count': images,
            'table_count': tables, 'expected_table_count': expected_tables,
            'image_bytes_preserved': media_ok,
            'bundle_unchanged': before == json.dumps(bundle, sort_keys=True),
            'visual_verified': False, 'export': exported}


def run_real(import_result, numbers, keys_path, run_name, reasoning_effort=None):
    work = (WORKSPACE / run_name).resolve()
    if not work.is_relative_to(WORKSPACE.resolve()) or work.exists():
        raise ValueError('验收输出必须是新的 session-3 私有子目录')
    work.mkdir(parents=True)
    input_path = Path(import_result).resolve()
    original_hash = hashlib.sha256(input_path.read_bytes()).hexdigest()
    imported = json.loads(input_path.read_text(encoding='utf-8'))
    original_source = Path(imported['report']['source']['path'])
    original_source_hash = hashlib.sha256(original_source.read_bytes()).hexdigest()
    selected = [copy.deepcopy(q) for q in imported['bundle']['questions'] if q['number'] in numbers]
    if len(selected) != len(numbers): raise ValueError('没有完整找到指定真实题目')
    referenced = {n['asset_id'] for q in selected for n in nodes(q) if n['type'] == 'image'}
    assets = [copy.deepcopy(a) for a in imported['bundle']['assets'] if a['id'] in referenced]
    manifest = json.loads((input_path.parent/'asset-manifest.json').read_text(encoding='utf-8'))
    paths, copy_receipts = {}, []
    for aid in referenced:
        record = manifest[aid]
        source = Path(record.get('preview_path') or record['local_path']).resolve()
        if not source.is_relative_to(input_path.parent): raise ValueError('资产来源越过已知导入 workspace')
        target = work/'assets'/(aid+source.suffix)
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(source, target)
        paths[aid] = str(target)
        copy_receipts.append({'asset_id': aid, 'source': str(source), 'target': str(target),
                              'sha256': hashlib.sha256(target.read_bytes()).hexdigest()})
    config = read_glm_config(keys_path)
    if reasoning_effort:
        config['GLM_REASONING_EFFORT'] = reasoning_effort
    client = GLMClient(config, max_calls=2*len(selected),
                       observe=lambda meta: print(json.dumps({'event': 'glm_request', **meta}), flush=True))
    results, candidates = [], []
    save(work/'input.json', bundle_of(selected, assets, '真实 GLM 验收母题快照'))
    for question in selected:
        num = question['number']
        def complete(role, system, user, images):
            raw, model = client.complete(role, system, user, images)
            suffix = 'judge' if '独立审题教师' in system else 'generation'
            save(work/f'q{num}-{suffix}-request.json', {'schema_version': 1, 'system': system, 'user': user})
            (work/f'q{num}-{suffix}-response.json').write_text(raw, encoding='utf-8')
            return raw, model
        services = {'validate_question': validate_question, 'compile_formula': compile_formula,
                    'complete': complete, 'resolve_image': lambda aid: paths.get(aid),
                    'current_revision': lambda qid: question['revision']}
        question_asset_ids = {n['asset_id'] for n in nodes(question) if n['type'] == 'image'}
        question_assets = [a for a in assets if a['id'] in question_asset_ids]
        result = generate_candidate(question, question_assets, services, intensity='light',
                                     teacher_feedback='保持小问、选项及必要条件；反应式用完整独立 LaTeX，温度在反应符号上方。保留配图并保证新题仍与图相符。')
        results.append(result)
        save(work/f'q{num}-result.json', result)
        print(json.dumps({'number': num, 'ok': result['ok'], 'status': result['status'],
                          'content_status': result.get('content_status'), 'error': result.get('error'),
                          'issue_codes': [i['code'] for i in result.get('issues', [])]}), flush=True)
        # Retain rejected scientific drafts solely for inspection/export verification.
        if result.get('format_status') == 'passed' and result.get('candidate'): candidates.append(result['candidate'])
        save(work/'usage.json', client.calls)
    report = {'schema_version': 1, 'input_source_sha256': imported['bundle']['paper']['source_sha256'],
              'selected_numbers': numbers, 'candidate_count': len(candidates),
              'accepted_by_ai_count': sum(r.get('content_status') == 'PASS' and r['ok'] for r in results),
              'rejected_by_ai_count': sum(r.get('content_status') == 'FAIL' for r in results),
              'calls': client.calls, 'api_call_count': len(client.calls), 'copy_receipts': copy_receipts,
              'input_json_unchanged': original_hash == hashlib.sha256(input_path.read_bytes()).hexdigest(),
              'original_word_unchanged': original_source_hash == hashlib.sha256(original_source.read_bytes()).hexdigest(),
              'source_json_snapshot_sha256': hashlib.sha256((work/'input.json').read_bytes()).hexdigest(),
              'production_writes': False, 'teacher_confirmed': False, 'visual_verified': False}
    if candidates:
        bundle = bundle_of(candidates, assets, 'GLM 变式候选验收（待教师核对）')
        save(work/'candidates.json', bundle)
        report['bundle_validation'] = validate_bundle(bundle)
        try:
            report['word_export'] = verify_export(bundle, work/'candidates.docx', lambda aid: paths.get(aid))
        except Exception:
            report['word_export'] = {'ok': False, 'message': '公共 Word 导出未完成，需查看候选结构及导出模块'}
    save(work/'report.json', report)
    print(json.dumps({'report': str(work/'report.json'), 'candidate_count': len(candidates),
                      'api_calls': len(client.calls), 'export_ok': report.get('word_export', {}).get('ok')}), flush=True)
    return report


def run_mock(run_name):
    """Use S4's shared readonly format controls, but real compiler and DOCX exporter."""
    work = (WORKSPACE/run_name).resolve()
    if not work.is_relative_to(WORKSPACE.resolve()) or work.exists():
        raise ValueError('模拟输出必须是新的 session-3 子目录')
    work.mkdir(parents=True)
    shared = ROOT/'fixtures/word_latex_v1/synthetic'
    source = json.loads((shared/'bundle.json').read_text(encoding='utf-8'))
    assets = source['assets']
    preview = work/'apparatus.png'
    shutil.copyfile(shared/'apparatus.png', preview)
    candidates, results = [], []
    for question in source['questions']:
        content = {key: copy.deepcopy(question[key]) for key in CONTENT_FIELDS}
        # Shared fixture is a layout control, not a complete scientific answer exemplar.
        for field in ('answers', 'explanations'):
            content[field] = []
            for index, target in enumerate(sorted(answer_targets(question))):
                prefix = f'mock-{question["id"]}-{field}-{index}'
                content[field].append({'id': prefix, 'target_id': target, 'blocks': [
                    {'id': prefix+'-p', 'type': 'paragraph', 'align': 'left', 'inlines': [
                        {'id': prefix+'-t', 'type': 'text', 'text': '模拟格式控制内容，未验收科学结论。'}]}]})
        payload = {'schema_version': 1, 'candidate': content, 'warnings': ['模拟响应，仅供结构验收。']}
        save(work/f'q{question["number"]}-response.json', payload)
        def complete(role, system, user, images):
            if '独立审题教师' in system:
                return json.dumps({'status': 'SUSPECT', 'independent_answer': '模拟核对内容',
                  'knowledge_correct': True, 'conditions_sufficient': True, 'answer_unique': True,
                  'calculation_verified': True, 'grade_level_appropriate': True, 'image_consistent': True,
                  'issues': ['模拟结果不是科学验收'], 'recommendation': '请教师核对'}), 'explicit-mock-judge'
            return json.dumps(payload), 'explicit-mock-generator'
        result = generate_candidate(question, assets, {'validate_question': validate_question,
                    'compile_formula': compile_formula, 'complete': complete,
                    'resolve_image': lambda aid: str(preview)})
        results.append(result)
        if result['ok']: candidates.append(result['candidate'])
    bundle = bundle_of(candidates, assets, '模拟 GLM 格式控制候选（无真实内容验收）')
    save(work/'results.json', results)
    save(work/'candidates.json', bundle)
    report = {'schema_version': 1, 'api_call_count': 0, 'compiler': 'real-public-compiler',
              'generator': 'explicit-test-double', 'science_checked': False,
              'candidate_count': len(candidates), 'validation': validate_bundle(bundle),
              'word_export': verify_export(bundle, work/'candidates.docx', lambda aid: str(preview)),
              'production_writes': False, 'teacher_confirmed': False}
    save(work/'report.json', report)
    print(json.dumps(report, ensure_ascii=True), flush=True)
    return report


def replay_final(run_name):
    """Recheck recorded GLM bodies with final code; zero API calls. Preserve originals."""
    work = (WORKSPACE/run_name).resolve()
    if not work.is_relative_to(WORKSPACE.resolve()) or work.exists():
        raise ValueError('回放输出必须是新的 session-3 子目录')
    work.mkdir(parents=True)
    accepted, rejected, results, assets, paths = [], [], [], [], {}
    sources = [('real-glm-02', '15'), ('real-glm-02', '25'), ('real-glm-03', '22')]
    for run, number in sources:
        saved = WORKSPACE/run
        source = json.loads((saved/'input.json').read_text(encoding='utf-8'))
        question = next(q for q in source['questions'] if q['number'] == number)
        cached = json.loads((saved/f'q{number}-result.json').read_text(encoding='utf-8'))
        raws = iter([(saved/f'q{number}-generation-response.json').read_text(encoding='utf-8'),
                     (saved/f'q{number}-judge-response.json').read_text(encoding='utf-8')])
        models = iter([cached['generator_model'], cached['judgement']['model']])
        ids = {n['asset_id'] for n in nodes(question) if n['type'] == 'image'}
        qa = [a for a in source['assets'] if a['id'] in ids]
        for a in qa:
            if a['id'] not in paths:
                original = next((saved/'assets').glob(a['id']+'.*'))
                target = work/'assets'/original.name
                target.parent.mkdir(exist_ok=True)
                shutil.copyfile(original, target)
                paths[a['id']] = str(target)
                assets.append(copy.deepcopy(a))
        result = generate_candidate(question, qa, {'validate_question': validate_question,
                    'compile_formula': compile_formula, 'complete': lambda *args: (next(raws), next(models)),
                    'resolve_image': lambda aid: paths.get(aid)})
        result['evidence_source'] = {'run': run, 'number': number, 'replayed_without_api': True}
        if number == '15' and result.get('candidate'):
            note = {'id': 'manual-review-atomic-masses-q15', 'code': 'CONDITIONS_REVIEW',
                    'severity': 'review', 'question_id': result['candidate']['id'], 'node_id': None,
                    'message': '计算依赖相对原子质量。独立导出前应补齐原卷必要卷头数据；AI PASS 不代替教师确认。'}
            result['issues'].append(note)
            result['candidate']['issues'].append(copy.deepcopy(note))
        results.append(result)
        if result['ok'] and result['content_status'] == 'PASS': accepted.append(result['candidate'])
        elif result.get('candidate'): rejected.append(result['candidate'])
    save(work/'results.json', results)
    report = {'schema_version': 1, 'new_api_call_count': 0, 'source_runs': sources,
              'accepted_by_ai_count': len(accepted), 'rejected_by_ai_count': len(rejected),
              'teacher_confirmed': False, 'production_writes': False}
    for label, questions, title in [('accepted', accepted, 'GLM 候选：AI核对通过，待教师确认'),
                                    ('rejected', rejected, 'GLM 失败候选：配图答案错误，不得用于正式试卷')]:
        if not questions: continue
        bundle = bundle_of(questions, assets, title)
        save(work/f'{label}-candidates.json', bundle)
        report[label+'_validation'] = validate_bundle(bundle)
        report[label+'_word_export'] = verify_export(bundle, work/f'{label}-candidates.docx', lambda aid: paths.get(aid))
    # Replay fixed malformed and uncompiled responses too, so failed records remain useful.
    from .protocol import normalize_response
    anomalies = []
    for run, number in [('real-glm-01', '22'), ('real-glm-02', '22')]:
        saved = WORKSPACE/run
        source = json.loads((saved/'input.json').read_text(encoding='utf-8'))
        question = next(q for q in source['questions'] if q['number'] == number)
        result = normalize_response((saved/f'q{number}-generation-response.json').read_text(encoding='utf-8'),
                                    question, source['assets'], validate_question=validate_question,
                                    compile_formula=compile_formula)
        anomalies.append({'source_run': run, 'result': result})
    save(work/'recorded-failures.json', anomalies)
    save(work/'report.json', report)
    print(json.dumps({'new_api_call_count': 0, 'accepted_by_ai_count': len(accepted),
                      'rejected_by_ai_count': len(rejected), 'report': str(work/'report.json')}, ensure_ascii=True))
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mock', action='store_true')
    parser.add_argument('--replay-final', action='store_true')
    parser.add_argument('--import-result')
    parser.add_argument('--numbers', nargs='+', default=['15', '25', '22'])
    parser.add_argument('--keys-path')
    parser.add_argument('--run-name', required=True)
    parser.add_argument('--reasoning-effort', choices=['low', 'high', 'max'])
    args = parser.parse_args()
    if args.replay_final:
        replay_final(args.run_name)
    elif args.mock:
        run_mock(args.run_name)
    else:
        if not args.import_result or not args.keys_path:
            parser.error('真实验收需要 --import-result 和 --keys-path')
        run_real(args.import_result, args.numbers, args.keys_path, args.run_name, args.reasoning_effort)


if __name__ == '__main__':
    main()
