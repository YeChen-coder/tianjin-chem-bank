import json
import sys
from pathlib import Path
from xml.etree import ElementTree as ET
import zipfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import aianswers as aa
import aivariant as ai
import banklib as b
from test_import import document, p
from test_api import service


@pytest.fixture
def bank(tmp_path, monkeypatch):
    for name, value in {'DB_PATH': str(tmp_path/'bank.sqlite'), 'DATA_DIR': str(tmp_path),
                        'MEDIA': str(tmp_path/'media'), 'MEDIA_ORIG': str(tmp_path/'media/original'),
                        'IMPORT_DIR': str(tmp_path/'imports')}.items():
        monkeypatch.setattr(b, name, value)
    monkeypatch.delenv('CHEM_DISABLE_AI', raising=False)
    con = b.init_db()
    qs = b.questions_from_docx(document(tmp_path, p('一、简答题') + p('1．（1）写出水的化学式。（2）写出空气中含量最多的气体。') + p('2．说明铁生锈的条件。')))
    b.insert_questions(con, 'fixture.docx', qs)
    con.commit()
    paper, err = b.apply_paper(con, {'name': '验证卷', 'ids': [1, 2]})
    assert not err
    yield con, paper['id']
    con.close()


def responses(monkeypatch, disagree=True, judge_error=False, missing=False, bad_judge=False):
    calls = []
    answers = [('H2O', '氮气'), ('H₂O', '氧气' if disagree else '氮气'), ('H2O', '氮气')]
    plan = [{'id': 'p1', 'label': '（1）', 'prompt': '写出水的化学式'},
            {'id': 'p2', 'label': '（2）', 'prompt': '写出空气中含量最多的气体'}]
    def complete(role, system, user_text, image_paths=None):
        request = json.loads(user_text)
        calls.append((system, request, image_paths, role))
        if system == aa.PLAN:
            return json.dumps({'parts': plan}, ensure_ascii=False), 'planner'
        if system == aa.SOLVE:
            assert 'solutions' not in request and 'answer' not in request and 'previous' not in request
            index = sum(s == aa.SOLVE for s, _, _, _ in calls) - 1
            value = {'coverage_complete': not missing, 'missing_parts': [{'label': '（3）', 'prompt': '说明实验现象'}] if missing else [],
                     'parts': [dict(id=part['id'], answer=answers[index][i], reason='可检查的依据', conditions_sufficient=True, image_clear=True)
                               for i, part in enumerate(plan)]}
            return json.dumps(value, ensure_ascii=False), 'solver-' + str(index)
        assert system == aa.JUDGE
        if judge_error:
            raise ai.ModelError('http', '独立核对服务暂时不可用')
        return json.dumps({'coverage_complete': True, 'missing_parts': [], 'parts': [
            {'id': part['id'], 'equivalent': not (disagree and i == 1), 'verified': True,
             'candidate_checks': [{'attempt': s['attempt'], 'correct': True} for s in request['solutions']],
             'answer': answers[0][i], 'explanation': '可检查的依据',
             'reason': '仅排版不同，语义一致' if i == 0 else '有分歧' if disagree else '三次均正确'}
            for i, part in enumerate(plan)]} if not bad_judge else {'parts': []}, ensure_ascii=False), 'judge'
    monkeypatch.setattr(ai, 'complete_role', complete)
    return calls


def run_first(con, paper_id):
    result, err = aa.enqueue(con, paper_id, 1)
    assert not err
    jid = result['jobs'][0]['id']
    assert aa._claim(con) == jid
    aa.run_job(con, jid)
    return jid, aa.state(con, 1)


def test_only_missing_answers_enqueued_and_idempotent(bank):
    con, paper_id = bank
    con.execute('UPDATE questions SET answer=? WHERE id=2', ('铁和氧气、水同时接触',)); con.commit()
    result, err = aa.enqueue(con, paper_id)
    assert not err and result['skipped'] == 1 and len(result['jobs']) == 1
    second, err = aa.enqueue(con, paper_id)
    assert not err and second['jobs'][0]['duplicate']
    assert con.execute('SELECT COUNT(*) FROM answer_jobs').fetchone()[0] == 1
    assert con.execute('SELECT answer FROM questions WHERE id=2').fetchone()[0] == '铁和氧气、水同时接触'


