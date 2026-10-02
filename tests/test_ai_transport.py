import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import aivariant as a
import banklib as b


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
