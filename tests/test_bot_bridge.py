import concurrent.futures
import json
from pathlib import Path
import subprocess
import sys
import threading
import urllib.error
import urllib.request

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app
import banklib as b
import bot_bridge as bridge
from test_import import PNG


@pytest.fixture
def bot_service(tmp_path, monkeypatch):
    for key, value in {'DB_PATH': str(tmp_path/'bank.sqlite'), 'DATA_DIR': str(tmp_path),
                       'MEDIA': str(tmp_path/'media'), 'MEDIA_ORIG': str(tmp_path/'media/original'),
                       'IMPORT_DIR': str(tmp_path/'imports')}.items():
        monkeypatch.setattr(b, key, value)
    monkeypatch.setenv('CHEM_DISABLE_AI', '1')
    con = b.init_db()
    for body, qtype in [('实验室制取二氧化碳，写出气体收集方法。', '实验题'),
                        ('计算二氧化碳的质量。', '计算题'),
                        ('比较金属活动性顺序。', '简答题'),
                        ('隐藏的二氧化碳草稿。', '实验题')]:
        result, error = b.insert_handwritten(con, {'body': body, 'qtype': qtype})
        assert not error
    (tmp_path/'media').mkdir(exist_ok=True)
    image = 'a'*64 + '.png'
    (tmp_path/'media'/image).write_bytes(PNG)
    segments = [[{'t': 'text', 's': '实验室制取二氧化碳，写出气体收集方法。'},
                 {'t': 'img', 'src': '/media/'+image, 'sha': 'a'*64}]]
    con.execute('UPDATE questions SET segments=?,image_count=1 WHERE id=1', (json.dumps(segments),))
    con.execute('UPDATE questions SET in_bank=0 WHERE id=4')
    b.apply_paper(con, {'name': '教师原有试卷', 'ids': [3]})
    con.commit()
    con.close()
    server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    url = 'http://127.0.0.1:%d' % server.server_address[1]
    def call(path, payload=None, headers=None):
        data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
        req = urllib.request.Request(url + path, data=data, headers=headers or {'Content-Type': 'application/json'})
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(req, timeout=5) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())
    yield call, url, tmp_path
    server.shutdown()
    server.server_close()
    worker.join()


def plan(call, **changes):
    result = {'request_id': 'teacher-20261002-1', 'name': '二氧化碳专项练习', 'question_ids': [2, 1],
              'bank_revision': call('/api/bot/info')[1]['bank_revision']}
    result.update(changes)
    return result


def test_search_pagination_filters_and_hidden_drafts(bot_service):
    call, _, _ = bot_service
    status, result = call('/api/bot/info')
    assert status == 200 and result['question_count'] == 3
    status, result = call('/api/bot/search', {'keywords': ['二氧化碳'], 'limit': 1})
    assert status == 200 and result['total'] == 2 and result['next_offset'] == 1
    assert result['items'][0]['id'] == 1 and result['items'][0]['images'][0]['available']
    _, page = call('/api/bot/search', {'keywords': ['二氧化碳'], 'offset': 1, 'limit': 1})
    assert page['items'][0]['id'] == 2 and page['next_offset'] is None
    _, filtered = call('/api/bot/search', {'keywords': ['二氧化碳'], 'types': ['实验题'], 'has_images': True})
    assert [q['id'] for q in filtered['items']] == [1]
    _, excluded = call('/api/bot/search', {'query': '二氧化碳', 'exclude_ids': [1]})
    assert [q['id'] for q in excluded['items']] == [2]
    assert call('/api/bot/questions', {'question_ids': [4]})[0] == 400


def test_new_paper_preserves_order_images_and_existing_papers(bot_service):
    call, _, _ = bot_service
    con = b.open_db()
    before = con.execute('SELECT id,body,segments,qtype FROM questions ORDER BY id').fetchall()
    con.close()
    status, result = call('/api/bot/papers', plan(call))
    assert status == 200 and result['count'] == 2 and result['paper']['ids'] == [2, 1]
    assert result['preview_path'] == '/?paper=2&view=1'
    assert call('/api/papers/1')[1]['ids'] == [3]
    con = b.open_db()
    assert con.execute('SELECT id,body,segments,qtype FROM questions ORDER BY id').fetchall() == before
    assert con.execute('SELECT COUNT(*) FROM ai_jobs').fetchone()[0] == 0
    con.close()
    con = b.init_db()
    assert b.get_paper(con, 2)['ids'] == [2, 1]
    con.close()


