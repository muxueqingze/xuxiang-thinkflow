"""Offline parser diagnostic and privacy boundaries; synthetic fixtures only."""
import asyncio
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.agent_loop import AgentConfig, AgentLoop
from src.parser import MAX_BUFFER_SIZE, ParseError, StreamingParser, parser_error_metadata
from src.provider import ProviderConfig

BENCH = Path(__file__).resolve().parents[1] / 'bench/harness_benchmark_20260929'
spec = importlib.util.spec_from_file_location('parser_diagnostic_monitor', BENCH / 'monitor.py')
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)

# These are deliberately fake privacy sentinels, never real prompts, bodies or credentials.
PRIVATE = 'SYNTHETIC_PRIVATE_SENTINEL'


class FakeResponse:
    def __init__(self, payload, channel, finish='stop'):
        self.lines = ['data: ' + json.dumps({'choices': [{'delta': {
            'reasoning_content' if channel == 'thinking' else 'content': payload,
        }, 'finish_reason': finish}]})]

    async def aiter_lines(self):
        for line in self.lines:
            yield line

    async def aclose(self):
        pass


async def run_fixture(payload, channel, *, finish='stop', gate_tail=False, sink=None):
    with tempfile.TemporaryDirectory() as directory:
        events, requests = [], []
        def observe(event):
            events.append(event)
            if sink is not None:
                sink(event)
        config = AgentConfig(provider=ProviderConfig(api_key='test-key', format='openai'),
                             cwd=directory, delivery_verify=False)
        agent = AgentLoop(config, event_sink=observe)
        responses = [FakeResponse(payload, channel, finish), FakeResponse('done', 'text')]
        async def send(_path, body):
            requests.append(body)
            return responses.pop(0)
        agent._send_stream_request = send
        try:
            if gate_tail:
                tails = [payload]
                with patch.object(agent.text_command_gate, 'feed', return_value=''), \
                     patch.object(agent.text_command_gate, 'flush', side_effect=lambda: tails.pop(0) if tails else ''):
                    await agent.run('synthetic parser fixture')
            else:
                await agent.run('synthetic parser fixture')
            diagnostics = [e for e in events if e['type'] == 'parser_error']
            return diagnostics, requests, agent.stopped_reason
        finally:
            await agent.close()


