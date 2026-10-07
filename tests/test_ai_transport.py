import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import aivariant as a
import banklib as b


@pytest.fixture(autouse=True)
def reset_provider_cooldown(monkeypatch):
    monkeypatch.setattr(a, '_glm_pause_until', 0.0)
    monkeypatch.setattr(a, '_glm_pause_key', '')


def test_glm_has_explicit_reasoning_and_output_budget(monkeypatch):
    monkeypatch.delenv('GLM_REASONING_EFFORT', raising=False)
    monkeypatch.delenv('GLM_MAX_TOKENS', raising=False)
    rich, plain = a._glm_bodies('glm-5.3-flash', 's', 'u', None)
    assert rich['reasoning_effort'] == plain['reasoning_effort'] == 'high'
    assert rich['max_tokens'] == 8192
    assert rich['thinking']['type'] == 'enabled'
    monkeypatch.setenv('GLM_REASONING_EFFORT', 'low')
    monkeypatch.setenv('GLM_MAX_TOKENS', '12000')
    assert a._glm_bodies('glm-5.3', 's', 'u', None)[0]['max_tokens'] == 12000
    assert a._glm_bodies('glm-5.3', 's', 'u', None)[0]['reasoning_effort'] == 'low'


def test_glm_invalid_effort_fails_before_api(monkeypatch):
    monkeypatch.setenv('GLM_REASONING_EFFORT', 'disabled')
    with pytest.raises(a.ModelError, match='low、high 或 max'):
        a._glm_bodies('glm-5.3-flash', 's', 'u', None)


def test_reasoning_only_token_exhaustion_is_explained():
    payload = {'choices':[{'finish_reason':'length','message':{'content':'','reasoning_content':'not a final answer'}}]}
    with pytest.raises(a.ModelError, match='输出额度耗尽'):
        a._message_text(payload)


def test_images_over_eight_are_all_attached(tmp_path, monkeypatch):
    monkeypatch.setattr(b, 'MEDIA', str(tmp_path))
    parts=[]
    for i in range(10):
        name = ('%064x' % i)+'.png'; (tmp_path/name).write_bytes(b'fixture')
        parts.append({'t':'img','sha':'%064x' % i,'src':'/media/'+name})
    paths = a._image_paths([parts])
    assert len(paths) == 10
    assert len(a._user_content('u', paths)) == 11


def test_missing_image_is_not_silently_dropped(tmp_path, monkeypatch):
    monkeypatch.setattr(b, 'MEDIA', str(tmp_path))
    with pytest.raises(a.ModelError, match='图片文件缺失'):
        a._image_paths([[{'t':'img','src':'/media/'+'a'*64+'.png'}]])
    with pytest.raises(a.ModelError, match='图片缺失'):
        a._user_content('u', [str(tmp_path/'missing.png')])


def test_http_error_preserves_provider_code_and_redacts_key(monkeypatch):
    key = 'fixture-sensitive-value'
    monkeypatch.setattr(a, '_glm_key', key)
    monkeypatch.setattr(a, '_http_post', lambda *args: (429, {'error':{'code':'1302','message':'并发超限 '+key}}))
    with pytest.raises(a.ModelError) as error:
        a._glm_chat('glm-5.3-flash', 's', 'u')
    assert '429' in str(error.value) and '1302' in str(error.value)
    assert '并发超限' in str(error.value) and key not in str(error.value)


def test_fallback_failure_keeps_glm_root_cause(monkeypatch):
    monkeypatch.delenv('CHEM_DISABLE_AI', raising=False)
    monkeypatch.setattr(a, '_glm_key', 'fixture-glm')
    monkeypatch.setattr(a, '_ds_key', 'fixture-ds')
    def glm(*args): raise a.ModelError('output_limit')
    def ds(*args): raise a.ModelError('http', '备用请求超时')
    monkeypatch.setattr(a, '_glm_chat', glm)
    monkeypatch.setattr(a, '_deepseek_chat', ds)
    with pytest.raises(a.ModelError) as error:
        a.complete_role('flash', 's', 'u')
    assert '输出额度耗尽' in str(error.value) and '备用请求超时' in str(error.value)


