import copy
import json
import unittest
from unittest.mock import patch

from ai_formula_v2 import build_request, normalize_response, generate_candidate
from ai_formula_v2.protocol import CONTENT_FIELDS
from ai_formula_v2.compatibility import legacy_paragraphs
from ai_formula_v2.answers import evaluate_answers
from question_content import validate_question


def fixture():
    """Synthetic control: nested questions, merged table, inline picture and styles."""
    serial = iter(range(1, 1000))
    def nid(): return 'mock-' + str(next(serial))
    def p(*parts): return {'id': nid(), 'type': 'paragraph', 'align': 'left', 'inlines': list(parts)}
    def text(s, **extra): return {'id': nid(), 'type': 'text', 'text': s, **extra}
    def formula(s): return {'id': nid(), 'type': 'formula', 'latex': s, 'status': 'confirmed', 'source_ref': 'original-f'}
    blank1 = {'id': nid(), 'type': 'blank', 'width_mm': 18}
    blank2 = {'id': nid(), 'type': 'blank', 'width_mm': 22}
    img = {'id': nid(), 'type': 'image', 'asset_id': 'asset-control', 'width_mm': 20, 'height_mm': 10}
    q = {'id': 'q-control', 'revision': 7, 'number': '15', 'qtype': '实验题',
         'body': [p(text('向50 mL溶液加入'), formula(r'\mathrm{NaOH}'), text('。pH比较：'), formula(r'\mathrm{pH}>7')),
                  {'id': nid(), 'type': 'table', 'columns_mm': [28, 28], 'rows': [
                      {'id': nid(), 'cells': [{'id': nid(), 'col_index': 0, 'row_span': 1, 'col_span': 2,
                                             'blocks': [p(text('合并标题'), formula(r'\mathrm{H}_{2}\mathrm{O}'))]}]},
                      {'id': nid(), 'cells': [
                          {'id': nid(), 'col_index': 0, 'row_span': 1, 'col_span': 1, 'blocks': [p(img)]},
                          {'id': nid(), 'col_index': 1, 'row_span': 1, 'col_span': 1, 'blocks': [p(formula(r'\mathrm{Fe}^{3+}'))]}]}]}],
         'options': [{'id': nid(), 'label': label, 'blocks': [p(text(label+'：选项'))]} for label in 'ABCD'],
         'subquestions': [{'id': nid(), 'label': '(1)', 'blocks': [p(text('说明'), blank1)]},
                          {'id': nid(), 'label': '(2)', 'blocks': [p(text('反应：'), formula(r'\mathrm{CaCO}_{3}\xrightarrow{\Delta}\mathrm{CaO}+\mathrm{CO}_{2}'))],
                           'subquestions': [{'id': nid(), 'label': '(2)①', 'blocks': [p(blank2)]}]}],
         'answers': [], 'explanations': [], 'source': {'source_sha256': None, 'number': '15', 'locator': 'synthetic-control'},
         'origin': 'teacher', 'issues': []}
    for target in (blank1['id'], blank2['id']):
        q['answers'].append({'id': nid(), 'target_id': target, 'blocks': [p(formula(r'\mathrm{OH}^{-}'))]})
        q['explanations'].append({'id': nid(), 'target_id': target, 'blocks': [p(text('依据'), formula(r'\frac{1}{2}'))]})
    assets = [{'id': 'asset-control', 'sha256': 'a'*64, 'mime_type': 'image/png',
               'preview_url': '/api/v2/content/assets/asset-control'}]
    response = {'schema_version': 1, 'candidate': {k: copy.deepcopy(q[k]) for k in CONTENT_FIELDS}, 'warnings': []}
    return q, assets, response


def compiler_stub(latex, source_ref=None):
    return {'ok': True, 'latex': latex, 'mathml': '<math>EXPLICIT TEST DOUBLE</math>',
            'omml': '<oMath>EXPLICIT TEST DOUBLE</oMath>', 'compiler_version': 'test-double-only', 'diagnostics': []}


