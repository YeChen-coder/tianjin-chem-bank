from copy import deepcopy
import json
import threading
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen

from PIL import Image
import bot_bridge
from question_content.bank_bridge import BankBridge
from question_content.http_api import ContentAPI
import test_workflow_integration_v2 as fixtures


class StructuredBotTests(unittest.TestCase):
    setUp = fixtures.WorkflowTests.setUp
    tearDown = fixtures.WorkflowTests.tearDown

    def prepare(self):
        image = self.root / 'diagram.png'
        Image.new('RGB', (4, 3), 'white').save(image)
        asset = self.store.register_asset(image)
        q = deepcopy(self.question)
        q['body'][0]['inlines'].append({'id': 'bot-formula', 'type': 'formula',
                                      'latex': r'\mathrm{Cu}(\mathrm{Fe})',
                                      'status': 'restored', 'source_ref': None})
        q['options'] = [{'id': 'bot-option', 'label': 'A', 'blocks': [
            {'id': 'bot-option-paragraph', 'type': 'paragraph', 'align': 'left', 'inlines': [
                {'id': 'bot-image', 'type': 'image', 'asset_id': asset['id'],
                 'width_mm': 40.0, 'height_mm': 30.0}]}]}]
        self.store.save_question(q['id'], 0, q)
        api = ContentAPI(self.store.workspace, fixtures.ROOT)
        api._store = self.store
        api._workflow = self.workflow
        bridge = BankBridge(api)
        api._bank = bridge
        bridge.synchronize()
        return api, bridge, self.workflow._legacy_id(q['id']), asset

    def plan(self, con, bridge, lid, request='bot-picture-test', dry_run=False):
        return {'request_id': request, 'bank_revision': bot_bridge.bank_revision(con, bridge),
                'name': '带图组卷', 'question_ids': [lid], 'dry_run': dry_run}

    def test_search_returns_authoritative_formula_and_option_image(self):
        api, bridge, lid, asset = self.prepare()
        with self.workflow.legacy() as con:
            self.assertEqual(con.execute('SELECT segments FROM questions WHERE id=?', (lid,)).fetchone()[0], '[]')
            result = bot_bridge.search(con, {'has_images': True}, bridge)
        question = next(item for item in result['items'] if item['id'] == lid)
        self.assertEqual(question['image_count'], 1)
        self.assertTrue(question['images'][0]['available'])
        self.assertEqual(question['images'][0]['asset_id'], asset['id'])
        self.assertTrue(question['images'][0]['path'].startswith('/api/v2/content/assets/'))
        self.assertEqual(question['structured']['question']['options'][0]['blocks'][0]['inlines'][0]['asset_id'], asset['id'])
        self.assertEqual(question['structured']['question']['body'][0]['inlines'][-1]['latex'], r'\mathrm{Cu}(\mathrm{Fe})')
        self.assertFalse(any('缺失' in warning for warning in question['warnings']))

    def test_picture_paper_dry_run_and_retry_preserve_order(self):
        api, bridge, lid, asset = self.prepare()
        with self.workflow.legacy() as con:
            before = con.execute('SELECT COUNT(*) FROM papers').fetchone()[0]
            dry = self.plan(con, bridge, lid, dry_run=True)
            self.assertTrue(bot_bridge.create_paper(con, dry, bridge)['dry_run'])
            self.assertEqual(con.execute('SELECT COUNT(*) FROM papers').fetchone()[0], before)
            payload = self.plan(con, bridge, lid)
            first = bot_bridge.create_paper(con, payload, bridge)
            retry = bot_bridge.create_paper(con, payload, bridge)
            self.assertEqual(first['paper']['ids'], [lid])
            self.assertEqual(first['paper']['id'], retry['paper']['id'])
            self.assertTrue(retry['duplicate'])
            self.assertEqual(con.execute('SELECT COUNT(*) FROM papers').fetchone()[0], before + 1)

    def test_missing_or_corrupt_original_still_blocks_paper(self):
        api, bridge, lid, asset = self.prepare()
        path = self.store.asset(asset['id'])
        original = path.read_bytes()
        for damaged in (b'corrupt image', None):
            if damaged is None:
                path.unlink()
            else:
                path.write_bytes(damaged)
            with self.workflow.legacy() as con:
                result = bot_bridge.read_questions(con, {'question_ids': [lid]}, bridge)
                self.assertFalse(result['items'][0]['images'][0]['available'])
                with self.assertRaises(bot_bridge.BridgeError):
                    bot_bridge.create_paper(con, self.plan(con, bridge, lid), bridge)
            path.write_bytes(original)

    def test_content_revision_invalidates_plan_even_without_summary_change(self):
        api, bridge, lid, asset = self.prepare()
        with self.workflow.legacy() as con:
            plan = self.plan(con, bridge, lid)
            legacy_revision = bot_bridge.bank_revision(con)
            q = self.store.get_question(self.question['id'])['question']
            q['options'][0]['blocks'][0]['inlines'][0]['width_mm'] = 55.0
            self.store.save_question(q['id'], q['revision'], q)
            self.assertEqual(bot_bridge.bank_revision(con), legacy_revision)
            self.assertNotEqual(bot_bridge.bank_revision(con, bridge), plan['bank_revision'])
            with self.assertRaises(bot_bridge.BridgeError) as error:
                bot_bridge.create_paper(con, plan, bridge)
            self.assertEqual(error.exception.status, 409)

    def test_legacy_read_remains_available_without_content_bridge(self):
        api, bridge, lid, asset = self.prepare()
        other = self.workflow._legacy_id(self.store.snapshot(self.pid)['questions'][1]['id'])
        with self.workflow.legacy() as con:
            result = bot_bridge.read_questions(con, {'question_ids': [other]})
        self.assertEqual(result['items'][0]['id'], other)
        self.assertNotIn('structured', result['items'][0])

    def test_http_bot_routes_use_v2_content_and_real_image_endpoint(self):
        import app
        api, bridge, lid, asset = self.prepare()
        with patch.object(app, 'CONTENT_API', api):
            server = ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f'http://127.0.0.1:{server.server_port}'
            def request(path, payload=None):
                req = Request(base + path, data=json.dumps(payload).encode() if payload is not None else None,
                              headers={'Content-Type': 'application/json'})
                with urlopen(req) as response:
                    return json.loads(response.read())
            try:
                self.assertEqual(request('/api/bot/info')['question_count'], 2)
                result = request('/api/bot/questions', {'question_ids': [lid]})
                item = result['items'][0]
                with urlopen(base + item['images'][0]['path']) as response:
                    self.assertEqual(response.status, 200)
                    self.assertTrue(response.read().startswith(b'\x89PNG'))
                payload = {'request_id': 'http-bot-test', 'name': 'HTTP 带图卷',
                           'bank_revision': result['bank_revision'], 'question_ids': [lid], 'dry_run': True}
                self.assertTrue(request('/api/bot/papers', payload)['dry_run'])
            finally:
                server.shutdown()
                thread.join()
                server.server_close()


if __name__ == '__main__':
    unittest.main()
