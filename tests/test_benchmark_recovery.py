"""Real loop regressions for faults observed in the 2026-09-29 benchmark."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock

import httpx

from src.agent_loop import AgentLoop, AgentConfig
from src.provider import ProviderConfig
from src.executor import ExecutionResult


def stream(*frames):
    return httpx.Response(200, text=''.join(
        'data: ' + json.dumps({'choices': [frame]}) + '\n\n' for frame in frames
    ) + 'data: [DONE]\n\n')


def call(name, arguments, identifier='call-a', finish='tool_calls'):
    return stream({'delta': {'tool_calls': [{'index': 0, 'id': identifier,
        'function': {'name': name, 'arguments': arguments}}]}},
        {'delta': {}, 'finish_reason': finish})


def done():
    return stream({'delta': {'content': 'Verified.'}, 'finish_reason': 'stop'})


class BenchmarkRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.events, self.requests = [], []
        self.agent = AgentLoop(AgentConfig(cwd=self.temp.name, max_run_turns=5,
            provider=ProviderConfig(base_url='https://fixture.invalid', model='deepseek-flash')),
            event_sink=self.events.append)
        await self.agent.client.aclose()

    async def asyncTearDown(self):
        await self.agent.close()
        self.temp.cleanup()

    def attach(self, handler):
        async def receive(request):
            body = json.loads(request.content)
            self.requests.append(body)
            return handler(body, len(self.requests))
        self.agent.client = httpx.AsyncClient(base_url='https://fixture.invalid',
            transport=httpx.MockTransport(receive))

    async def test_misrouted_tag_is_rejected_then_native_fallback_recovers(self):
        first_has_no_effect = []
        def handle(body, count):
            if count == 1:
                return call('tf-write', '{"path":"a.txt","content":"do not execute"}')
            if count == 2:
                first_has_no_effect.append(not (self.root / 'a.txt').exists())
                return call('write', '{"path":"a.txt","content":"recovered"}', 'call-b')
            return done()
        self.attach(handle)
        await self.agent.run('Create a file')
        self.assertEqual(first_has_no_effect, [True])
        self.assertIn('write', {t['function']['name'] for t in self.requests[1]['tools']})
        self.assertIn('PROTOCOL', self.requests[1]['messages'][-2]['content'])
        self.assertEqual((self.root / 'a.txt').read_text(), 'recovered')
        self.assertEqual(self.agent.stopped_reason, 'completed')
        self.assertEqual([r.status for r in self.agent.context.records], ['failed', 'success'])

    async def test_misroute_does_not_expand_explicit_native_allowlist(self):
        self.agent.config.provider.native_tools = ['read']
        self.attach(lambda body, count: call('tf-write', '{"path":"a.txt","content":"x"}')
                    if count == 1 else done())
        await self.agent.run('fixture')
        self.assertFalse(self.agent.native_write_fallback)
        self.assertFalse((self.root / 'a.txt').exists())
        self.assertEqual({t['function']['name'] for t in self.requests[1]['tools']}, {'read'})
        self.assertIn('PROTOCOL', self.agent.context.records[0].error)

    async def test_tag_attribute_fragment_in_native_name_also_gets_recovery(self):
        self.attach(lambda body, count: call('tf-write id=', '{}') if count == 1 else done())
        await self.agent.run('fixture')
        self.assertTrue(self.agent.native_write_fallback)
        self.assertIn('PROTOCOL', self.agent.context.records[0].error)
        self.assertEqual(list(self.root.iterdir()), [])

    async def test_truncated_native_arguments_are_not_executed_as_failed_tools(self):
        first_records = []
        def handle(body, count):
            if count == 1:
                return call('write', '{"path":"a.txt","content":"partial', finish='length')
            if count == 2:
                first_records.extend(self.agent.context.records)
                return call('write', '{"path":"a.txt","content":"complete"}', 'call-b')
            return done()
        self.attach(handle)
        await self.agent.run('fixture')
        self.assertEqual(first_records, [])
        self.assertEqual((self.root / 'a.txt').read_text(), 'complete')
        self.assertEqual(len(self.agent.context.records), 1)
        self.assertEqual(self.agent.usage.turns[0].abort_reason, 'length')
        self.assertFalse(any(e.get('failed') for e in self.events if e['type'] == 'turn_finished'))

    async def test_exhausted_continuation_is_not_reported_completed(self):
        self.agent.config.max_auto_continues = 1
        self.attach(lambda body, count: stream(
            {'delta': {'reasoning_content': 'fixture thinking'}, 'finish_reason': 'length'}))
        await self.agent.run('fixture')
        self.assertEqual(len(self.requests), 2)
        self.assertNotEqual(self.agent.stopped_reason, 'completed')
        self.assertTrue(self.agent.last_error)

    async def test_replayed_unknown_receipt_stops_immediately_without_reexecution(self):
        self.agent.executor.execute = AsyncMock(return_value=ExecutionResult(
            success=False, tool='write', status='unknown', error='receipt storage unavailable'))
        self.attach(lambda body, count: call('write', '{"path":"a.txt","content":"x"}'))
        await self.agent.run('first')
        self.assertEqual(self.agent.stopped_reason, 'unconfirmed_side_effect')
        await self.agent.run('retry')
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(self.agent.stopped_reason, 'unconfirmed_side_effect')
        self.agent.executor.execute.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