def test_image_only_existing_answer_skipped(bank):
    con, pid = bank
    con.execute('UPDATE question_metadata SET payload=? WHERE question_id=1',
                (json.dumps({'answer_segments': [[{'t': 'img', 'src': '/media/' + 'a'*64 + '.png'}]]}),)); con.commit()
    result, err = aa.enqueue(con, pid, 1)
    assert not err and not result['jobs'] and result['skipped'] == 1


def test_three_blind_solutions_semantic_equivalence_and_local_disagreement(bank, monkeypatch):
    con, pid = bank
    calls = responses(monkeypatch)
    jid, state = run_first(con, pid)
    assert len(calls) == 5
    assert [p['status'] for p in state['parts']] == ['CONFIRMED', 'SUSPECT']
    assert 'H2O' in state['answer'] and '氧气' not in state['answer'] and '氮气' not in state['answer']
    assert [c['answer'] for c in state['parts'][1]['candidates']] == ['氮气', '氧气', '氮气']
    assert con.execute('SELECT COUNT(*) FROM questions').fetchone()[0] == 2
    assert con.execute('SELECT status FROM answer_jobs WHERE id=?', (jid,)).fetchone()[0] == 'done'
    result, err = aa.enqueue(con, pid, 1)
    assert not err and result['skipped'] == 1


@pytest.mark.parametrize('bad', ['judge_error', 'bad_judge'])
def test_failed_judge_never_publishes_possible_answers(bank, monkeypatch, bad):
    con, pid = bank
    responses(monkeypatch, disagree=False, **{bad: True})
    _, state = run_first(con, pid)
    assert not state['answer']
    assert all(part['status'] == 'SUSPECT' for part in state['parts'])
    assert all(len(part['candidates']) == 3 for part in state['parts'])


def test_failed_solution_keeps_evidence_but_no_consensus(bank, monkeypatch):
    con, pid = bank
    responses(monkeypatch, disagree=False)
    original = ai.complete_role
    n = [0]
    def fail_one(role, system, text, images=None):
        if system == aa.SOLVE:
            n[0] += 1
            if n[0] == 3:
                raise ai.ModelError('http', '第三次未完成')
        return original(role, system, text, images)
    monkeypatch.setattr(ai, 'complete_role', fail_one)
    _, state = run_first(con, pid)
    assert state['answer'] == '' and all(len(p['candidates']) == 2 for p in state['parts'])


def test_missing_subquestion_only_marks_that_subquestion(bank, monkeypatch):
    con, pid = bank
    responses(monkeypatch, disagree=False, missing=True)
    _, state = run_first(con, pid)
    assert [p['status'] for p in state['parts']] == ['CONFIRMED', 'CONFIRMED', 'SUSPECT']
    assert state['parts'][2]['label'] == '（3）'


def test_suspect_draft_autosaves_but_needs_teacher_confirmation(bank, monkeypatch):
    con, pid = bank
    responses(monkeypatch)
    _, state = run_first(con, pid)
    saved, err = aa.edit_answer(con, 1, {'revision': state['revision'], 'part_id': 'p2', 'answer': '氮气（教师修订）'})
    assert not err and saved['parts'][1]['draft'] == '氮气（教师修订）'
    assert saved['parts'][1]['status'] == 'SUSPECT' and '教师修订' not in saved['answer']
    confirmed, err = aa.edit_answer(con, 1, {'revision': saved['revision'], 'part_id': 'p2', 'answer': '氮气（教师修订）', 'confirm': True})
    assert not err and confirmed['parts'][1]['status'] == 'TEACHER'
    assert 'H2O' in confirmed['answer'] and '教师修订' in confirmed['answer']
    assert len(confirmed['parts'][1]['candidates']) == 3
    con.commit()
    again = b.open_db()
    assert aa.state(again, 1)['answer'] == confirmed['answer']
    again.close()


