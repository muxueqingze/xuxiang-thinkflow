"""Independent metadata monitor boundary fixtures; no model/network/credentials."""
import asyncio
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

BENCH = Path(__file__).resolve().parents[1] / 'bench/harness_benchmark_20260929'


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, BENCH / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


monitor = load('review_benchmark_monitor', 'monitor.py')
with patch.dict(sys.modules, {'monitor': monitor}):
    worker = load('review_benchmark_worker', 'thinkflow_worker.py')


class MonitorBoundaryTests(unittest.TestCase):
    def test_progress_survives_legacy_console_encoding_without_losing_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            error = 'path contains replacement character \ufffd'
            (Path(directory)/'live.json').write_text(json.dumps({'last_tool':{'error':error}}),encoding='utf-8')
            meter = types.SimpleNamespace(lock=threading.RLock(),requests=[])
            buffer = io.BytesIO()
            output = io.TextIOWrapper(buffer,encoding='gbk',errors='strict')
            with patch.object(sys,'stdout',output):
                monitor.print_progress('fixture',directory,meter)
            output.flush()
            self.assertEqual(json.loads(buffer.getvalue().decode('ascii'))['last_tool']['error'],error)
            output.detach()

    def test_counts_do_not_persist_text_or_mutate_event(self):
        with tempfile.TemporaryDirectory() as directory:
            metadata = {'context_chars': 99, 'message_count': 3,
                        'run_active': True, 'stopped_reason': 'running', 'turn_usage': []}
            m = monitor.Monitor(directory, lambda: metadata)
            events = [
                {'type': 'reasoning_activity', 'turn': 1, 'chars': 9, 'text': 'PRIVATE_REASONING'},
                {'type': 'text_delta', 'turn': 1, 'text': 'PRIVATE_ANSWER'},
                {'type': 'tool_started', 'turn': 1, 'id': 'a', 'tool': 'write',
                 'content': 'PRIVATE_FILE_BODY', 'input_hash': 'hash'},
                {'type': 'tool_completed', 'turn': 1, 'id': 'a', 'tool': 'write',
                 'success': True, 'status': 'success'},
            ]
            original = copy.deepcopy(events)
            for event in events:
                m.observe(event)
            metadata.update(run_active=False, stopped_reason='max_auto_continues')
            m.close()
            self.assertEqual(events, original)
            persisted = ''.join(p.read_text(encoding='utf-8') for p in Path(directory).iterdir())
            self.assertNotIn('PRIVATE_', persisted)
            live = json.loads((Path(directory) / 'live.json').read_text(encoding='utf-8'))
            self.assertEqual(live['reasoning_chars'], 9)
            self.assertEqual(live['text_chars'], len('PRIVATE_ANSWER'))
            self.assertEqual(live['phase'], 'finished')
            self.assertEqual(live['stopped_reason'], 'max_auto_continues')
            self.assertFalse(live['run_active'])

    def test_final_snapshot_bypasses_throttle_and_usage_is_not_double_counted(self):
        with tempfile.TemporaryDirectory() as directory:
            metadata = {'turn_usage': [{'turn': 1, 'usage_reported': True,
                                      'prompt_tokens': 10, 'completion_tokens': 2}],
                        'run_active': True, 'stopped_reason': 'running'}
            m = monitor.Monitor(directory, lambda: metadata, clock=lambda: 1.0)
            m.observe({'type': 'turn_finished', 'turn': 1})
            metadata.update(run_active=False, stopped_reason='completed')
            m.observe({'type': 'run_finished', 'turn': 1, 'stopped_reason': 'completed'})
            m.close()
            trace = [json.loads(line) for line in (Path(directory) / 'trace.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertEqual(sum(e['type'] == 'request_usage' for e in trace), 1)
            live = json.loads((Path(directory) / 'live.json').read_text(encoding='utf-8'))
            self.assertEqual(live['stopped_reason'], 'completed')
            self.assertFalse(live['run_active'])

    def test_meter_is_authoritative_and_pending_or_invalid_usage_is_not_counted(self):
        meter = types.SimpleNamespace(lock=threading.RLock(), requests=[
            {'finished': True, 'usage': {'prompt_tokens': 10, 'completion_tokens': 2, 'total_tokens': 999}},
            {'finished': False, 'usage': {'prompt_tokens': 20, 'completion_tokens': 3}},
            {'finished': True, 'usage': {'prompt_tokens': True, 'completion_tokens': 3}},
            {'finished': True, 'usage': None},
        ])
        original = copy.deepcopy(meter.requests)
        self.assertEqual(monitor.meter_progress(meter), {
            'completed_requests': 1, 'completed_tokens': 12, 'pending_usage_requests': 3})
        self.assertEqual(meter.requests, original)

    def test_final_monitor_write_failure_cannot_erase_successful_worker_result(self):
        class Agent:
            _run_active = False
            stopped_reason = ''
            last_error = ''
            context = types.SimpleNamespace(records=[])
            usage = types.SimpleNamespace(turns=[], to_dict=lambda: {})

            def build_system_prompt(self):
                return ''

            def build_request_body(self, system):
                return {'messages': []}

            async def run(self, prompt):
                self.stopped_reason = 'completed'

            async def close(self):
                pass

        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / 'harness.json'
            replace = os.replace
            calls = 0

            def final_failure(source, target):
                nonlocal calls
                calls += 1
                if calls > 1:
                    raise PermissionError('injected final monitor write failure')
                return replace(source, target)

            with patch.dict(os.environ, {'BENCH_RECORD': str(destination), 'BENCH_URL': 'https://fixture.invalid',
                                         'BENCH_API_KEY': 'not-a-secret'}), \
                    patch.object(worker, 'create_agent', return_value=Agent()), \
                    patch.object(worker, 'resolve_system_prompt', return_value=''), \
                    patch.object(sys, 'stdin', io.StringIO('fixture')), \
                    patch.object(monitor.os, 'replace', side_effect=final_failure):
                asyncio.run(worker.main())
            self.assertEqual(json.loads(destination.read_text(encoding='utf-8'))['stopped_reason'], 'completed')
            self.assertTrue(json.loads(destination.read_text(encoding='utf-8'))['monitor_errors'])
            with patch.dict(os.environ, {'BENCH_RECORD': str(destination), 'BENCH_URL': 'https://fixture.invalid',
                                         'BENCH_API_KEY': 'not-a-secret'}), \
                    patch.object(worker, 'create_agent', return_value=Agent()), \
                    patch.object(worker, 'resolve_system_prompt', return_value=''), \
                    patch.object(sys, 'stdin', io.StringIO('fixture')), \
                    patch.object(worker, 'Monitor', side_effect=PermissionError('initial monitor unavailable')):
                asyncio.run(worker.main())
            result = json.loads(destination.read_text(encoding='utf-8'))
            self.assertEqual(result['stopped_reason'], 'completed')
            self.assertTrue(result['monitor_errors'])


if __name__ == '__main__':
    unittest.main()
