import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import aivariant as ai
import aianswers as aa


@pytest.fixture(autouse=True)
def offline_config(monkeypatch):
    for name in ('CHEM_DISABLE_AI', 'CHEM_AI_PROVIDER', 'KIMI_MAX_TOKENS',
                 'KIMI_REASONING_EFFORT', 'DEEPSEEK_REASONING_EFFORT', 'DEEPSEEK_MAX_TOKENS'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(ai, '_kimi_key', 'fixture-kimi')
    monkeypatch.setattr(ai, '_ds_key', 'fixture-ds')
    monkeypatch.setattr(ai, '_glm_key', 'fixture-glm')


def test_kimi_k2_vision_and_format_retry_keep_thinking(tmp_path, monkeypatch):
    image = tmp_path / 'diagram.png'
    image.write_bytes(b'fixture')
    calls = []
    def post(provider, url, key, body):
        calls.append(body)
        if len(calls) == 1:
            return 400, {'error': {'message': 'unsupported JSON mode'}}
        return 200, {'choices': [{'finish_reason': 'stop', 'message': {'content': '{"answer":"H2O"}'}}]}
    monkeypatch.setattr(ai, '_provider_post', post)
    with ai.provider_scope('kimi'):
        _, model = ai.complete_role('flash', 'JSON', 'question', [str(image)])
    assert model == ai._kimi_flash
    assert len(calls) == 2
    assert all(c['thinking'] == {'type': 'enabled'} and c['max_tokens'] == 32768 for c in calls)
    assert all('reasoning_effort' not in c and 'temperature' not in c for c in calls)
    assert all(c['messages'][1]['content'][1]['image_url']['url'].startswith('data:image/png') for c in calls)
    assert 'response_format' in calls[0] and 'response_format' not in calls[1]


def test_kimi_k3_has_correct_budget_and_effort():
    rich, plain = ai._kimi_bodies('kimi-k3', 'JSON', 'question')
    assert rich['reasoning_effort'] == plain['reasoning_effort'] == 'high'
    assert rich['max_completion_tokens'] == 32768
    assert 'thinking' not in rich and 'max_tokens' not in rich


def test_pinned_provider_never_falls_back_even_on_bad_json(monkeypatch):
    monkeypatch.setattr(ai, '_kimi_chat', lambda *args: '{}')
    def other(*args):
        pytest.fail('Pinned benchmark must not invoke another service')
    monkeypatch.setattr(ai, '_deepseek_chat', other)
    monkeypatch.setattr(ai, '_glm_chat', other)
    with ai.provider_scope('kimi'):
        with pytest.raises(ValueError, match='作答位置'):
            aa._call(aa.PLAN, {'body': 'question'}, [], 'flash', independent=True)


def test_pinned_context_is_restored(monkeypatch):
    calls = []
    monkeypatch.setattr(ai, '_deepseek_chat', lambda *args: calls.append('ds') or '{"ok":true}')
    monkeypatch.setattr(ai, '_kimi_chat', lambda *args: calls.append('kimi') or '{"ok":true}')
    with ai.provider_scope('deepseek'):
        ai.complete_role('flash', 's', 'u')
    ai.complete_role('flash', 's', 'u')
    assert calls == ['ds', 'kimi']


def test_env_selection_pins_independent_answer_calls(monkeypatch):
    monkeypatch.setenv('CHEM_AI_PROVIDER', 'kimi')
    value = {'coverage_complete': True, 'missing_parts': [], 'parts': [
        {'id': 'p1', 'answer': 'H2O', 'reason': 'chemical formula',
         'conditions_sufficient': True, 'image_clear': True}]}
    monkeypatch.setattr(ai, '_kimi_chat', lambda *args: json.dumps(value))
    monkeypatch.setattr(ai, '_deepseek_chat', lambda *args: pytest.fail('unexpected fallback'))
    assert aa._call(aa.SOLVE, {'parts': [{'id': 'p1'}]}, [], 'flash', independent=True)[1] == ai._kimi_flash


def test_kimi_key_redacted():
    assert ai._kimi_key not in ai._safe_text('failed: ' + ai._kimi_key)


def test_provider_contexts_do_not_leak_between_concurrent_workers(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    barrier = Barrier(2)
    def call(provider):
        with ai.provider_scope(provider):
            barrier.wait(timeout=5)
            return ai.complete_role('flash', 's', 'u')[1]
    monkeypatch.setattr(ai, '_kimi_chat', lambda *args: '{"ok":true}')
    monkeypatch.setattr(ai, '_deepseek_chat', lambda *args: '{"ok":true}')
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(call, 'kimi')
        second = pool.submit(call, 'deepseek')
        assert first.result() == ai._kimi_flash
        assert second.result() == ai._ds_flash


def test_telemetry_records_actual_parameters_without_keys(monkeypatch):
    monkeypatch.setattr(ai, '_http_post', lambda *args: (200, {
        'choices': [{'finish_reason': 'stop', 'message': {'content': '{"ok":true}'}}],
        'usage': {'total_tokens': 123}}))
    calls = []
    with ai.provider_scope('kimi', calls.append):
        ai.complete_role('flash', 'JSON', 'question')
    assert calls[0]['usage']['total_tokens'] == 123
    assert calls[0]['thinking']['type'] == 'enabled'
    assert calls[0]['max_tokens'] == 32768
    assert ai._kimi_key not in json.dumps(calls)


def test_auto_independent_check_can_use_kimi_if_deepseek_fails(monkeypatch):
    monkeypatch.setattr(ai, '_glm_key', '')
    def failed(*args):
        raise ai.ModelError('http', 'fixture outage')
    monkeypatch.setattr(ai, '_deepseek_chat', failed)
    monkeypatch.setattr(ai, '_kimi_chat', lambda *args: '{"ok":true}')
    assert ai.complete_role('flash', 's', 'u', preferred_provider='deepseek')[1] == ai._kimi_flash


def test_auto_failure_keeps_kimi_cause_and_redacts_all_keys(monkeypatch):
    for name, key in [('_kimi_chat', ai._kimi_key), ('_glm_chat', ai._glm_key), ('_deepseek_chat', ai._ds_key)]:
        def fail(*args, secret=key):
            raise ai.ModelError('http', 'fixture outage ' + secret)
        monkeypatch.setattr(ai, name, fail)
    with pytest.raises(ai.ModelError) as result:
        ai.complete_role('flash', 's', 'u')
    assert 'Kimi' in str(result.value) and 'GLM' in str(result.value) and 'DeepSeek' in str(result.value)
    assert all(key not in str(result.value) for key in (ai._kimi_key, ai._glm_key, ai._ds_key))


def test_stopped_benchmark_does_not_send_more_paid_requests(monkeypatch):
    from threading import Event
    stop = Event()
    stop.set()
    monkeypatch.setattr(ai, '_deepseek_chat', lambda *args: pytest.fail('No API request after balance exhaustion'))
    with ai.provider_scope('deepseek', stop_event=stop):
        with pytest.raises(ai.ModelError, match='已暂停后续请求'):
            ai.complete_role('flash', 's', 'u')


def test_kimi_rate_backoff_keeps_thinking_and_budget(monkeypatch):
    calls = []
    delays = []
    def post(provider, url, key, body):
        calls.append(body)
        if len(calls) < 4:
            return 429, {'error': {'message': 'Organization Rate limit exceeded'}}
        return 200, {'choices': [{'message': {'content': '{"ok":true}'}}]}
    monkeypatch.setattr(ai, '_provider_post', post)
    monkeypatch.setattr(ai.time, 'sleep', delays.append)
    ai._kimi_chat('kimi-k2.6', 'JSON', 'u')
    assert delays == [1.5, 3.0, 6.0]
    assert all(c['max_tokens'] == 32768 and c['thinking']['type'] == 'enabled' for c in calls)


def test_kimi_serializes_requests_across_workers(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    entered = Event()
    release = Event()
    calls = []
    def serial(*args):
        calls.append(1)
        entered.set()
        assert release.wait(timeout=5)
        return '{"ok":true}'
    monkeypatch.setattr(ai, '_kimi_chat_serial', serial)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(ai._kimi_chat, 'kimi-k2.6', 's', 'u')
        assert entered.wait(timeout=5)
        second = pool.submit(ai._kimi_chat, 'kimi-k2.6', 's', 'u')
        assert calls == [1]
        release.set()
        assert first.result() and second.result()
    assert calls == [1, 1]


def test_cancelled_call_waiting_at_kimi_gate_never_reaches_api(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    entered = Event()
    release = Event()
    waiting = Event()
    cancel = Event()
    calls = []
    def serial(*args):
        calls.append(1)
        entered.set()
        assert release.wait(timeout=5)
        return '{"ok":true}'
    def queued_call():
        with ai.provider_scope('kimi', cancel_check=cancel.is_set):
            waiting.set()
            return ai._kimi_chat('kimi-k2.6', 's', 'u')
    monkeypatch.setattr(ai, '_kimi_chat_serial', serial)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(ai._kimi_chat, 'kimi-k2.6', 's', 'u')
        assert entered.wait(timeout=5)
        second = pool.submit(queued_call)
        assert waiting.wait(timeout=5)
        cancel.set()
        release.set()
        first.result()
        with pytest.raises(InterruptedError):
            second.result()
    assert calls == [1]


def test_cancellation_during_rate_backoff_skips_next_request(monkeypatch):
    from threading import Event
    cancel = Event()
    calls = []
    def post(*args):
        calls.append(1)
        return 429, {'error': {'message': 'rate limit'}}
    monkeypatch.setattr(ai, '_provider_post', post)
    monkeypatch.setattr(ai.time, 'sleep', lambda delay: cancel.set())
    with ai.provider_scope('kimi', cancel_check=cancel.is_set):
        with pytest.raises(InterruptedError):
            ai._kimi_chat('kimi-k2.6', 's', 'u')
    assert calls == [1]


@pytest.mark.parametrize('env,value', [('KIMI_MAX_TOKENS', '12'), ('KIMI_MAX_TOKENS', 'abc'),
                                      ('KIMI_REASONING_EFFORT', 'none')])
def test_invalid_kimi_configuration_fails_before_network(monkeypatch, env, value):
    monkeypatch.setenv(env, value)
    with pytest.raises(ai.ModelError):
        ai._kimi_bodies('kimi-k3', 's', 'u')


def test_local_config_env_wins_and_moonshot_alias(monkeypatch):
    monkeypatch.setattr(ai, '_load_local_keys', lambda: None)
    monkeypatch.delenv('KIMI_API_KEY', raising=False)
    monkeypatch.setenv('MOONSHOT_API_KEY', 'fixture-alias')
    ai._load_provider_config()
    assert ai._kimi_key == 'fixture-alias'
    monkeypatch.setenv('KIMI_API_KEY', 'fixture-primary')
    ai._load_provider_config()
    assert ai._kimi_key == 'fixture-primary'