class ParserDiagnosticTests(unittest.TestCase):
    def test_rejection_kinds_preserve_validation_and_hide_payload(self):
        cases = [
            ('<tf-touch path="x" />', 'missing_id'),
            ('<tf-touch id="' + PRIVATE + '" path="x" />', 'invalid_id'),
            ('<tf-touch id="1" path="x" injected="3" />', 'invalid_injected'),
            ('<tf-touch id="1" />', 'missing_path'),
            ('<tf-copy id="1" path="x" />', 'missing_dest'),
            ('<tf-bash id="1" />', 'missing_cmd'),
            ('<tf-edit id="1" path="x"><old>' + PRIVATE + '</old></tf-edit>', 'missing_edit_sections'),
            ('<tf-edit id="1" path="x" />', 'missing_edit_body'),
        ]
        for payload, kind in cases:
            with self.subTest(kind=kind):
                parser = StreamingParser(allow_legacy_tags=False)
                self.assertEqual(parser.feed(payload), [])
                error = parser.errors[-1]
                self.assertEqual(error.kind, kind)
                self.assertEqual(error.diagnostic(channel='text', phase='feed'),
                                 {'error_kind': kind, 'channel': 'text', 'phase': 'feed'})
                self.assertNotIn(PRIVATE, json.dumps(error.diagnostic(channel='text', phase='feed')))
        duplicate = StreamingParser(allow_legacy_tags=False)
        self.assertEqual(len(duplicate.feed('<tf-touch id="1" path="x" />')), 1)
        self.assertEqual(duplicate.feed('<tf-touch id="1" path="x" />'), [])
        self.assertEqual(duplicate.errors[-1].kind, 'duplicate_id')
        overflow = StreamingParser()
        self.assertEqual(overflow.feed('x' * (MAX_BUFFER_SIZE + 1)), [])
        self.assertEqual(overflow.errors[-1].kind, 'buffer_overflow')
        self.assertNotIn('position', overflow.errors[-1].diagnostic(channel='thinking', phase='feed'))

    def test_flush_location_is_buffer_relative_and_default_zero_is_unknown(self):
        for payload, kind in [('<tf-write id="1"', 'incomplete_tag'),
                              ('<tf-write id="1" path="x">' + PRIVATE, 'unclosed_block')]:
            with self.subTest(kind=kind):
                parser = StreamingParser(allow_legacy_tags=False)
                self.assertEqual(parser.feed(payload), [])
                error = parser.flush()
                self.assertEqual(error.kind, kind)
                self.assertEqual(error.diagnostic(channel='text', phase='flush'), {
                    'error_kind': kind, 'channel': 'text', 'phase': 'flush',
                    'position': 0, 'position_scope': 'parser_buffer'})
                self.assertEqual(parser.buffer, '')
        legacy = ParseError(PRIVATE, raw=PRIVATE)
        self.assertEqual(legacy.diagnostic(channel='text', phase='feed'), {
            'error_kind': 'unknown', 'channel': 'text', 'phase': 'feed'})

    def test_untrusted_metadata_values_are_not_serialized(self):
        for kind in [PRIVATE, {'body': PRIVATE}, [PRIVATE], None]:
            data = parser_error_metadata(kind, channel=PRIVATE, phase=PRIVATE,
                                         position=PRIVATE, position_scope=PRIVATE)
            self.assertEqual(data, {'error_kind': 'unknown', 'channel': 'unknown', 'phase': 'unknown'})
        for position in [True, -1, MAX_BUFFER_SIZE + 1, PRIVATE, None]:
            data = parser_error_metadata('invalid_id', channel='text', phase='feed',
                                         position=position, position_scope='parser_buffer')
            self.assertNotIn('position', data)
        data = parser_error_metadata('invalid_id', channel='text', phase='feed', position=0)
        self.assertNotIn('position', data)

    def test_feed_failure_events_and_recovery_on_both_channels(self):
        payload = '<tf-write id="' + PRIVATE + '" path="x">' + PRIVATE + '</tf-write>'
        for channel in ['thinking', 'text']:
            with self.subTest(channel=channel):
                events, requests, stopped = asyncio.run(run_fixture(payload, channel))
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0]['error_kind'], 'invalid_id')
                self.assertEqual(events[0]['channel'], channel)
                self.assertEqual(events[0]['phase'], 'feed')
                self.assertNotIn('position', events[0])
                self.assertNotIn(PRIVATE, json.dumps(events))
                self.assertEqual(len(requests), 2)
                self.assertTrue(any('THINKFLOW PARSER ERROR' in m.get('content', '')
                                    for m in requests[1]['messages'] if m['role'] == 'user'))
                self.assertEqual(stopped, 'completed')

    def test_flush_failure_events_and_recovery_on_both_channels(self):
        payload = '<tf-write id="1" path="x">' + PRIVATE
        for channel in ['thinking', 'text']:
            with self.subTest(channel=channel):
                events, requests, stopped = asyncio.run(run_fixture(payload, channel))
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0]['error_kind'], 'unclosed_block')
                self.assertEqual(events[0]['channel'], channel)
                self.assertEqual(events[0]['phase'], 'flush')
                self.assertEqual(events[0]['position'], 0)
                self.assertEqual(events[0]['position_scope'], 'parser_buffer')
                self.assertNotIn(PRIVATE, json.dumps(events))
                self.assertEqual(len(requests), 2)
                self.assertEqual(stopped, 'completed')

    def test_message_stop_gate_tail_emits_safe_feed_error(self):
        payload = '<tf-touch id="' + PRIVATE + '" path="x" />'
        events, requests, stopped = asyncio.run(run_fixture(payload, 'text', gate_tail=True))
        self.assertEqual(len(events), 1)
        self.assertEqual((events[0]['channel'], events[0]['phase'], events[0]['error_kind']),
                         ('text', 'feed', 'invalid_id'))
        self.assertNotIn(PRIVATE, json.dumps(events))
        self.assertEqual((len(requests), stopped), (2, 'completed'))

    def test_length_cleanup_records_location_without_changing_continuation(self):
        payload = '<tf-write id="1" path="x">' + PRIVATE
        for channel in ['thinking', 'text']:
            with self.subTest(channel=channel):
                events, requests, stopped = asyncio.run(run_fixture(payload, channel, finish='length'))
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0]['channel'], channel)
                self.assertEqual(events[0]['phase'], 'flush')
                self.assertEqual(events[0]['position_scope'], 'parser_buffer')
                self.assertFalse(any('THINKFLOW PARSER ERROR' in m.get('content', '')
                                     for m in requests[1]['messages'] if m['role'] == 'user'))
                self.assertEqual((len(requests), stopped), (2, 'completed'))

    def test_monitor_projects_safe_fields_and_drops_raw_body_and_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            m = monitor.Monitor(directory, lambda: {'turn_usage': [], 'run_active': False})
            event = {'type': 'parser_error', 'seq': 3, 'turn': 2, 'error_kind': 'invalid_id',
                     'channel': 'text', 'phase': 'feed', 'position': 0,
                     'position_scope': PRIVATE, 'error': PRIVATE, 'message': PRIVATE,
                     'raw': PRIVATE, 'prompt': PRIVATE, 'body': PRIVATE, 'cmd': PRIVATE,
                     'path': PRIVATE, 'content': PRIVATE, 'input_summary': PRIVATE}
            m.observe(event)
            m.observe({**event, 'error_kind': PRIVATE, 'channel': PRIVATE, 'phase': PRIVATE})
            m.close()
            persisted = ''.join(p.read_text(encoding='utf-8') for p in Path(directory).iterdir())
            self.assertNotIn(PRIVATE, persisted)
            records = [json.loads(line) for line in (Path(directory) / 'trace.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertEqual(records[0]['error_kind'], 'invalid_id')
            self.assertNotIn('position', records[0])
            self.assertEqual(records[1]['error_kind'], 'unknown')
            self.assertEqual(set(records[0]), {'type', 'seconds', 'seq', 'turn', 'error_kind', 'channel', 'phase',
                                              'context_chars', 'message_count'})
            self.assertEqual((records[0]['context_chars'], records[0]['message_count']), (0, 0))

    def test_runtime_to_monitor_pipeline_persists_metadata_only(self):
        for channel in ['thinking', 'text']:
            with self.subTest(channel=channel), tempfile.TemporaryDirectory() as directory:
                m = monitor.Monitor(directory, lambda: {'turn_usage': [], 'run_active': False})
                events, requests, stopped = asyncio.run(run_fixture(
                    '<tf-write id="1" path="x">' + PRIVATE, channel, sink=m.observe))
                m.close()
                persisted = ''.join(p.read_text(encoding='utf-8') for p in Path(directory).iterdir())
                self.assertNotIn(PRIVATE, persisted)
                records = [json.loads(line) for line in (Path(directory) / 'trace.jsonl').read_text(encoding='utf-8').splitlines()]
                diagnostic = next(r for r in records if r['type'] == 'parser_error')
                self.assertEqual(diagnostic['channel'], channel)
                self.assertEqual(diagnostic['error_kind'], 'unclosed_block')
                self.assertEqual(diagnostic['position_scope'], 'parser_buffer')
                self.assertEqual((len(requests), stopped), (2, 'completed'))


if __name__ == '__main__':
    unittest.main()
