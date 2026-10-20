import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, ProxyHandler, build_opener

from server import Handler, Store, ThreadingHTTPServer


class FakeAI:
    def __init__(self):
        self.calls = []

    def get_settings(self):
        return dict(base_url='', model='', key_saved=False, configured=False, warning=None)

    def save_settings(self, payload):
        self.calls.append(('save', payload))
        return self.get_settings()

    def test_connection(self, payload):
        self.calls.append(('test', payload))
        return {'ok': True, 'message': '测试连接成功'}

    def create_task(self, notice_ids, question):
        self.calls.append(('task', notice_ids, question))
        return {'task_id': 'test-task', 'status': 'queued'}

    def get_task(self, task_id):
        self.calls.append(('get', task_id))
        return {'task_id': task_id, 'status': 'succeeded', 'answer': '测试摘要', 'sources': []}


class AIHTTPTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        class QuietHandler(Handler):
            store = Store(Path(self.directory.name) / 'notices.json')
            def log_message(self, *args): pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), QuietHandler)
        self.ai = self.server.ai_service = FakeAI()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.client = build_opener(ProxyHandler({}))

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.directory.cleanup()

    def request(self, path, payload=None, origin=None, extra=None):
        body = None if payload is None else json.dumps(payload).encode()
        headers = {'Content-Type': 'application/json', 'Origin': origin or self.base}
        headers.update(extra or {})
        with self.client.open(Request(self.base + path, body, headers)) as response:
            return response.status, json.load(response)

    def test_settings_get_save_and_explicit_test_dispatch(self):
        self.assertEqual(self.request('/api/ai/settings')[1]['configured'], False)
        self.assertEqual(self.ai.calls, [])
        payload = {'base_url': 'https://example.com/v1', 'model': 'user-model', 'api_key': 'test-only'}
        status, body = self.request('/api/ai/settings', payload)
        self.assertEqual(status, 200)
        self.assertNotIn('api_key', body)
        self.assertEqual(self.ai.calls, [('save', payload)])
        self.assertTrue(self.request('/api/ai/test', payload)[1]['ok'])
        self.assertEqual(self.ai.calls[-1], ('test', payload))

    def test_create_task_202_and_poll(self):
        status, body = self.request('/api/ai/tasks', {'notice_ids': ['com:one'], 'question': '截止时间？'})
        self.assertEqual(status, 202)
        self.assertEqual(body['task_id'], 'test-task')
        self.assertEqual(self.ai.calls[-1], ('task', ['com:one'], '截止时间？'))
        self.assertEqual(self.request('/api/ai/tasks/test-task')[1]['answer'], '测试摘要')

    def test_post_origin_content_type_and_json_validation(self):
        for path in ('/api/ai/settings', '/api/ai/test', '/api/ai/tasks'):
            for payload, origin, extra, code in [({}, 'https://external.example', None, 403),
                                                ({}, None, {'Content-Type': 'text/plain'}, 403),
                                                ([], None, None, 400)]:
                with self.subTest(path=path, code=code):
                    with self.assertRaises(HTTPError) as caught:
                        self.request(path, payload, origin, extra)
                    self.assertEqual(caught.exception.code, code)
        self.assertEqual(self.ai.calls, [])

    def test_oversized_input_unknown_routes_and_host_rejected(self):
        with self.assertRaises(HTTPError) as caught:
            self.request('/api/ai/settings', {'api_key': 'x' * 70000})
        self.assertEqual(caught.exception.code, 400)
        with self.assertRaises(HTTPError) as caught:
            self.request('/api/ai/not-found', {})
        self.assertEqual(caught.exception.code, 404)
        with self.assertRaises(HTTPError) as caught:
            self.request('/api/ai/settings', extra={'Host': 'external.example'})
        self.assertEqual(caught.exception.code, 403)


if __name__ == '__main__': unittest.main()
