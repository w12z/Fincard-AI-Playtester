import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import unittest

from engine import ModelClient, SafeError, validate_endpoint


class MockProvider(BaseHTTPRequestHandler):
    content = '{}'
    status = 200
    requests = []

    def log_message(self, *_):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.requests.append((self.path, body))
        self.send_response(self.status)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        if self.status == 200:
            payload = {'choices': [{'message': {'content': self.content}}], 'usage': {'total_tokens': 15}}
        else:
            payload = {'error': 'sensitive-provider-response-not-for-display'}
        self.wfile.write(json.dumps(payload).encode())


class ModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), MockProvider)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        MockProvider.status = 200
        MockProvider.requests.clear()
        self.model = ModelClient({'base_url': f'http://127.0.0.1:{self.server.server_port}/v1', 'model': 'test'})
        self.observation = {'legal_actions': [{'kind': 'end_turn'}]}

    def test_legal_action_and_prompt(self):
        MockProvider.content = json.dumps({'action': {'kind': 'end_turn'}, 'reason': '完成', 'feedback': ''})
        decision = self.model.decide(self.observation, [])
        self.assertEqual(decision['action'], self.observation['legal_actions'][0])
        self.assertEqual(self.model.request_count, 1)
        self.assertEqual(MockProvider.requests[0][0], '/v1/chat/completions')
        self.assertIn('max_tokens', MockProvider.requests[0][1])

    def test_invented_action_is_never_applied(self):
        MockProvider.content = '{"action":{"kind":"set_budget","amount":999}}'
        with self.assertRaises(SafeError):
            self.model.decide(self.observation, [])

    def test_invalid_json(self):
        MockProvider.content = 'not json'
        with self.assertRaises(SafeError):
            self.model.decide(self.observation, [])

    def test_no_retry_no_response_leak(self):
        MockProvider.status = 429
        with self.assertRaises(SafeError) as result:
            self.model.decide(self.observation, [])
        self.assertNotIn('sensitive', str(result.exception))
        self.assertEqual(len(MockProvider.requests), 1)
        self.assertEqual(self.model.request_count, 1)

    def test_cloud_requires_https_and_no_embedded_secret(self):
        for url in ['http://example.com/v1', 'https://key:secret@example.com/v1', 'https://example.com/v1?api_key=secret']:
            with self.assertRaises(SafeError):
                validate_endpoint(url)


if __name__ == '__main__':
    unittest.main()
