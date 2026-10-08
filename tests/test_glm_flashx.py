import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import aivariant as ai


@pytest.fixture(autouse=True)
def glm_config(monkeypatch):
    monkeypatch.delenv('CHEM_DISABLE_AI', raising=False)
    monkeypatch.setenv('CHEM_AI_PROVIDER', 'glm')
    monkeypatch.setattr(ai, '_glm_key', 'fixture-glm')
    monkeypatch.setattr(ai, '_glm_flashx', 'glm-5.3-flashx')
    monkeypatch.setattr(ai, '_glm_flash', 'glm-5.3-flash')
    monkeypatch.setattr(ai, '_glm_pro', 'glm-5.3')
    monkeypatch.setattr(ai, '_glm_flashx_pauses', {})
    monkeypatch.setattr(ai, '_glm_pause_until', 0.0)
    monkeypatch.setattr(ai, '_glm_pause_key', '')


def ok():
    return 200, {'choices': [{'message': {'content': '{"ok":true}'}}]}


def test_flashx_first_and_actual_model_recorded(monkeypatch):
    bodies = []
    monkeypatch.setattr(ai, '_http_post', lambda url, key, body, timeout: bodies.append(body) or ok())
    assert ai._preferred_model('flash') == ai._glm_flashx
    assert ai.complete_role('flash', 's', 'u')[1] == ai._glm_flashx
    assert [b['model'] for b in bodies] == [ai._glm_flashx]
    assert ai.complete_role('pro', 's', 'u', ['must-not-be-read.png'])[1] == ai._glm_pro
    assert bodies[-1]['messages'][1]['content'] == 'u'


@pytest.mark.parametrize('status,code', [(429, '1113'), (429, 1113), (402, '')])
def test_balance_failure_switches_with_same_prompt_and_images(tmp_path, monkeypatch, status, code):
    image = tmp_path / 'diagram.png'
    image.write_bytes(b'fixture')
    bodies = []
    clock = [1.0]
    monkeypatch.setattr(ai.time, 'monotonic', lambda: clock[0])
    def post(url, key, body, timeout):
        bodies.append(body)
        if body['model'] == ai._glm_flashx:
            return status, {'error': {'code': code, 'message': 'balance exhausted'}}
        return ok()
    monkeypatch.setattr(ai, '_http_post', post)
    text, model = ai.complete_role('flash', 'same system', 'same question', [str(image)])
    assert json.loads(text)['ok'] and model == ai._glm_flash
    assert [b['model'] for b in bodies] == [ai._glm_flashx, ai._glm_flash]
    assert bodies[0]['messages'] == bodies[1]['messages']
    assert len(bodies[1]['messages'][1]['content']) == 2
    assert all(b['thinking'] == {'type': 'enabled', 'clear_thinking': False} for b in bodies)
    assert not ai._glm_paused()
    assert ai._preferred_model('flash') == ai._glm_flash
    ai.complete_role('flash', 's', 'u')
    assert len(bodies) == 3 and bodies[-1]['model'] == ai._glm_flash
    clock[0] = 602.0
    ai.complete_role('flash', 's', 'u')
    assert [b['model'] for b in bodies[-2:]] == [ai._glm_flashx, ai._glm_flash]
    monkeypatch.setattr(ai, '_glm_key', 'new-key')
    assert ai._glm_role_models('flash')[0] == ai._glm_flashx
    monkeypatch.setattr(ai, '_glm_key', 'fixture-glm')
    monkeypatch.setattr(ai, '_glm_url', 'https://new-endpoint.test/chat')
    assert ai._glm_role_models('flash')[0] == ai._glm_flashx


@pytest.mark.parametrize('status,code', [(429, '1302'), (429, '1305'), (429, '1310'), (401, '1000'), (500, '1200')])
def test_other_errors_do_not_consume_flash_pack(monkeypatch, status, code):
    calls = []
    monkeypatch.setattr(ai, '_http_post', lambda url, key, body, timeout:
                        calls.append(body['model']) or (status, {'error': {'code': code}}))
    with pytest.raises(ai.ModelError):
        ai.complete_role('flash', 's', 'u')
    assert calls == [ai._glm_flashx] and not ai._glm_flashx_pauses


def test_output_budget_exhaustion_is_not_balance_exhaustion(monkeypatch):
    monkeypatch.setattr(ai, '_http_post', lambda *args: (200, {'choices': [
        {'finish_reason': 'length', 'message': {'content': ''}}]}))
    with pytest.raises(ai.ModelError) as exc:
        ai.complete_role('flash', 's', 'u')
    assert exc.value.kind == 'output_limit' and not ai._glm_flashx_pauses


def test_both_packs_exhausted_preserves_causes_and_existing_backup(monkeypatch):
    monkeypatch.setenv('CHEM_AI_PROVIDER', 'auto')
    monkeypatch.setattr(ai, '_kimi_key', '')
    monkeypatch.setattr(ai, '_ds_key', 'fixture-ds')
    models = []
    def post(url, key, body, timeout):
        models.append(body['model'])
        return 429, {'error': {'code': '1113', 'message': body['model'] + ' exhausted'}}
    monkeypatch.setattr(ai, '_http_post', post)
    monkeypatch.setattr(ai, '_deepseek_chat', lambda *args: '{"ok":true}')
    assert ai.complete_role('flash', 's', 'u')[1] == ai._ds_flash
    assert models == [ai._glm_flashx, ai._glm_flash] and ai._glm_paused()
    ai.complete_role('flash', 's', 'u')
    assert len(models) == 2
    monkeypatch.setenv('CHEM_AI_PROVIDER', 'glm')
    ai._glm_flashx_pauses.clear()
    with pytest.raises(ai.ModelError) as exc:
        ai.complete_role('flash', 's', 'u')
    assert 'FlashX' in str(exc.value) and 'Flash' in str(exc.value)
    assert exc.value.provider_code == '1113'


def test_config_supports_disabling_flashx_and_duplicate_model(monkeypatch):
    monkeypatch.setattr(ai, '_load_local_keys', lambda: None)
    for name in ('GLM_FLASH', 'GLM_FLASHX', 'GLM_PRO'):
        monkeypatch.delenv(name, raising=False)
    ai._load_provider_config()
    assert ai._glm_role_models('flash') == ['glm-5.3-flashx', 'glm-5.3-flash']
    monkeypatch.setenv('GLM_FLASHX', 'off')
    ai._load_provider_config()
    assert ai._glm_role_models('flash') == ['glm-5.3-flash']
    monkeypatch.setenv('GLM_FLASHX', 'glm-5.3-flash')
    ai._load_provider_config()
    assert ai._glm_role_models('flash') == ['glm-5.3-flash']
