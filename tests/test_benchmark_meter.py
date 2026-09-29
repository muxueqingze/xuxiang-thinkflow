"""Offline benchmark regressions: loopback fixtures only, no provider credentials."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import struct
import sys
import tempfile
import threading
import types
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock, patch

import httpx


BENCH = Path(__file__).resolve().parents[1] / 'bench/harness_benchmark_20260929'


def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, BENCH / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


meter_module = load_module('benchmark_meter_regression', 'meter.py')
adapters = load_module('benchmark_adapters_regression', 'adapters.py')
original_path = list(sys.path)
try:
    with patch.dict(sys.modules, {
        'meter': meter_module,
        'adapters': adapters,
        'scripts.live_deepseek': types.SimpleNamespace(
            load_key=Mock(side_effect=AssertionError('Credentials must never be read'))),
    }):
        runner = load_module('benchmark_runner_regression', 'run.py')
finally:
    sys.path[:] = original_path


class MemoryMeter(meter_module.Meter):
    def save(self):
        pass


@contextlib.contextmanager
def upstream_fixture(*, hold_headers=False, mode='bounded'):
    ready = threading.Event()
    release = threading.Event()
    if not hold_headers:
        release.set()
    received = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            ready.set()
            if not release.wait(3):
                return
            # Repeated usage is a snapshot, not an additional billable request.
            snapshots = [
                {'prompt_tokens': 11, 'completion_tokens': 2},
                {'prompt_tokens': 11, 'completion_tokens': 7,
                 'prompt_cache_hit_tokens': 4,
                 'completion_tokens_details': {'reasoning_tokens': 3}},
            ]
            body = ''.join('data: ' + json.dumps({'usage': usage, 'choices': []}) + '\n\n'
                           for usage in snapshots).encode() + b'data: [DONE]\n\n'
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except OSError:
                pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    meter = MemoryMeter('fixture-not-a-credential', Path('unused'), max_seconds=5, mode=mode,
                        upstream=f'http://127.0.0.1:{server.server_port}/v1')
    endpoint = meter.start()
    try:
        yield meter, endpoint, ready, release, received
    finally:
        release.set()
        meter.finish()
        server.shutdown()
        server.server_close()


class BenchmarkMeterTests(unittest.TestCase):
    def test_production_forwards_full_large_history_and_no_generation_cap(self):
        messages = [{'role':'user', 'content':'x' * 8_000_001}]
        with contextlib.redirect_stdout(io.StringIO()), upstream_fixture(mode='production') as fixture:
            meter, endpoint, _, _, received = fixture
            meter.started -= 1000
            with httpx.Client(trust_env=False, timeout=10) as client:
                response = client.post(endpoint + '/chat/completions',
                    headers={'Authorization':'Bearer ' + meter.token},
                    json={'messages':messages, 'stream':True})
            self.assertEqual(response.status_code, 200)
            meter.finish()
            self.assertEqual(received[0]['messages'], messages)
            record = meter.requests[0]
            self.assertEqual(record['incoming_output_limits'], {})
            self.assertEqual(record['forwarded_output_limits'], {})
            self.assertEqual(record['messages_sha256'], record['forwarded_messages_sha256'])

    def test_production_exceeds_legacy_request_count(self):
        with contextlib.redirect_stdout(io.StringIO()), upstream_fixture(mode='production') as fixture:
            meter, endpoint, _, _, received = fixture
            with httpx.Client(trust_env=False, timeout=5) as client:
                for _ in range(25):
                    response = client.post(endpoint + '/chat/completions',
                        headers={'Authorization':'Bearer ' + meter.token},
                        json={'messages':[], 'stream':True})
                    self.assertEqual(response.status_code, 200)
            self.assertEqual(meter.finish()['api_requests'], 25)
            self.assertEqual(len(received), 25)

    def test_production_rejects_client_caps_instead_of_hiding_them(self):
        with contextlib.redirect_stdout(io.StringIO()), upstream_fixture(mode='production') as fixture:
            meter, endpoint, _, _, received = fixture
            with httpx.Client(trust_env=False, timeout=5) as client:
                for name in ('max_tokens','max_completion_tokens','max_output_tokens'):
                    response = client.post(endpoint + '/chat/completions',
                        headers={'Authorization':'Bearer ' + meter.token},
                        json={'messages':[], 'stream':True, name:None})
                    self.assertEqual(response.status_code, 400)
            self.assertEqual(received, [])
            self.assertTrue(all(r['transport_error']=='ClientOutputLimitPresent' for r in meter.requests))

    def test_every_request_counts_final_usage_once(self):
        with contextlib.redirect_stdout(io.StringIO()), upstream_fixture() as fixture:
            meter, endpoint, _, _, received = fixture
            with httpx.Client(trust_env=False, timeout=5) as client:
                for _ in range(2):
                    response = client.post(endpoint + '/chat/completions',
                        headers={'Authorization': 'Bearer ' + meter.token},
                        json={'messages': [], 'stream': True, 'max_tokens': 1})
                    self.assertEqual(response.status_code, 200)
            totals = meter.finish()
        self.assertEqual(totals['api_requests'], 2)
        self.assertEqual(totals['prompt_tokens'], 22)
        self.assertEqual(totals['completion_tokens'], 14)
        self.assertEqual(totals['total_tokens'], 36)
        self.assertEqual(totals['cached_tokens'], 8)
        self.assertEqual(totals['reasoning_tokens'], 6)
        self.assertTrue(totals['usage_complete'])
        self.assertTrue(all(body['max_tokens'] == 8192 for body in received))

    def test_missing_or_invalid_usage_never_claims_complete(self):
        cases = [
            {'http_status': 200, 'finished': True, 'usage': {}},
            {'http_status': 200, 'finished': True, 'usage': {'prompt_tokens': 11}},
            {'http_status': 200, 'finished': True,
             'usage': {'prompt_tokens': True, 'completion_tokens': 7}},
            {'finished': True, 'transport_error': 'ReadTimeout'},
        ]
        for request in cases:
            with self.subTest(request=request):
                result = meter_module.summarize([request])
                self.assertFalse(result['usage_complete'])
                self.assertEqual(result['unknown_usage_requests'], 1)
        result = meter_module.summarize([{'http_status': 200, 'finished': True,
            'usage': {'prompt_tokens': 11, 'completion_tokens': 7}}])
        self.assertTrue(result['usage_complete'])
        self.assertFalse(result['cache_usage_complete'])
        self.assertEqual(result['reasoning_reported_requests'], 0)

    def test_headers_stage_reset_still_drains_usage(self):
        with contextlib.redirect_stdout(io.StringIO()), upstream_fixture(hold_headers=True) as fixture:
            meter, _, ready, release, _ = fixture
            with socket.create_connection(('127.0.0.1', meter.server.server_port), timeout=3) as downstream:
                body = json.dumps({'messages': [], 'stream': True}).encode()
                headers = (f'POST /v1/chat/completions HTTP/1.1\r\nHost: localhost\r\n'
                           f'Authorization: Bearer {meter.token}\r\n'
                           f'Content-Length: {len(body)}\r\n\r\n')
                downstream.sendall(headers.encode() + body)
                self.assertTrue(ready.wait(3))
                linger_format = 'hh' if os.name == 'nt' else 'ii'
                downstream.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                                      struct.pack(linger_format, 1, 0))
            release.set()
            totals = meter.finish()
        self.assertEqual(totals['total_tokens'], 18)
        self.assertEqual(totals['abandoned_requests'], 1)
        self.assertTrue(totals['usage_complete'])

    def test_unfinished_meter_refuses_final_totals(self):
        meter = MemoryMeter('fixture', Path('unused'), max_seconds=1)
        meter.server = Mock()
        meter.active = 1
        with patch.object(meter_module.time, 'monotonic', side_effect=[0, 1000]):
            with self.assertRaisesRegex(RuntimeError, 'totals are not final'):
                meter.finish()


class BenchmarkRunnerTests(unittest.TestCase):
    def test_manifest_rejects_source_change_without_installed_clis(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            here = root / 'bench'
            here.mkdir()
            for name in ('meter.py', 'run.py', 'adapters.py', 'thinkflow_worker.py',
                         'monitor.py', 'package-lock.json', 'tasks.py'):
                (here / name).write_text('fixture', encoding='utf8')
            source = root / 'src/worker.py'
            source.parent.mkdir()
            source.write_text('before', encoding='utf8')
            pi = root / 'pi'
            pi.mkdir()
            (pi / 'package.json').write_text('{"version":"fixture"}', encoding='utf8')
            opencode = root / 'appdata/npm/node_modules/opencode-ai/package.json'
            opencode.parent.mkdir(parents=True)
            opencode.write_text('{"version":"fixture"}', encoding='utf8')
            destination = root / 'results'
            with patch.multiple(runner, HERE=here, ROOT=root, PI_ROOT=pi), \
                 patch.dict(os.environ, {'APPDATA': str(root / 'appdata')}), \
                 patch.object(runner.subprocess, 'check_output', return_value='fixture-commit\n'):
                runner.freeze_manifest(destination, False)
                frozen = (destination / 'manifest.json').read_bytes()
                runner.freeze_manifest(destination, False)
                source.write_text('after', encoding='utf8')
                with self.assertRaisesRegex(RuntimeError, 'Frozen code/configuration changed'):
                    runner.freeze_manifest(destination, False)
                self.assertEqual((destination / 'manifest.json').read_bytes(), frozen)

    def run_fake_job(self, *, broken_configuration=False, broken_progress=False):
        class FakeMeter:
            token = 'local-fixture'

            def __init__(self, *_args, **_kwargs):
                pass

            def start(self):
                return 'http://127.0.0.1:1/v1'

            def finish(self):
                return meter_module.summarize([])

        def materialize(_task, workspace):
            (workspace / 'public_tests.py').write_text('assert True\n', encoding='utf8')
            return 'Offline fixture'

        fake_tasks = types.SimpleNamespace(materialize=materialize,
            grade=lambda *_: {'passed': True, 'checks_passed': 1, 'checks_total': 1, 'details': []})

        def configuration(_harness, destination, *_args):
            if broken_configuration:
                raise ValueError('fixture configuration failed')
            (destination / 'harness.json').write_text('{invalid', encoding='utf8')
            (destination / 'workspace/public_tests.py').write_text('assert False\n', encoding='utf8')
            return ['never-executed'], {}

        process = Mock(returncode=0)
        if broken_progress:
            process.communicate.side_effect=[runner.subprocess.TimeoutExpired('fixture',8),None]
        with tempfile.TemporaryDirectory() as temporary, \
             patch.dict(sys.modules, {'tasks': fake_tasks}), \
             patch.object(runner, 'Meter', FakeMeter), \
             patch.object(runner, 'configuration', side_effect=configuration), \
             patch.object(runner.subprocess, 'Popen', return_value=process) as spawn, \
             patch.object(runner,'print_progress',side_effect=UnicodeEncodeError('gbk','\ufffd',0,1,'fixture')), \
             patch.object(runner,'kill_tree') as kill, \
             contextlib.redirect_stdout(io.StringIO()):
            result = runner.run_one({'id': 'fixture', 'task': 'fixture', 'harness': 'fixture'},
                                    Path(temporary), 'fixture-not-a-credential')
            saved = json.loads((Path(temporary) / 'fixture/result.json').read_text(encoding='utf8'))
            self.assertEqual(saved, result)
            if broken_configuration:
                spawn.assert_not_called()
            if broken_progress:
                kill.assert_not_called()
            return result

    def test_public_test_change_and_bad_diagnostic_are_recorded(self):
        result = self.run_fake_job()
        self.assertEqual(result['modified_public_tests'], ['public_tests.py'])
        self.assertFalse(result['grade']['passed'])
        self.assertIn('diagnostic_error', result)

    def test_configuration_failure_still_saves_result(self):
        result = self.run_fake_job(broken_configuration=True)
        self.assertIn('configuration failed', result['infrastructure_error'])
        self.assertIsNone(result['exit_code'])

    def test_progress_failure_cannot_terminate_worker(self):
        result=self.run_fake_job(broken_progress=True)
        self.assertIsNone(result['infrastructure_error'])
        self.assertEqual(result['exit_code'],0)
        self.assertEqual(result['observer_errors'],['UnicodeEncodeError'])


class BenchmarkReportRecoveryTests(unittest.TestCase):
    def test_recovery_requires_identical_conditions_and_observer_failure(self):
        analysis = load_module('benchmark_report_recovery', 'analyze.py')
        with tempfile.TemporaryDirectory() as temporary:
            original, recovery = Path(temporary)/'original', Path(temporary)/'recovery'
            job = {'id':'repair_routes-thinkflow-r2','task':'repair_routes','harness':'thinkflow','repeat':2}
            manifest = {'source_commit':'fixture','versions':{'thinkflow':'fixture'},
                        'generation':{'max_tokens':None}, 'files':{'worker.py':'fixture'},
                        'mode':'production','provider_capabilities':{'context_window':1048576}}
            for directory in (original,recovery):
                (directory/job['id']).mkdir(parents=True)
                (directory/'manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
                (directory/'plan.json').write_text(json.dumps([job]),encoding='utf-8')
            result = original/job['id']/'result.json'
            result.write_text(json.dumps({'infrastructure_error':None}),encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'Only the recorded'):
                analysis.recovery_runs(original,recovery)
            result.write_text(json.dumps({'infrastructure_error':"UnicodeEncodeError: 'gbk' codec failure"}),encoding='utf-8')
            self.assertEqual(analysis.recovery_runs(original,recovery),{job['id']:recovery/job['id']})
            for key in ('source_commit','versions','generation','files','mode','provider_capabilities'):
                changed = {**manifest,key:'changed'}
                (recovery/'manifest.json').write_text(json.dumps(changed),encoding='utf-8')
                with self.subTest(key=key), self.assertRaisesRegex(ValueError,'frozen conditions'):
                    analysis.recovery_runs(original,recovery)
            (recovery/'manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
            (recovery/'plan.json').write_text(json.dumps([{**job,'repeat':1}]),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'identity mismatch'):
                analysis.recovery_runs(original,recovery)


if __name__ == '__main__':
    unittest.main()
