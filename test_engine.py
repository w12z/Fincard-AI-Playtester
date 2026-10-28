import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import unittest

from engine import ModelClient, SafeError, chat_endpoint, deepseek_preset, validate_endpoint


class MockProvider(BaseHTTPRequestHandler):
    content = '{}'
    status = 200
    requests = []
    finish_reason = 'stop'
    reasoning_content = None

    def log_message(self, *_):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.requests.append((self.path, body))
        self.send_response(self.status)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        if self.status == 200:
            payload = {'choices': [{'finish_reason': self.finish_reason,
                                   'message': {'content': self.content, 'reasoning_content': self.reasoning_content}}],
                       'usage': {'total_tokens': 15}}
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
        MockProvider.finish_reason = 'stop'
        MockProvider.reasoning_content = None
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

    def deepseek_model(self, **overrides):
        config = deepseek_preset({})
        config.update({'base_url': f'http://127.0.0.1:{self.server.server_port}/v1', **overrides})
        return ModelClient(config)

    def test_deepseek_preset_uses_current_official_model(self):
        preset = deepseek_preset({'game_path': '../Fincard'})
        self.assertEqual(preset['model'], 'deepseek-flash')
        self.assertEqual(preset['base_url'], 'https://api.deepseek.com')
        self.assertEqual(preset['api_key_env'], 'DEEPSEEK_API_KEY')
        self.assertEqual(preset['game_path'], '../Fincard')

    def test_official_host_auto_detection(self):
        model = ModelClient({'base_url': 'https://api.deepseek.com/v1', 'model': 'deepseek-flash'}, api_key='test-only')
        self.assertTrue(model.deepseek)
        self.assertEqual(model.endpoint, 'https://api.deepseek.com/v1/chat/completions')

    def test_deepseek_decision_parameters(self):
        model = self.deepseek_model(token_parameter='max_completion_tokens')
        MockProvider.content = '{"action":{"kind":"end_turn"},"reason":"测试","feedback":""}'
        model.decide(self.observation, [])
        body = MockProvider.requests[0][1]
        self.assertEqual(body['model'], 'deepseek-flash')
        self.assertEqual(body['thinking'], {'type': 'disabled'})
        self.assertEqual(body['response_format'], {'type': 'json_object'})
        self.assertEqual(body['max_tokens'], 1200)
        self.assertNotIn('max_completion_tokens', body)
        self.assertNotIn('reasoning_effort', body)
        self.assertFalse(body['stream'])

    def test_deepseek_thinking_parameters_and_final_answer(self):
        model = self.deepseek_model(thinking_mode='low', max_completion_tokens=8192)
        MockProvider.reasoning_content = 'not an action'
        MockProvider.content = '{"action":{"kind":"end_turn"}}'
        decision = model.decide(self.observation, [])
        self.assertEqual(decision['action'], {'kind': 'end_turn'})
        body = MockProvider.requests[0][1]
        self.assertEqual(body['thinking'], {'type': 'enabled'})
        self.assertEqual(body['reasoning_effort'], 'low')
        self.assertEqual(body['max_tokens'], 8192)

    def test_thinking_with_small_budget_does_not_send_request(self):
        model = self.deepseek_model(thinking_mode='high')
        with self.assertRaisesRegex(SafeError, '8192'):
            model.decide(self.observation, [])
        self.assertEqual(model.request_count, 0)
        self.assertEqual(MockProvider.requests, [])

    def test_truncated_output_is_never_used_even_if_json_is_valid(self):
        MockProvider.finish_reason = 'length'
        MockProvider.content = '{"action":{"kind":"end_turn"}}'
        with self.assertRaisesRegex(SafeError, '截断'):
            self.deepseek_model().decide(self.observation, [])

    def test_reasoning_only_is_not_treated_as_decision(self):
        MockProvider.content = None
        MockProvider.reasoning_content = '{"action":{"kind":"end_turn"}}'
        with self.assertRaisesRegex(SafeError, '没有最终答案'):
            self.deepseek_model().decide(self.observation, [])

    def test_empty_content_is_safe_error(self):
        MockProvider.content = ''
        with self.assertRaisesRegex(SafeError, '没有返回可读内容'):
            self.deepseek_model().decide(self.observation, [])

    def test_deepseek_report_is_text_not_json_mode(self):
        MockProvider.content = '# 测试报告'
        report = self.deepseek_model().report({}, [])
        self.assertIn('报告', report)
        self.assertNotIn('response_format', MockProvider.requests[0][1])

    def test_full_endpoint_is_not_appended_twice(self):
        model = self.deepseek_model(base_url=f'http://127.0.0.1:{self.server.server_port}/v1/chat/completions/')
        MockProvider.content = '{"action":{"kind":"end_turn"}}'
        model.decide(self.observation, [])
        self.assertEqual(MockProvider.requests[0][0], '/v1/chat/completions')
        self.assertEqual(chat_endpoint('https://api.deepseek.com'), 'https://api.deepseek.com/chat/completions')

    def test_generic_endpoint_receives_no_deepseek_fields(self):
        MockProvider.content = '{"action":{"kind":"end_turn"}}'
        self.model.decide(self.observation, [])
        body = MockProvider.requests[0][1]
        self.assertNotIn('thinking', body)
        self.assertNotIn('response_format', body)

    def test_balance_error_is_explained_without_raw_response(self):
        MockProvider.status = 402
        with self.assertRaisesRegex(SafeError, '余额不足') as result:
            self.deepseek_model().decide(self.observation, [])
        self.assertNotIn('sensitive', str(result.exception))

    def test_vision_format_keeps_supported_image_url(self):
        MockProvider.content = '{"action":{"kind":"end_turn"}}'
        self.deepseek_model().decide(self.observation, [], b'fake-image-for-wire-test')
        parts = MockProvider.requests[0][1]['messages'][-1]['content']
        self.assertEqual(parts[1]['type'], 'image_url')
        self.assertTrue(parts[1]['image_url']['url'].startswith('data:image/png;base64,'))


if __name__ == '__main__':
    unittest.main()