def judge(status='PASS'):
    return {'status': status, 'independent_answer': '独立完整答案', 'knowledge_correct': True,
            'conditions_sufficient': True, 'answer_unique': True, 'calculation_verified': True,
            'grade_level_appropriate': True, 'image_consistent': True, 'issues': [], 'recommendation': '需教师查看'}


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.q, self.assets, self.payload = fixture()
        self.assertTrue(validate_question(self.q, self.assets)['ok'])

    def normalize(self, payload=None, compiler=compiler_stub):
        return normalize_response(payload if payload is not None else self.payload, self.q, self.assets,
                                  validate_question=validate_question, compile_formula=compiler)

    def error(self, payload, code):
        result = self.normalize(payload)
        self.assertFalse(result['ok'])
        self.assertIn(code, [i['code'] for i in result['issues']])

    def test_full_request_snapshot_and_feedback(self):
        req = build_request(self.q, self.assets, intensity='deep', teacher_feedback='保留加热条件', validate_question=validate_question)
        self.assertEqual(req['payload']['question']['body'], self.q['body'])
        self.assertEqual(req['payload']['question']['answers'], self.q['answers'])
        self.assertEqual(json.loads(req['user'])['question']['body'][0]['inlines'][1]['latex'], r'\mathrm{NaOH}')
        req['payload']['question']['body'].clear()
        self.assertTrue(self.q['body'])
        self.assertIn('保留加热条件', req['user'])

    def test_ids_targets_formula_status_and_source_are_server_owned(self):
        original = copy.deepcopy(self.q)
        result = self.normalize(json.dumps(self.payload))
        self.assertTrue(result['ok'], result['issues'])
        candidate = result['candidate']
        self.assertTrue(validate_question(candidate, self.assets)['ok'])
        self.assertEqual(candidate['origin'], 'ai')
        self.assertEqual(candidate['revision'], 0)
        self.assertNotEqual(candidate['id'], self.q['id'])
        self.assertEqual(candidate['source']['parent_revision'], 7)
        self.assertEqual(self.q, original)
        self.assertEqual(candidate['answers'][0]['target_id'], result['id_map'][self.q['answers'][0]['target_id']])
        self.assertEqual(candidate['body'][0]['inlines'][1]['status'], 'review_required')
        self.assertIsNone(candidate['body'][0]['inlines'][1]['source_ref'])
        self.assertEqual(len(result['compiled_formulas']), 9)
        self.assertFalse(result['teacher_confirmed'])

    def test_duplicate_ids(self):
        self.payload['candidate']['options'][0]['id'] = self.q['body'][0]['id']
        self.error(self.payload, 'DUPLICATE_ID')

    def test_suspended_answer_target(self):
        self.payload['candidate']['answers'][0]['target_id'] = 'ghost'
        self.error(self.payload, 'ANSWER_MAPPING_REVIEW')

    def test_answer_to_option_rejected(self):
        self.payload['candidate']['answers'][0]['target_id'] = self.q['options'][0]['id']
        self.error(self.payload, 'ANSWER_MAPPING_REVIEW')

    def test_missing_leaf_answer(self):
        self.payload['candidate']['answers'].pop()
        self.error(self.payload, 'ANSWER_COVERAGE')

    def test_duplicate_answer_target(self):
        self.payload['candidate']['answers'][1]['target_id'] = self.q['answers'][0]['target_id']
        self.error(self.payload, 'ANSWER_MAPPING_REVIEW')

    def test_missing_picture(self):
        self.payload['candidate']['body'][1]['rows'][1]['cells'][0]['blocks'][0]['inlines'][0]['asset_id'] = 'unknown'
        self.error(self.payload, 'ASSET_MISSING')

    def test_picture_ratio_cannot_be_changed(self):
        self.payload['candidate']['body'][1]['rows'][1]['cells'][0]['blocks'][0]['inlines'][0]['width_mm'] = 40
        self.error(self.payload, 'IMAGE_RATIO')

    def test_silent_option_and_subquestion_loss(self):
        for key in ('options', 'subquestions'):
            bad = copy.deepcopy(self.payload)
            bad['candidate'][key] = []
            if key == 'subquestions':
                for field in ('answers', 'explanations'):
                    bad['candidate'][field] = [copy.deepcopy(bad['candidate'][field][0])]
                    bad['candidate'][field][0]['target_id'] = self.q['id']
            self.error(bad, 'CONTENT_LOSS')

    def test_empty_answers_and_placeholders(self):
        for value in ([], legacy_paragraphs('略')):
            bad = copy.deepcopy(self.payload)
            bad['candidate']['answers'][0]['blocks'] = value
            self.error(bad, 'ANSWER_MISSING')

    def test_bad_json_and_truncation(self):
        for value in ('{"schema_version":', '```json\n{}\n```', '[]', '{"a":1,"a":2}'):
            self.assertFalse(self.normalize(value)['ok'])

    def test_latex_control_character_corruption(self):
        self.payload['candidate']['body'][0]['inlines'][1]['latex'] = '\frac{1}{2}'  # form-feed, not backslash
        self.error(self.payload, 'LATEX_ESCAPE_DAMAGED')

    def test_bad_formula_does_not_pass(self):
        result = self.normalize(compiler=lambda *args, **kwargs: {'ok': False, 'diagnostics': ['unsupported']})
        self.assertFalse(result['ok'])
        self.assertIsNotNone(result['candidate'])
        self.assertIn('FORMULA_COMPILE_FAILED', [i['code'] for i in result['issues']])

    def test_empty_fake_compile_success_is_rejected(self):
        self.assertFalse(self.normalize(compiler=lambda *args, **kwargs: {'ok': True})['ok'])

    def test_table_overlap_is_public_validation_failure(self):
        self.payload['candidate']['body'][1]['rows'][1]['cells'][1]['col_index'] = 0
        self.error(self.payload, 'CONTENT_INVALID')

    def test_ai_metadata_and_xml_cannot_enter(self):
        for key in ('revision', 'source', 'teacher_confirmed'):
            bad = copy.deepcopy(self.payload)
            bad['candidate'][key] = True
            self.error(bad, 'CANDIDATE_FIELDS')
        self.payload['candidate']['body'][0]['inlines'][1]['omml'] = '<evil/>'
        self.error(self.payload, 'NODE_FIELDS')

    def test_legacy_text_preserved_and_only_explicit_math_converted(self):
        blocks = legacy_paragraphs(r'A/B pH>7 50 mL NaOH $\mathrm{H}_{2}\mathrm{O}$')
        self.assertEqual(blocks[0]['inlines'][0]['text'], 'A/B pH>7 50 mL NaOH ')
        self.assertEqual(blocks[0]['inlines'][1]['latex'], r'\mathrm{H}_{2}\mathrm{O}')


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.q, self.assets, self.payload = fixture()
        self.calls = []
        def complete(role, system, user, images):
            self.calls.append((role, system, user, images))
            return json.dumps(self.payload if len(self.calls) == 1 else judge()), 'mock-glm-explicit'
        self.services = {'complete': complete, 'validate_question': validate_question,
                         'compile_formula': compiler_stub, 'resolve_image': lambda aid: 'registered-preview',
                         'current_revision': lambda qid: self.q['revision']}

    def test_generate_and_independent_review_use_actual_picture(self):
        original = copy.deepcopy(self.q)
        result = generate_candidate(self.q, self.assets, self.services)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['status'], 'candidate_ready')
        self.assertEqual(result['call_count'], 2)
        self.assertTrue(all(call[0] == 'flash' and call[3] for call in self.calls))
        self.assertEqual(result['content_status'], 'PASS')
        self.assertFalse(result['teacher_confirmed'])
        self.assertEqual(original, self.q)

    def test_cancelled_before_call(self):
        self.services['cancelled'] = lambda: True
        result = generate_candidate(self.q, self.assets, self.services)
        self.assertEqual(result['status'], 'cancelled')
        self.assertEqual(self.calls, [])

    def test_late_cancellation_discards_candidate(self):
        self.services['cancelled'] = lambda: len(self.calls) > 0
        result = generate_candidate(self.q, self.assets, self.services)
        self.assertEqual(result['status'], 'cancelled')
        self.assertIsNone(result['candidate'])
        self.assertEqual(len(self.calls), 1)

    def test_revision_conflict_discards_candidate(self):
        self.services['current_revision'] = lambda qid: 8 if self.calls else 7
        result = generate_candidate(self.q, self.assets, self.services)
        self.assertEqual(result['status'], 'stale')
        self.assertIsNone(result['candidate'])

    def test_missing_image_never_sends_request(self):
        del self.services['resolve_image']
        result = generate_candidate(self.q, self.assets, self.services)
        self.assertFalse(result['ok'])
        self.assertEqual(result['error']['code'], 'ASSET_MISSING')
        self.assertEqual(len(self.calls), 0)

    def test_malformed_judge_is_not_success(self):
        def complete(*args):
            self.calls.append(args)
            return (json.dumps(self.payload) if len(self.calls) == 1 else '{"status":"PASS"}'), 'mock'
        self.services['complete'] = complete
        result = generate_candidate(self.q, self.assets, self.services)
        self.assertFalse(result['ok'])
        self.assertEqual(result['status'], 'judge_error')
        self.assertIsNotNone(result['candidate'])

    def test_scientific_failure_rejects_candidate_but_preserves_inspectable_draft(self):
        def complete(*args):
            self.calls.append(args)
            verdict = judge('FAIL')
            verdict['image_consistent'] = False
            verdict['issues'] = ['仪器名称与原图不符']
            verdict['recommendation'] = '仪器名称与原图不符，不能发布'
            return json.dumps(self.payload if len(self.calls) == 1 else verdict), 'mock'
        self.services['complete'] = complete
        result = generate_candidate(self.q, self.assets, self.services)
        self.assertFalse(result['ok'])
        self.assertEqual(result['format_status'], 'passed')
        self.assertEqual(result['status'], 'candidate_rejected')
        self.assertIsNotNone(result['candidate'])
        self.assertIn('AI_JUDGE_FAIL', [i['code'] for i in result['candidate']['issues']])

    def test_service_exception_is_redacted(self):
        def broken(*args): raise RuntimeError('secret-value')
        self.services['complete'] = broken
        result = generate_candidate(self.q, self.assets, self.services)
        self.assertNotIn('secret-value', json.dumps(result))


