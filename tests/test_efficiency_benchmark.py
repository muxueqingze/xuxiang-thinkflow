"""Offline contract checks; upstreams are loopback fixtures, never paid providers."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bench.efficiency_20261011.meter import Meter, normalize_anthropic_usage
from bench.efficiency_20261011.observations import claude_metrics, thinkflow_metrics, test_command
from bench.efficiency_20261011.run import configuration, prepare_baseline, claude_executable


class OfflineUpstream:
    def __init__(self, chunks, status=200):
        self.chunks, self.status, self.requests = chunks, status, []

    def __enter__(self):
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                fixture.requests.append({'path': self.path, 'body': body, 'headers': dict(self.headers)})
                self.send_response(fixture.status)
                self.send_header('Content-Type', 'text/event-stream')
                self.end_headers()
                chunks = fixture.chunks(body) if callable(fixture.chunks) else fixture.chunks
                for chunk in chunks:
                    self.wfile.write(chunk)
                    self.wfile.flush()

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()


def sse(value):
    event = 'event: ' + value['type'] + '\n' if value.get('type') else ''
    return (event + 'data: ' + json.dumps(value) + '\n\n').encode()


class EfficiencyBenchmarkTests(unittest.TestCase):
    def test_anthropic_usage_does_not_double_count_cache(self):
        usage = normalize_anthropic_usage({'input_tokens': 12, 'cache_read_input_tokens': 30,
                                          'cache_creation_input_tokens': 4, 'output_tokens': 8})
        self.assertEqual(usage['prompt_tokens'], 46)
        self.assertEqual(usage['prompt_cache_hit_tokens'], 30)
        self.assertEqual(usage['completion_tokens'], 8)

    def test_anthropic_stream_transparent_messages_usage_and_sdk_cap_removal(self):
        chunks = [sse({'type': 'message_start', 'message': {'model': 'deepseek-flash',
                    'usage': {'input_tokens': 12, 'cache_read_input_tokens': 30,
                              'cache_creation_input_tokens': 4, 'output_tokens': 0}}}),
                  sse({'type': 'message_delta', 'delta': {'stop_reason': 'end_turn'}, 'usage': {'output_tokens': 8}}),
                  sse({'type': 'message_stop'})]
        with tempfile.TemporaryDirectory() as directory, OfflineUpstream(chunks) as upstream:
            destination = Path(directory) / 'meter.json'
            meter = Meter('fake-provider-secret', destination, protocol='anthropic', upstream=upstream.url)
            endpoint = meter.start()
            messages = [{'role': 'user', 'content': 'fixed task'}]
            system = [{'type': 'text', 'text': 'native harness system'}]
            try:
                response = httpx.post(endpoint + '/v1/messages', headers={'x-api-key': meter.token},
                    json={'model': 'sonnet', 'system': system, 'messages': messages, 'stream': True,
                          'max_tokens': 32000, 'thinking': {'type': 'adaptive'},
                          'output_config': {'effort': 'medium'}}, trust_env=False)
                self.assertEqual(response.content, b''.join(chunks))
            finally:
                totals = meter.finish()
            body = upstream.requests[0]['body']
            self.assertEqual(body['messages'], messages)
            self.assertEqual(body['system'], system)
            self.assertNotIn('max_tokens', body)
            self.assertEqual(body['thinking'], {'type': 'enabled'})
            self.assertEqual(body['output_config'], {'effort': 'high'})
            raw = json.loads(destination.read_text())
            record = raw['requests'][0]
            self.assertEqual(record['incoming_generation']['max_tokens'], 32000)
            self.assertNotIn('max_tokens', record['forwarded_generation'])
            self.assertEqual(record['system_sha256'], record['forwarded_system_sha256'])
            self.assertEqual(record['messages_sha256'], record['forwarded_messages_sha256'])
            self.assertEqual(totals['total_tokens'], 54)
            self.assertEqual(totals['cached_tokens'], 30)
            self.assertEqual(totals['uncached_tokens'], 16)
            self.assertTrue(totals['usage_complete'])
            self.assertTrue(totals['cache_usage_complete'])
            self.assertNotIn('fake-provider-secret', destination.read_text())
            self.assertNotIn('native harness system', destination.read_text())

    def test_openai_fixture_keeps_prompt_and_collects_tail_usage(self):
        chunks = [sse({'choices': [{'delta': {'reasoning_content': 'private reasoning body'}}]}),
                  sse({'choices': [{'finish_reason': 'stop', 'delta': {}}]}),
                  sse({'usage': {'prompt_tokens': 22, 'completion_tokens': 17, 'prompt_cache_hit_tokens': 9}, 'choices': []})]
        with tempfile.TemporaryDirectory() as directory, OfflineUpstream(chunks) as upstream:
            meter = Meter('fake-key', Path(directory) / 'meter.json', upstream=upstream.url)
            endpoint = meter.start()
            messages = [{'role': 'system', 'content': 'fixed system'}, {'role': 'user', 'content': 'task'}]
            try:
                response = httpx.post(endpoint + '/chat/completions', headers={'Authorization': 'Bearer ' + meter.token},
                                      json={'messages': messages, 'stream': True, 'model': 'deepseek-flash'}, trust_env=False)
                self.assertEqual(response.content, b''.join(chunks))
            finally:
                totals = meter.finish()
            body = upstream.requests[0]['body']
            self.assertEqual(body['messages'], messages)
            self.assertNotIn('max_tokens', body)
            self.assertEqual(body['stream_options'], {'include_usage': True})
            self.assertEqual(totals['total_tokens'], 39)
            self.assertEqual(totals['api_requests'], 1)
            self.assertNotIn('private reasoning body', (Path(directory) / 'meter.json').read_text())

    def test_missing_cache_fields_are_explicitly_incomplete(self):
        with tempfile.TemporaryDirectory() as directory:
            meter = Meter('fake', Path(directory) / 'm.json', protocol='anthropic')
            record = {'finished': True, 'http_status': 200}
            meter.observe({'type': 'message_start', 'message': {'usage': {'input_tokens': 12, 'output_tokens': 0}}}, record)
            meter.observe({'type': 'message_delta', 'usage': {'output_tokens': 9}}, record)
            meter.requests.append(record)
            self.assertFalse(meter.totals()['cache_usage_complete'])
            self.assertFalse(meter.totals()['usage_complete'])
            self.assertTrue(meter.totals()['totals_are_partial'])
            self.assertEqual(meter.totals()['cached_tokens'], 0)

    def test_failed_provider_request_retained(self):
        with tempfile.TemporaryDirectory() as directory, OfflineUpstream([b'{"error":{"type":"rate_limit"}}'], status=429) as upstream:
            meter = Meter('fake', Path(directory) / 'm.json', upstream=upstream.url)
            endpoint = meter.start()
            try:
                result = httpx.post(endpoint + '/chat/completions', headers={'Authorization': 'Bearer ' + meter.token},
                    json={'messages': [], 'stream': False}, trust_env=False)
                self.assertEqual(result.status_code, 429)
            finally:
                totals = meter.finish()
            self.assertEqual(totals['failed_requests'], 1)
            self.assertEqual(totals['unknown_usage_requests'], 1)
            self.assertFalse(totals['usage_complete'])

    def test_claude_tool_result_distinguishes_failures_and_successful_tests(self):
        events = [
            {'type': 'assistant', 'message': {'id': 'a1', 'content': [
                {'type': 'tool_use', 'id': 't1', 'name': 'Bash', 'input': {'command': 'python public_tests.py'}}]}},
            {'type': 'user', 'message': {'content': [{'type': 'tool_result', 'tool_use_id': 't1', 'content': 'FAILED', 'is_error': True}]}},
            {'type': 'assistant', 'message': {'id': 'a2', 'content': [
                {'type': 'tool_use', 'id': 't2', 'name': 'Bash', 'input': {'command': 'python public_tests.py'}}]}},
            {'type': 'user', 'message': {'content': [{'type': 'tool_result', 'tool_use_id': 't2', 'content': 'Ran 1 test\nOK'}]},
             'tool_use_result': {'stdout': '', 'stderr': 'Ran 1 test\nOK'}},
            {'type': 'result', 'num_turns': 2, 'is_error': False, 'subtype': 'success'}]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'stdout.jsonl'
            path.write_text('\n'.join(json.dumps(e) for e in events))
            metrics = claude_metrics(path)
        self.assertEqual(metrics['tool_failures'], 1)
        self.assertEqual(metrics['active_test_successes'], 1)
        self.assertEqual(metrics['harness_turns'], 2)
        self.assertEqual(metrics['terminal_result']['num_turns'], 2)

    def test_exit_zero_without_test_evidence_does_not_claim_test_success(self):
        self.assertFalse(test_command('echo public_tests.py'))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'stdout.jsonl'
            path.write_text(json.dumps({'type': 'result', 'is_error': False, 'num_turns': 1}))
            self.assertEqual(claude_metrics(path)['active_test_successes'], 0)

    def test_thinkflow_success_requires_actual_exit_code_and_stream_timeline(self):
        raw = {'events': [{'type': 'stream_started', 'turn': 1, 'seconds': 0},
            {'type': 'tool_started', 'id': 'b', 'tool': 'bash', 'cmd': 'python public_tests.py'},
            {'type': 'tool_completed', 'id': 'b', 'tool': 'bash', 'success': True},
            {'type': 'tool_completed', 'id': 'w', 'tool': 'write', 'success': True, 'flow': 'delayed', 'turn': 1, 'seconds': 1},
            {'type': 'stream_finished', 'turn': 1, 'seconds': 2}],
            'tool_records': [{'id': 'b', 'exit_code': 1}]}
        metrics = thinkflow_metrics(raw)
        self.assertEqual(metrics['active_test_successes'], 0)
        self.assertEqual(metrics['writes_before_stream_finished'], 1)

    def test_verify_tests_need_success_receipt_and_preserve_file_version_evidence(self):
        raw = {'events': [
            {'type': 'tool_started', 'id': 'v1', 'tool': 'verify', 'input_summary': '{"cmd":"python public_tests.py","paths":["router.py"]}'},
            {'type': 'tool_completed', 'id': 'v1', 'tool': 'verify', 'success': True},
            {'type': 'tool_started', 'id': 'v2', 'tool': 'verify', 'input_summary': '{"cmd":"python public_tests.py","paths":["router.py"]}'},
            {'type': 'tool_completed', 'id': 'v2', 'tool': 'verify', 'success': False}],
            'tool_records': [
                {'id': 'v1', 'status': 'success', 'exit_code': 0, 'stderr': 'Ran 1 test\nOK', 'verified_revisions': {'router.py': 'sha256:revision'}},
                {'id': 'v2', 'status': 'failed', 'exit_code': 0, 'stderr': 'Ran 1 test\nOK', 'verified_revisions': {}}]}
        metrics = thinkflow_metrics(raw)
        self.assertEqual(metrics['active_test_successes'], 1)
        self.assertEqual(metrics['active_tests'][0]['verified_revisions'], {'router.py': 'sha256:revision'})
        self.assertFalse(metrics['active_tests'][1]['success'])

    def test_claude_profile_has_only_local_token_and_real_cli_flags(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'personal-secret', 'DEEPSEEK_API_KEY': 'provider-secret'}):
            command, env = configuration('claude', Path(directory), 'http://127.0.0.1:4567', 'local-random',
                                         {'claude': {'path': 'real-installed-claude.exe'}})
            self.assertEqual(env['ANTHROPIC_API_KEY'], 'local-random')
            self.assertNotIn('DEEPSEEK_API_KEY', env)
            self.assertEqual(command[command.index('--setting-sources') + 1], '')
            self.assertNotIn('--system-prompt', command)
            self.assertNotIn('--bare', command)
            self.assertNotIn('--max-turns', command)
            self.assertNotIn('--max-budget-usd', command)
            self.assertTrue(str(env['CLAUDE_CONFIG_DIR']).startswith(directory))

    def test_both_thinkflow_source_trees_build_uncapped_high_requests_without_model_call(self):
        baseline, _ = prepare_baseline()
        for source in (ROOT, baseline):
            with self.subTest(source=str(source)), tempfile.TemporaryDirectory() as directory:
                destination = Path(directory)
                env = {**os.environ, 'BENCH_SOURCE': str(source), 'BENCH_URL': 'http://127.0.0.1:1/v1',
                       'BENCH_API_KEY': 'local-only', 'BENCH_RECORD': str(destination / 'harness.json'),
                       'THINKFLOW_HOME': str(destination / 'thinkflow'), 'PYTHONDONTWRITEBYTECODE': '1'}
                result = subprocess.run([sys.executable, '-B', str(ROOT / 'bench/efficiency_20261011/thinkflow_worker.py'), '--preflight'],
                                         env=env, cwd=destination, capture_output=True, text=True, encoding='utf8', timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
                values = json.loads(result.stdout)
                self.assertEqual(values['output_limits'], {})
                self.assertEqual(values['thinking'], {'type': 'enabled'})
                self.assertEqual(values['effort'], 'high')
                self.assertIsNone(values['max_run_turns'])
                self.assertIsNone(values['max_run_seconds'])
                self.assertIsNone(values['max_auto_continues'])
                self.assertFalse(values['compaction_enabled'])

    def test_installed_claude_executes_tool_with_only_loopback_mock_provider(self):
        try:
            cli = claude_executable()
        except RuntimeError:
            self.skipTest('Installed native Claude CLI not available for local integration fixture')

        def response(body):
            completed = any(block.get('type') == 'tool_result'
                for message in body.get('messages', [])
                for block in message.get('content', []) if isinstance(message.get('content'), list))
            start = {'type': 'message_start', 'message': {'id': 'msg_second' if completed else 'msg_first',
                'type': 'message', 'role': 'assistant', 'model': 'deepseek-flash', 'content': [],
                'stop_reason': None, 'stop_sequence': None, 'usage': {'input_tokens': 20, 'output_tokens': 0,
                    'cache_read_input_tokens': 0, 'cache_creation_input_tokens': 0}}}
            if completed:
                middle = [{'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}},
                          {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': 'Verified.'}}]
            else:
                middle = [{'type': 'content_block_start', 'index': 0,
                           'content_block': {'type': 'tool_use', 'id': 'tool_public', 'name': 'Bash', 'input': {}}},
                          {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'input_json_delta',
                            'partial_json': json.dumps({'command': 'python public_tests.py', 'description': 'Run public tests'})}}]
            return [sse(start), *(sse(value) for value in middle),
                    sse({'type': 'content_block_stop', 'index': 0}),
                    sse({'type': 'message_delta', 'delta': {'stop_reason': 'end_turn' if completed else 'tool_use',
                                                          'stop_sequence': None}, 'usage': {'output_tokens': 10}}),
                    sse({'type': 'message_stop'})]

        with tempfile.TemporaryDirectory() as directory, OfflineUpstream(response) as upstream:
            destination = Path(directory)
            workspace = destination / 'workspace'
            workspace.mkdir()
            (workspace / 'public_tests.py').write_text("import unittest\nclass Test(unittest.TestCase):\n def test_one(self): self.assertEqual(1+1,2)\nunittest.main()\n")
            meter = Meter('fake-upstream-only', destination / 'meter.json', protocol='anthropic', upstream=upstream.url)
            endpoint = meter.start()
            command, env = configuration('claude', destination, endpoint, meter.token, {'claude': {'path': str(cli)}})
            try:
                result = subprocess.run(command, input='Run python public_tests.py, then finish.', env=env,
                                        cwd=workspace, capture_output=True, text=True, encoding='utf8', timeout=40)
            finally:
                totals = meter.finish()
            self.assertEqual(result.returncode, 0, result.stderr + '\n' + result.stdout + '\n' +
                json.dumps([{'stream': r['body'].get('stream'), 'message_count': len(r['body'].get('messages', []))}
                            for r in upstream.requests]))
            path = destination / 'stdout.jsonl'
            path.write_text(result.stdout, encoding='utf8')
            metrics = claude_metrics(path)
            self.assertEqual(metrics['tool_calls'], 1)
            self.assertEqual(metrics['active_test_successes'], 1)
            self.assertEqual(totals['api_requests'], 2)
            self.assertTrue(totals['usage_complete'])
            self.assertTrue(all('max_tokens' not in request['body'] for request in upstream.requests))
            self.assertTrue(all(request['body'].get('system') for request in upstream.requests))


if __name__ == '__main__':
    unittest.main()
