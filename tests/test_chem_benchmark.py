import hashlib
import json
from pathlib import Path
import sqlite3
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import chem_benchmark as cb
from test_ai_answers import bank, responses


def source_fixture(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    with sqlite3.connect(source / 'bank.sqlite') as con:
        con.execute('CREATE TABLE questions(id INTEGER PRIMARY KEY)')
        con.execute('INSERT INTO questions VALUES(7)')
    manifest = {'question_ids': [7], 'question_count': 1, 'files': {'bank.sqlite': {
        'sha256': hashlib.sha256((source / 'bank.sqlite').read_bytes()).hexdigest()}},
        'stem_incomplete_ids': [], 'questions': []}
    (source / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    return source, manifest


def test_source_is_verified_without_any_writes(tmp_path):
    source, manifest = source_fixture(tmp_path)
    before = {p.name: p.read_bytes() for p in source.iterdir()}
    assert cb.validate_source(source) == manifest
    assert before == {p.name: p.read_bytes() for p in source.iterdir()}


def test_tampered_source_fails_before_running_ai(tmp_path):
    source, _ = source_fixture(tmp_path)
    with (source / 'bank.sqlite').open('ab') as f:
        f.write(b'tampered')
    with pytest.raises(ValueError, match='校验失败'):
        cb.validate_source(source)


def test_unlisted_question_is_rejected(tmp_path):
    source, manifest = source_fixture(tmp_path)
    manifest.update(question_ids=[8])
    (source / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(ValueError, match='题号'):
        cb.validate_source(source)


def test_output_cannot_overlap_source(tmp_path):
    source, _ = source_fixture(tmp_path)
    with pytest.raises(SystemExit):
        cb.main(['--source', str(source), '--output', str(source / 'results')])


def test_missing_provider_report_does_not_claim_completion(tmp_path, monkeypatch):
    source, manifest = source_fixture(tmp_path)
    output = tmp_path / 'report'
    output.mkdir()
    report = cb.summarize(output, manifest, ['deepseek', 'kimi'])
    assert all(p['completed'] == 0 and p['status'] != 'complete' for p in report['providers'].values())
    assert '不是人工标注正确率' in (output / 'comparison.html').read_text(encoding='utf-8')


def test_recheck_archives_original_calls_and_updates_only_result_copy(bank, tmp_path, monkeypatch):
    con, pid = bank
    responses(monkeypatch, disagree=False)
    queued, _ = cb.aa.enqueue(con, pid, 1)
    snapshot = json.loads(con.execute('SELECT snapshot FROM answer_jobs WHERE id=?',
                                     (queued['jobs'][0]['id'],)).fetchone()[0])
    payload = cb.aa.evaluate(snapshot, lambda phase: None)
    payload['parts'][0].update(status='SUSPECT', answer='', reason='fixture old local classification')
    target = tmp_path / 'comparison' / 'deepseek'
    (target / 'results').mkdir(parents=True)
    with sqlite3.connect(target / 'bank.sqlite') as copy:
        con.backup(copy)
    record = {'question_id': 1, 'provider': 'deepseek', 'status': 'done', 'confirmed': 1,
              'suspect': 1, 'parts_total': 2, 'fully_confirmed': False, 'pipeline_complete': True,
              'payload': payload, 'calls': [{'usage': {'total_tokens': 1234}}]}
    cb.save_json(target / 'results' / '1.json', record)
    cb.save_json(target / 'run.json', {'fingerprint': 'fixture-original-generation',
                                     'settings': cb.settings('deepseek')})
    source_before = con.execute('SELECT answer FROM questions WHERE id=1').fetchone()[0]
    monkeypatch.setattr(cb.ai, 'complete_role', lambda *args, **kwargs: pytest.fail('Replay must not call a provider'))
    cb.recheck_provider('deepseek', target, {'question_ids': [1], 'files': {}})
    original = json.loads((target / 'raw-results' / '1.json').read_text(encoding='utf-8'))
    latest = json.loads((target / 'results' / '1.json').read_text(encoding='utf-8'))
    assert original['confirmed'] == 1 and latest['confirmed'] == 2
    assert latest['calls'] == original['calls']
    assert latest['classification']['original_confirmed'] == 1
    assert con.execute('SELECT answer FROM questions WHERE id=1').fetchone()[0] == source_before


def test_pending_worker_does_not_open_bank_or_api_when_provider_stopped(tmp_path, monkeypatch):
    from threading import Event
    stop = Event()
    stop.set()
    monkeypatch.setattr(cb, 'connect', lambda *args: pytest.fail('Pending work should stay untouched'))
    result = cb.evaluate_question('deepseek', tmp_path/'not-created', 7, {}, stop_event=stop)
    assert result['status'] == 'waiting_provider'
    assert not (tmp_path/'not-created').exists()


def test_old_rate_limit_does_not_retry_a_current_input_failure(tmp_path, monkeypatch):
    target = tmp_path / 'kimi'
    (target/'results').mkdir(parents=True)
    record = {'question_id': 7, 'status': 'error', 'pipeline_complete': False,
              'calls': [{'http_status': 429}, {'http_status': 200}],
              'error': 'AI 没有完整列出作答位置'}
    cb.save_json(target/'results'/'7.json', record)
    monkeypatch.setattr(cb, 'connect', lambda *args: pytest.fail('Current input refusal must not be rerun'))
    assert cb.evaluate_question('kimi', target, 7, {}, retry_service_errors=True) == record