def test_http_transport_reads_partial_chunks_without_waiting_for_8k(monkeypatch):
    class Socket:
        def settimeout(self, timeout): pass
    class Response:
        status=200
        def __init__(self): self.chunks=iter([b': keep-alive\n', b'{"choices":', b'[{"message":{"content":"{}"}}]}'])
        def read1(self, size): return next(self.chunks, b'')
    class Connection:
        sock=Socket()
        def __init__(self, *args, **kwargs): pass
        def request(self, *args, **kwargs): pass
        def getresponse(self): return Response()
        def close(self): pass
    monkeypatch.setattr(a.http.client, 'HTTPSConnection', Connection)
    status, payload = a._http_post('https://example.test/chat', 'fixture', {}, 90)
    assert status == 200 and a._message_text(payload) == '{}'


def test_missing_generated_answer_falls_back_before_saving(monkeypatch):
    monkeypatch.delenv('CHEM_DISABLE_AI', raising=False)
    monkeypatch.setattr(a, '_glm_key', 'fixture-glm')
    monkeypatch.setattr(a, '_ds_key', 'fixture-ds')
    monkeypatch.setattr(a, '_glm_chat', lambda *args: '{"stem":"填写水的化学式____。","answer":""}')
    monkeypatch.setattr(a, '_deepseek_chat', lambda *args: '{"stem":"填写水的化学式____。","answer":"H2O","qtype":"填空题"}')
    text, model = a.complete_role('flash', a.SYSTEM_GENERATOR, 'u')
    assert model == a._ds_flash and json.loads(text)['answer'] == 'H2O'
    monkeypatch.setattr(a, '_deepseek_chat', lambda *args: '{"stem":"题目","answer":"略"}')
    with pytest.raises(a.ModelError, match='没有完整答案'):
        a.complete_role('flash', a.SYSTEM_GENERATOR, 'u')


def test_glm_quota_fallback_cooldown_and_recovery(monkeypatch):
    monkeypatch.delenv('CHEM_DISABLE_AI', raising=False)
    monkeypatch.setattr(a, '_glm_key', 'fixture-glm')
    monkeypatch.setattr(a, '_ds_key', 'fixture-ds')
    clock = [1.0]
    monkeypatch.setattr(a.time, 'monotonic', lambda: clock[0])
    calls = []
    def http(url, key, body, timeout):
        calls.append(url)
        if 'open.bigmodel.cn' in url and calls.count(url) == 1:
            return 429, {'error': {'code': '1113', 'message': 'Insufficient balance'}}
        return 200, {'choices':[{'message':{'content':'{"ok":1}'}}]}
    monkeypatch.setattr(a, '_http_post', http)
    assert a.complete_role('flash', 's', 'u')[1] == a._ds_flash
    assert a._glm_paused()
    assert a.complete_role('pro', 's', 'u')[1] == a._ds_pro
    assert sum('open.bigmodel.cn' in url for url in calls) == 1
    assert a._job_public({'id':1,'paper_id':None,'base_question_id':1,'version_id':1,'phase':'generate','status':'running'})['service_message']
    clock[0] = 602.0
    assert a.complete_role('flash', 's', 'u')[1] == a._glm_flash
    assert not a._glm_paused()


def test_deepseek_format_retry_preserves_budget_and_vision(tmp_path, monkeypatch):
    from test_import import PNG
    image = tmp_path/'diagram.png'; image.write_bytes(PNG)
    monkeypatch.delenv('DEEPSEEK_MAX_TOKENS', raising=False)
    monkeypatch.setattr(a, '_ds_key', 'fixture-ds')
    # Both roles may be configured with the same vision model; do not silently lose its image.
    monkeypatch.setattr(a, '_ds_pro', a._ds_flash)
    calls = []
    def post(key, body):
        calls.append(body)
        return (400, {'error': {'message':'JSON format unsupported'}}) if len(calls) == 1 else (200, {'choices':[{'message':{'content':'{"answer":"H2O"}'}}]})
    monkeypatch.setattr(a, '_post_chat', post)
    assert 'H2O' in a._deepseek_chat(a._ds_flash, 's', 'u', [str(image)])
    assert len(calls) == 2
    assert all(body['thinking']['type'] == 'disabled' and body['max_tokens'] == 8192 for body in calls)
    assert all(len(body['messages'][1]['content']) == 2 for body in calls)