class AnswerTests(unittest.TestCase):
    def setUp(self):
        self.q, self.assets, self.payload = fixture()
        self.original = copy.deepcopy(self.q)
        self.requests = []
        self.bad_check = False
        def complete(role, system, user, images):
            data = json.loads(user)
            self.requests.append(data)
            if len(self.requests) <= 3:
                self.assertNotIn('solutions', data)
                self.assertNotIn('answers', data['question'])
                return json.dumps({'schema_version': 1, 'answers': self.q['answers'],
                                   'explanations': self.q['explanations'], 'warnings': []}), 'mock-isolated'
            parts = [{'target_id': target, 'equivalent': True, 'verified': True,
                      'candidate_checks': [{'attempt': attempt, 'correct': not (self.bad_check and attempt == 2)} for attempt in (1, 2, 3)],
                      'reason': '明确的模拟逐项依据'} for target in data['expected_targets']]
            return json.dumps({'coverage_complete': True, 'parts': parts}), 'mock-consensus'
        self.services = {'complete': complete, 'validate_question': validate_question,
                         'compile_formula': compiler_stub, 'resolve_image': lambda _: 'registered-preview'}

    def test_isolated_solutions_and_semantic_consensus_preserve_stem_targets(self):
        result = evaluate_answers(self.q, self.assets, self.services)
        self.assertTrue(result['ok'], result)
        self.assertEqual(len(self.requests), 4)
        self.assertEqual(len(result['solutions']), 3)
        self.assertEqual(result['candidate']['body'], self.original['body'])
        self.assertEqual(result['candidate']['subquestions'], self.original['subquestions'])
        self.assertEqual([a['target_id'] for a in result['candidate']['answers']], [a['target_id'] for a in self.q['answers']])
        self.assertEqual(self.q, self.original)
        self.assertFalse(result['published'])
        self.assertFalse(result['teacher_confirmed'])

    def test_judge_cannot_confirm_failed_attempt_by_majority(self):
        self.bad_check = True
        result = evaluate_answers(self.q, self.assets, self.services)
        self.assertFalse(result['ok'])
        self.assertNotIn('candidate', result)
        self.assertTrue(all(not p['verified'] for p in result['consensus']['parts']))

    def test_answer_cancellation(self):
        self.services['cancelled'] = lambda: bool(self.requests)
        result = evaluate_answers(self.q, self.assets, self.services)
        self.assertEqual(result['status'], 'cancelled')
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.q, self.original)