def test_confirmed_answer_edits_autosave_and_clear(bank, monkeypatch):
    con, pid = bank
    responses(monkeypatch)
    _, state = run_first(con, pid)
    edited, err = aa.edit_answer(con, 1, {'revision': state['revision'], 'part_id': 'p1', 'answer': '水：H₂O'})
    assert not err and edited['parts'][0]['status'] == 'TEACHER' and '水：H₂O' in edited['answer']
    cleared, err = aa.edit_answer(con, 1, {'revision': edited['revision'], 'part_id': 'p1', 'answer': ''})
    assert not err and cleared['parts'][0]['status'] == 'MISSING' and not cleared['answer']


def test_edit_revision_rejects_other_window_overwrite(bank):
    con, _ = bank
    state = aa.state(con, 1)
    saved, err = aa.edit_answer(con, 1, {'revision': state['revision'], 'answer': '教师先保存的答案'})
    assert not err
    result, err = aa.edit_answer(con, 1, {'revision': state['revision'], 'answer': '另一个窗口的旧答案'})
    assert result is None and '别处更新' in err and aa.state(con, 1)['answer'] == saved['answer']


@pytest.mark.parametrize('changed', ['answer', 'body'])
def test_ai_does_not_overwrite_teacher_edit_during_request(bank, monkeypatch, changed):
    con, pid = bank
    responses(monkeypatch, disagree=False)
    complete = ai.complete_role
    def edit_while_solving(role, system, text, images=None):
        if system == aa.JUDGE:
            if changed == 'answer':
                _, err = aa.edit_answer(con, 1, {'revision': aa.state(con, 1)['revision'], 'answer': '老师正在填写的答案'})
            else:
                _, err = b.assign_body(con, 1, {'body': '老师已修改题干'})
            assert not err
        return complete(role, system, text, images)
    monkeypatch.setattr(ai, 'complete_role', edit_while_solving)
    jid, state = run_first(con, pid)
    assert con.execute('SELECT status FROM answer_jobs WHERE id=?', (jid,)).fetchone()[0] == 'stale'
    assert state['answer'] == ('老师正在填写的答案' if changed == 'answer' else '')


def test_stop_discards_inflight_result(bank, monkeypatch):
    con, pid = bank
    responses(monkeypatch, disagree=False)
    complete = ai.complete_role
    def stop_during_call(role, system, text, images=None):
        result = complete(role, system, text, images)
        if system == aa.SOLVE:
            aa.stop_all(con)
        return result
    monkeypatch.setattr(ai, 'complete_role', stop_during_call)
    jid, state = run_first(con, pid)
    assert not state['answer'] and not state['parts']
    assert con.execute('SELECT status FROM answer_jobs WHERE id=?', (jid,)).fetchone()[0] == 'cancelled'


def test_export_has_confirmed_answers_and_pending_marker_not_possible_answers(bank, monkeypatch, tmp_path):
    con, pid = bank
    responses(monkeypatch)
    _, state = run_first(con, pid)
    for enabled in (False, True):
        path = tmp_path / ('answers.docx' if enabled else 'student.docx')
        b.export_docx([1], str(path), keep_answers=enabled)
        with zipfile.ZipFile(path) as archive:
            text = ''.join(ET.fromstring(archive.read('word/document.xml')).itertext())
        assert '氮气' not in text and '氧气' not in text
        assert ('H2O' in text) == enabled
        assert ('答案存疑，待教师确认' in text) == enabled


def test_stem_change_invalidates_old_confirmation_and_exports_pending(bank, monkeypatch):
    con, pid = bank
    responses(monkeypatch, disagree=False)
    _, state = run_first(con, pid)
    _, err = b.assign_body(con, 1, {'body': '新的问题，答案尚未核对'})
    assert not err
    state = aa.state(con, 1)
    assert state['stale'] and all(p['status'] == 'STALE' for p in state['parts'])
    assert 'H2O' not in aa.export_text(con, 1)
    edited, err = aa.edit_answer(con, 1, {'revision': state['revision'], 'part_id': 'p1', 'answer': '重新确认的第一问', 'confirm': True})
    assert not err and [p['status'] for p in edited['parts']] == ['TEACHER', 'STALE']