def test_repeated_concurrent_submission_creates_one_paper(bot_service):
    call, _, _ = bot_service
    payload = plan(call)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: call('/api/bot/papers', payload), range(4)))
    assert all(status == 200 for status, _ in results)
    assert {result['paper']['id'] for _, result in results} == {2}
    assert sum(not result['duplicate'] for _, result in results) == 1
    assert call('/api/bot/papers', dict(payload, name='另一份试卷'))[0] == 409
    assert len(call('/api/papers')[1]['papers']) == 2


@pytest.mark.parametrize('ids', [[999], [4], [1, 1], [True], [], list(range(1, 202))])
def test_invalid_ids_never_make_partial_papers(bot_service, ids):
    call, _, _ = bot_service
    assert call('/api/bot/papers', plan(call, question_ids=ids))[0] == 400
    assert len(call('/api/papers')[1]['papers']) == 1


def test_changed_or_wrong_bank_is_rejected(bot_service):
    call, _, _ = bot_service
    payload = plan(call)
    con = b.open_db()
    con.execute("UPDATE questions SET body=body || ' 教师修订' WHERE id=1")
    con.commit()
    con.close()
    status, result = call('/api/bot/papers', payload)
    assert status == 409 and '题库与检索时不同' in result['error']
    assert len(call('/api/papers')[1]['papers']) == 1


def test_missing_images_and_dry_run(bot_service):
    call, _, tmp_path = bot_service
    payload = plan(call)
    status, result = call('/api/bot/papers', dict(payload, dry_run=True))
    assert status == 200 and result['dry_run'] and len(call('/api/papers')[1]['papers']) == 1
    (tmp_path/'media'/('a'*64+'.png')).unlink()
    status, result = call('/api/bot/papers', payload)
    assert status == 400 and '图片不完整' in result['error']
    assert len(call('/api/papers')[1]['papers']) == 1


def test_bot_api_rejects_cross_origin_wrong_host_and_form_posts(bot_service):
    call, _, _ = bot_service
    assert call('/api/bot/search', {}, {'Content-Type': 'application/json', 'Origin': 'https://untrusted.example'})[0] == 403
    assert call('/api/bot/info', headers={'Host': 'untrusted.example'})[0] == 403
    assert call('/api/bot/search', {}, {'Content-Type': 'text/plain'})[0] == 415
    assert call('/api/bot/search', {'knowledge': 'invented-tag'})[0] == 400


def test_cli_search_create_and_repeat(bot_service, monkeypatch):
    call, url, tmp_path = bot_service
    monkeypatch.setenv('HTTP_PROXY', 'http://127.0.0.1:1')
    command = [sys.executable, str(Path(app.__file__).parent/'chem_bot.py'), '--server', url]
    def run(*args):
        completed = subprocess.run(command + list(args), capture_output=True, encoding='utf-8', timeout=10)
        assert completed.returncode == 0, completed.stdout + completed.stderr
        return json.loads(completed.stdout)
    assert run('doctor')['question_count'] == 3
    assert run('search', '--keyword', '二氧化碳')['total'] == 2
    assert [q['id'] for q in run('show', '--ids', '2,1')['items']] == [2, 1]
    saved_plan = tmp_path/'plan.json'
    saved_plan.write_text(json.dumps(plan(call), ensure_ascii=False), encoding='utf-8-sig')
    assert run('create', '--plan', str(saved_plan), '--dry-run')['dry_run']
    result = run('create', '--plan', str(saved_plan))
    assert result['preview_url'] == url+'/?paper=2&view=1'
    assert run('create', '--plan', str(saved_plan))['duplicate']
    import chem_bot
    opened = []
    monkeypatch.setattr(chem_bot.webbrowser, 'open', opened.append)
    assert chem_bot.main(['--server', url, 'create', '--plan', str(saved_plan), '--open'])['duplicate']
    assert opened == [result['preview_url']]
    opened.clear()
    assert chem_bot.main(['--server', url, 'create', '--plan', str(saved_plan), '--dry-run', '--open'])['dry_run']
    assert opened == []


def test_cli_rejects_remote_servers_and_missing_database(tmp_path, monkeypatch):
    import chem_bot
    with pytest.raises(Exception, match='用户电脑'):
        chem_bot.server_url('https://example.com')
    monkeypatch.setenv('CHEM_DATA_DIR', str(tmp_path))
    with pytest.raises(chem_bot.ToolError, match='没有题库'):
        chem_bot.start('http://127.0.0.1:1')