class TransportTests(unittest.TestCase):
    def test_json_mode_model_usage_and_call_limit(self):
        from ai_formula_v2.glm_transport import GLMClient
        from ai_formula_v2 import ProtocolError
        observed = []
        client = GLMClient({'GLM_API_KEY': 'fake-test-secret'}, max_calls=1, observe=observed.append)
        with patch('aivariant._http_post', return_value=(200, {'choices': [{'finish_reason': 'stop',
                    'message': {'content': '{}'}}], 'usage': {'total_tokens': 3}})) as post:
            raw, model = client.complete('pro', 'json', 'question')
        self.assertEqual(raw, '{}')
        self.assertEqual(model, 'glm-5.3')
        body = post.call_args.args[2]
        self.assertEqual(body['response_format'], {'type': 'json_object'})
        self.assertEqual(client.calls[0]['usage']['total_tokens'], 3)
        self.assertNotIn('fake-test-secret', json.dumps(observed))
        with self.assertRaises(ProtocolError): client.complete('pro', 'json', 'question')

    def test_output_limit_is_not_accepted(self):
        from ai_formula_v2.glm_transport import GLMClient
        from ai_formula_v2 import ProtocolError
        client = GLMClient({'GLM_API_KEY': 'fake-test-secret'})
        with patch('aivariant._http_post', return_value=(200, {'choices': [{'finish_reason': 'length',
                    'message': {'content': '{'}}]})):
            with self.assertRaises(ProtocolError) as error: client.complete('pro', 'json', 'question')
        self.assertEqual(error.exception.code, 'GLM_OUTPUT_LIMIT')

    def test_existing_workflow_wrappers_use_same_candidate(self):
        import aivariant, aianswers
        q, assets, payload = fixture()
        responses = iter([payload, judge()])
        services = {'validate_question': validate_question, 'compile_formula': compiler_stub,
                    'resolve_image': lambda _: 'registered-preview',
                    'complete': lambda *args: (json.dumps(next(responses)), 'mock')}
        result = aivariant.generate_content_candidate(q, assets, services)
        self.assertTrue(result['ok'])
        services['complete'] = lambda *args: (json.dumps(judge()), 'mock')
        reviewed = aianswers.review_content_candidate(result['candidate'], assets, services)
        self.assertEqual(reviewed['status'], 'PASS')
        self.assertFalse(reviewed['teacher_confirmed'])
        self.assertFalse(result['teacher_confirmed'])


if __name__ == '__main__':
    unittest.main()