def test_retry_preserves_confirmed_teacher_parts(bank, monkeypatch):
    con, pid = bank
    responses(monkeypatch)
    _, state = run_first(con, pid)
    edited, err = aa.edit_answer(con, 1, {'revision': state['revision'], 'part_id': 'p1', 'answer': '教师修订的水答案'})
    assert not err
    responses(monkeypatch, disagree=False)
    result, err = aa.enqueue(con, pid, 1, retry=True)
    assert not err
    jid = aa._claim(con); aa.run_job(con, jid)
    state = aa.state(con, 1)
    assert state['parts'][0]['answer'] == '教师修订的水答案'
    assert state['parts'][0]['status'] == 'TEACHER' and state['parts'][1]['status'] == 'CONFIRMED'


def test_api_answer_autosave_and_bulk_disabled(service):
    status, raw = service('/api/questions/1/answer')
    assert status == 200
    initial = json.loads(raw)
    status, raw = service('/api/questions/1/answer', {'revision': initial['revision'], 'answer': '老师填写的答案'})
    assert status == 200 and json.loads(raw)['answer'] == '老师填写的答案'
    assert json.loads(service('/api/questions')[1])['items'][0]['answer_state']['parts'][0]['status'] == 'TEACHER'
    status, raw = service('/api/questions/1/answer', {'revision': initial['revision'], 'answer': '过时的修改'})
    assert status == 409
    status, raw = service('/api/papers', {'name': '试卷', 'ids': [1, 2]})
    pid = json.loads(raw)['id']
    status, raw = service('/api/papers/%s/ai-answers' % pid, {})
    assert status == 400 and '停用' in json.loads(raw)['error']
    con = b.open_db()
    assert con.execute('SELECT COUNT(*) FROM answer_jobs').fetchone()[0] == 0
    con.close()


def test_api_paper_answer_generation_includes_only_paper_missing_items(service, monkeypatch):
    monkeypatch.delenv('CHEM_DISABLE_AI', raising=False)
    state = json.loads(service('/api/questions/1/answer')[1])
    assert service('/api/questions/1/answer', {'revision': state['revision'], 'answer': '已有答案'})[0] == 200
    pid = json.loads(service('/api/papers', {'name': '补答案卷', 'ids': [1, 2]})[1])['id']
    status, raw = service('/api/papers/%s/ai-answers' % pid, {})
    result = json.loads(raw)
    assert status == 200 and result['skipped'] == 1 and result['jobs'][0]['question_id'] == 2
    states = json.loads(service('/api/papers/%s/answers' % pid)[1])
    assert states['active'] and len(states['items']) == 2
    assert service('/api/ai/stop-all', {})[0] == 200
    assert not json.loads(service('/api/papers/%s/answers' % pid)[1])['active']


def test_chain_blanks_and_consecutive_short_questions_are_individual_parts():
    parts = aa._refine_plan([{'id': 'p1', 'label': '（2）', 'prompt': 'B→____→____，原理____'}],
                           '实验题', '(2)连接顺序B→____→____，原理____。(3)说明现象____。')
    assert [p['label'] for p in parts] == ['（2）第1空', '（2）第2空', '（2）第3空', '（3）第1空']
    assert all(p['prompt'].count('【本空待答】') == 1 for p in parts)
    parts = aa._refine_plan([{'id': 'p1', 'label': '（1）', 'prompt': '用什么燃料？有什么现象？是不是化学变化？为什么？'}], '简答题')
    assert len(parts) == 4 and all(p['prompt'].count('？') == 1 for p in parts)


def test_arithmetic_and_self_contradictory_judge_cannot_pass():
    assert aa._arithmetic_issue('20g-18.4g=3.2g')
    assert aa._arithmetic_issue('15.8g×87/316=8.7g')
    assert not aa._arithmetic_issue('20g-18.4g=1.6g')
    assert not aa._arithmetic_issue('20×32/316≈2.03g')
    assert not aa._arithmetic_issue('1kg+500g=1500g')  # mixed units require semantic judging
    assert aa._judge_has_doubt('第二次计算有误，但最终结论正确')
    assert not aa._judge_has_doubt('三次答案均正确，没有科学错误，表述不同但语义等价')
    assert not aa._judge_has_doubt('其他选项归类无误，无科学性错误')
    assert aa._numeric_answer_conflict([{'answer': '3.2g'}, {'answer': '生成氧气的质量为1.6g。计算：20g-18.4g=1.6g'}], {'answer': '1.6g'})
    assert not aa._numeric_answer_conflict([{'answer': '1000mg'}, {'answer': '1g'}], {'answer': '1g'})