def test_old_answer_is_not_an_anchor_in_generation_or_judging():
    profile = {'knowledge':'物理变化','skill':'辨析','original_answer':'PRIVATE_OLD_ANSWER'}
    user = a._generation_user(profile, None, None, '', 'light', [], retry=True)
    assert 'PRIVATE_OLD_ANSWER' not in user and '未通过独立检查' in user
    assert profile['original_answer'] == 'PRIVATE_OLD_ANSWER'
    judge = a._judge_user({'stem':'新题','qtype':'多选题','answer':'ACD'}, profile, None)
    assert 'PRIVATE_OLD_ANSWER' not in judge and 'ACD' in judge


def test_second_image_version_keeps_vision_and_persists_answer(tmp_path, monkeypatch):
    import sqlite3
    from test_import import PNG
    for key, value in {'DB_PATH':str(tmp_path/'bank.sqlite'), 'DATA_DIR':str(tmp_path),
                       'MEDIA':str(tmp_path/'media'), 'MEDIA_ORIG':str(tmp_path/'media/original'),
                       'IMPORT_DIR':str(tmp_path/'imports')}.items():
        monkeypatch.setattr(b, key, value)
    monkeypatch.delenv('CHEM_DISABLE_AI', raising=False)
    monkeypatch.setattr(a, '_cancelled_jobs', set())
    monkeypatch.setattr(a, '_job_epochs', {})
    con = b.init_db(); con.row_factory = sqlite3.Row
    base, error = b.insert_handwritten(con, {'body':'请根据实验装置图填写仪器名称____。', 'qtype':'实验题'})
    assert not error
    Path(b.MEDIA).mkdir(exist_ok=True)
    picture = Path(b.MEDIA)/('a'*64+'.png'); picture.write_bytes(PNG)
    segments = [[{'t':'text','s':'请根据实验装置图填写仪器名称____。'},
                 {'t':'img','src':'/media/'+picture.name,'sha':'a'*64}]]
    con.execute('UPDATE questions SET segments=?,image_count=1 WHERE id=?', (json.dumps(segments),base['id']))
    con.commit()
    job, error = a.enqueue_base(con,None,base['id'],None,'','light','another')
    assert not error
    con.execute('UPDATE ai_versions SET seq=2 WHERE id=?', (job['version_id'],)); con.commit()
    monkeypatch.setattr(a, '_ensure_profile', lambda *args: ({'knowledge':'实验仪器','skill':'识图','qtype':'实验题'}, {'requires_image':True}))
    calls = []
    def complete(role, system, user, paths=None):
        calls.append((role,system,paths))
        data = {'stem':'写出图中仪器的名称____。','answer':'铁架台','analysis':'根据仪器结构识别。',
                'qtype':'实验题','depends_on_image':True,'segments':segments} if system == a.SYSTEM_GENERATOR else {'status':'PASS','independent_answer':'铁架台'}
        return json.dumps(data,ensure_ascii=False), 'vision-fixture'
    monkeypatch.setattr(a, 'complete_role', complete)
    a._process(con, job['id'])
    assert len(calls) == 2 and all(role == 'flash' and paths == [str(picture)] for role,_,paths in calls)
    version = con.execute('SELECT * FROM ai_versions WHERE id=?',(job['version_id'],)).fetchone()
    assert version['status'] == 'PASS'
    question = con.execute('SELECT answer,image_count FROM questions WHERE id=?',(version['question_id'],)).fetchone()
    assert question['answer'] == '铁架台\n\n根据仪器结构识别。' and question['image_count'] == 1
    assert con.execute('SELECT answer FROM questions WHERE id=?',(base['id'],)).fetchone()[0] == ''
    con.close()