def test_apparatus_conflict_only_affects_its_subquestion():
    stem = '(1)名称____。(2)实验室用A装置制取气体，所选装置的连接顺序为：B→____→____。(3)说明现象____。'
    assert aa._stem_conflict(stem, {'label': '（2）第1空', 'prompt': '连接顺序____'})
    assert not aa._stem_conflict(stem, {'label': '（3）第1空', 'prompt': '说明现象____'})


def test_wrong_judge_claim_does_not_override_error_in_its_reason(bank, monkeypatch):
    con, pid = bank
    responses(monkeypatch, disagree=False)
    original = ai.complete_role
    def contradictory(role, system, text, images=None):
        raw, model = original(role, system, text, images)
        if system == aa.JUDGE:
            result = json.loads(raw)
            result['parts'][1]['reason'] = '第二次计算错误，但多数答案一致所以通过'
            raw = json.dumps(result, ensure_ascii=False)
        return raw, model
    monkeypatch.setattr(ai, 'complete_role', contradictory)
    _, state = run_first(con, pid)
    assert [p['status'] for p in state['parts']] == ['CONFIRMED', 'SUSPECT']


def test_queued_teacher_edit_stops_before_any_model_call(bank, monkeypatch):
    con, pid = bank
    calls = responses(monkeypatch, disagree=False)
    result, err = aa.enqueue(con, pid, 1)
    assert not err
    _, err = aa.edit_answer(con, 1, {'revision': aa.state(con, 1)['revision'], 'answer': '教师已补答案'})
    assert not err
    jid = aa._claim(con); aa.run_job(con, jid)
    assert not calls and aa.state(con, 1)['job']['status'] == 'stale'


def test_deleted_question_cancels_answer_job(bank):
    con, pid = bank
    result, err = aa.enqueue(con, pid, 1)
    assert not err and b.delete_question(con, 1)
    assert aa._claim(con) is None and aa.state(con, 1) is None


def test_independent_provider_preference_is_call_local_and_falls_back(monkeypatch):
    monkeypatch.delenv('CHEM_DISABLE_AI', raising=False)
    monkeypatch.setattr(ai, '_glm_key', 'fixture-glm')
    monkeypatch.setattr(ai, '_ds_key', 'fixture-deepseek')
    monkeypatch.setattr(ai, '_glm_pause_until', 0)
    calls = []
    def ds(*args): calls.append('deepseek'); return '{"ok":true}'
    def glm(*args): calls.append('glm'); return '{"ok":true}'
    monkeypatch.setattr(ai, '_deepseek_chat', ds); monkeypatch.setattr(ai, '_glm_chat', glm)
    assert ai.complete_role('pro', 's', 'u', preferred_provider='deepseek')[1] == ai._ds_pro
    assert ai.complete_role('pro', 's', 'u')[1] == ai._glm_pro
    assert calls == ['deepseek', 'glm']
    def failed(*args): calls.append('deepseek-error'); raise ai.ModelError('http', '服务暂时不可用')
    monkeypatch.setattr(ai, '_deepseek_chat', failed)
    assert ai.complete_role('pro', 's', 'u', preferred_provider='deepseek')[1] == ai._glm_pro
    assert calls[-2:] == ['deepseek-error', 'glm']
    assert ai._glm_key == 'fixture-glm' and ai._ds_key == 'fixture-deepseek'


def test_choice_annotation_is_allowed_but_ambiguous_choices_are_not():
    assert aa._choice_set('AD。A正确，B错误，C错误，D正确。') == frozenset('AD')
    assert aa._choice_set('选项B（植物油）') == frozenset('B')
    assert aa._choice_set('A、D') == frozenset('AD')
    assert aa._choice_set('B或C') is None
    assert aa._choice_set('B or C') is None
    assert aa._equation_issue('2KMnO₄→K₂MnO₄+MnO₂↑+O₂↑')
    assert not aa._equation_issue('2KMnO₄→K₂MnO₄+MnO₂+O₂↑')
