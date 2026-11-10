"""Tool output remains useful after truncation, interruption and session changes."""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.agent_loop import AgentConfig, AgentLoop
from src.output_capture import OutputCapture
from src.parser import Command, StreamingParser
from src.provider import ProviderConfig
from src.security import SecurityPolicy
from src.session import SessionStore
from src.tool_registry import ToolSpec


class SSE(httpx.AsyncByteStream):
    def __init__(self, text):
        self.text = text

    async def __aiter__(self):
        for data in ({'choices': [{'delta': {'content': self.text}, 'finish_reason': None}]},
                     {'choices': [{'delta': {}, 'finish_reason': 'stop'}]}):
            yield ('data: ' + json.dumps(data) + '\n\n').encode()
        yield b'data: [DONE]\n\n'


class OutputRecallTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='thinkflow-output-recall-')
        self.root = Path(self.temp.name)
        self.agents = []
        self.agent = self.make_agent()

    def make_agent(self):
        agent = AgentLoop(AgentConfig(
            cwd=str(self.root),
            provider=ProviderConfig(base_url='https://example.test', model='fixture', api_key='',
                                    enable_native_tools=False),
            security=SecurityPolicy(allowed_roots=[str(self.root)], bash_policy='unrestricted',
                                    approval_mode='approve_all'),
        ), event_sink=lambda event: None)
        self.agents.append(agent)
        return agent

    async def asyncTearDown(self):
        temporary_roots = [agent.outputs.root for agent in self.agents if agent.outputs._temporary is not None]
        for agent in self.agents:
            await agent.close()
        self.temp.cleanup()
        self.assertFalse(self.root.exists())
        for root in temporary_roots:
            self.assertFalse(root.exists())

    async def produce(self, text, *, identifier='1', error=False):
        code = f'import sys\nsys.stdout.buffer.write({text!r}.encode("utf-8"))\n'
        if error:
            code += 'sys.stderr.buffer.write("TAIL_ERROR_中文\\n".encode("utf-8"))\nsys.exit(3)\n'
        return await self.agent._execute_command(Command(identifier, 'python', cmd=code))

    async def test_failed_process_keeps_tail_and_exact_middle_after_ledger_eviction(self):
        original = 'A' * 45000 + 'MIDDLE_中文' + 'Z' * 45000 + '\nTAIL_DIAGNOSTIC\n'
        result = await self.produce(original, error=True)
        self.assertFalse(result.success)
        self.assertEqual(result.exit_code, 3)
        self.assertTrue(result.stdout.endswith('TAIL_DIAGNOSTIC\n'))
        self.assertEqual(result.stderr, 'TAIL_ERROR_中文\n')
        self.assertLessEqual(len(result.stdout), 80000)
        self.assertEqual(result.output_refs['stdout'], 'receipt:1:stdout')
        self.assertEqual(result.output_sizes['stdout'], len(original))
        self.agent.context.records[0].injected = 1
        self.agent.context.compact_history(keep_records=0)
        self.assertEqual(self.agent.context.records, [])
        recalled = await self.agent._recall_tool({'ref': 'receipt:1:stdout', 'offset': 44998, 'limit': 20})
        self.assertIn(original[44998:45018], recalled)
        self.assertIn('TAIL_ERROR_中文', await self.agent._recall_tool({'ref': 'receipt:1:stderr'}))

    async def test_ledger_clips_its_view_without_losing_read_result(self):
        original = 'prefix ' * 700 + 'UNIQUE_MIDDLE_中文' + ' suffix' * 1000
        (self.root / 'large.txt').write_bytes(original.encode())
        result = await self.agent._execute_command(Command('1', 'read', path='large.txt'))
        self.assertEqual(result.content, original)
        record = self.agent.context.records[0]
        record.injected = 1
        self.agent.context.compact_history(payload_budget=300)
        self.assertNotIn('UNIQUE_MIDDLE_中文', record.stdout)
        self.assertFalse(record.truncated)
        self.assertTrue(record.payload_clipped)
        self.assertIn('receipt:1:stdout', record.to_injection_string())
        recalled = await self.agent._recall_tool({'ref': 'receipt:1:stdout', 'offset': 4890, 'limit': 40})
        self.assertIn(original[4890:4930], recalled)

    async def test_snapshot_history_and_fork_have_independent_output_files(self):
        original = 'saved 原文\r\n' * 2000
        await self.produce(original)
        source_root = self.agent.outputs.root
        source_snapshot = self.agent.to_snapshot()
        store = SessionStore(str(self.root / 'live.json'))
        store.save(source_snapshot)
        self.assertEqual(source_snapshot['outputs']['root'], str(source_root))
        raw = json.loads(store.path.read_text())
        self.assertEqual(raw['outputs']['root'], 'live.outputs')
        history_path = next((self.root / 'history').glob('*.json'))
        history = SessionStore(str(history_path))
        self.assertEqual(json.loads(history_path.read_text())['outputs']['root'], '../live.outputs')
        await self.agent.close()
        self.assertFalse(source_root.exists())
        restored = self.make_agent()
        restored.bind_output_store(store.output_dir)
        restored.load_snapshot(history.load())
        self.assertIn(original[19:54], await restored._recall_tool({'ref': 'receipt:1:stdout', 'offset': 19, 'limit': 35}))
        fork = self.make_agent()
        fork_store = SessionStore(str(self.root / 'fork.json'))
        fork.bind_output_store(fork_store.output_dir)
        fork.load_snapshot(restored.to_snapshot())
        fork_store.save(fork.to_snapshot(), history=False)
        store.output_dir.rename(self.root / 'retired-parent.outputs')
        self.assertIn(original[19:54], await fork._recall_tool({'ref': 'receipt:1:stdout', 'offset': 19, 'limit': 35}))
        self.assertTrue(fork.outputs.root.samefile(fork_store.output_dir))
        self.assertNotEqual(fork.outputs.root, restored.outputs.root)

    async def test_receipt_preview_clipping_preserves_native_read_deduplication(self):
        original = 'source 中文\n' * 1200
        self.agent.config.provider.enable_native_tools = True
        await self.agent._handle_traditional_tools([
            {'id': 'write-original', 'name': 'write',
             'input': json.dumps({'path': 'source.txt', 'content': original})},
            {'id': 'read-original', 'name': 'read', 'input': json.dumps({'path': 'source.txt'})},
        ])
        before = self.agent._messages_with_runtime_status()[-1]
        self.assertIn('Exact same complete content', before['content'])
        self.agent.context.compact_history(payload_budget=500)
        after = self.agent._messages_with_runtime_status()[-1]
        self.assertEqual(after, before)
        self.assertNotIn(original, after['content'])

    async def test_output_recall_reaches_both_native_provider_messages(self):
        original = 'native 中部\n' * 1500
        await self.produce(original)
        self.agent.config.provider.enable_native_tools = True
        self.agent.config.provider.max_tokens = 1024
        for provider in ('openai', 'anthropic'):
            self.agent.config.provider.format = provider
            await self.agent._handle_traditional_tools([{
                'id': 'recall-' + provider, 'name': 'recall',
                'input': json.dumps({'ref': 'receipt:1:stdout', 'offset': 27, 'limit': 49}),
            }])
            content = self.agent.messages[-1]['content']
            text = content if provider == 'openai' else content[0]['content']
            self.assertIn(original[27:76], text)
            self.assertIn('receipt:1:stdout', text)
        self.agent.tool_registry.register(ToolSpec(name='fixture_search', description='Fixture search',
            parameters={'type': 'object', 'properties': {}}), lambda _input: self.search_output(original))
        result = await self.agent._execute_traditional_result('fixture_search', {}, tool_id='extension-output')
        self.assertIn(original, self.agent._native_result_text(result))
        self.assertIn('receipt:4:stdout', self.agent._native_result_text(result))

    @staticmethod
    async def search_output(text):
        return text

    async def test_two_output_ranges_share_one_information_boundary(self):
        original = 'A' * 9000 + 'MIDDLE_RULE' + 'Z' * 9000 + 'TAIL_RULE'
        await self.produce(original)
        self.agent.context.records[0].injected = 1
        requests = []

        def receive(request):
            body = json.loads(request.content)
            requests.append(body)
            if len(requests) == 1:
                text = ('<tf-recall id="2" ref="receipt:1:stdout" offset="9000" limit="11" />'
                        '<tf-recall id="3" ref="receipt:1:stdout" offset="18011" limit="9" />')
            else:
                feedback = body['messages'][-1]['content']
                self.assertIn('MIDDLE_RULE', feedback)
                self.assertIn('TAIL_RULE', feedback)
                text = '<tf-write id="4" path="result.txt">both rules recovered</tf-write>Done.'
            return httpx.Response(200, stream=SSE(text))

        await self.agent.client.aclose()
        self.agent.client = httpx.AsyncClient(base_url=self.agent.config.provider.base_url,
                                             transport=httpx.MockTransport(receive))
        await self.agent.run('Recover both original rules and write the result.')
        self.assertEqual(len(requests), 2, [str(body['messages'][-1]['content'])[-1200:] for body in requests])
        self.assertEqual((self.root / 'result.txt').read_text(), 'both rules recovered')
        self.assertEqual([r.tool for r in self.agent.context.records], ['python', 'recall', 'recall', 'write'])

    async def test_literal_queries_share_a_batch_and_find_across_stream_chunks(self):
        parser = StreamingParser(allow_legacy_tags=False)
        self.assertEqual(parser.feed('<tf-recall id="7" ref="receipt:1:stdout" query="" />'), [])
        self.assertEqual(len(parser.feed('<tf-recall id="7" ref="receipt:1:stdout" query="MATCH_A" />')), 1)
        original = 'x' * 65532 + '跨块_MATCH_A=value-a\r\n' + 'z' * 40000 + '\r\nMATCH_B=value-b\r\n'
        await self.produce(original)
        self.agent.context.records[0].injected = 1
        requests = []

        def receive(request):
            body = json.loads(request.content)
            requests.append(body)
            if len(requests) == 1:
                text = ('<tf-recall id="2" ref="receipt:1:stdout" query="跨块_MATCH_A" limit="120" />'
                        '<tf-recall id="3" ref="receipt:1:stdout" query="MATCH_B" limit="120" />')
            else:
                feedback = body['messages'][-1]['content']
                self.assertIn('MATCH_A=value-a', feedback)
                self.assertIn('MATCH_B=value-b', feedback)
                self.assertNotIn('x' * 500, feedback)
                text = '<tf-write id="4" path="searched.txt">value-a value-b</tf-write>Done.'
            return httpx.Response(200, stream=SSE(text))

        await self.agent.client.aclose()
        self.agent.client = httpx.AsyncClient(base_url=self.agent.config.provider.base_url,
                                             transport=httpx.MockTransport(receive))
        await self.agent.run('Find both markers and save the values.')
        self.assertEqual(len(requests), 2)
        self.assertEqual((self.root / 'searched.txt').read_text(), 'value-a value-b')
        missing = await self.agent._recall_tool({'ref': 'receipt:1:stdout', 'query': 'NO_MATCH'})
        self.assertIn('No match', missing)
        self.agent.config.provider.enable_native_tools = True
        self.agent.config.provider.max_tokens = 1024
        for provider in ('openai', 'anthropic'):
            self.agent.config.provider.format = provider
            await self.agent._handle_traditional_tools([{
                'id': 'query-' + provider, 'name': 'recall',
                'input': json.dumps({'ref': 'receipt:1:stdout', 'query': 'MATCH_B', 'limit': 120}),
            }])
            content = self.agent.messages[-1]['content']
            text = content if provider == 'openai' else content[0]['content']
            self.assertIn('MATCH_B=value-b', text)

    async def test_timeout_and_cancel_preserve_received_output(self):
        self.agent.config.security.max_bash_output_chars = 256
        self.agent.config.security.bash_timeout_seconds = 0.8
        code = 'import sys,time\nsys.stdout.write("p"*40000+"BEFORE_STOP\\n")\nsys.stdout.flush()\ntime.sleep(60)'
        timed = await self.agent._execute_command(Command('1', 'python', cmd=code))
        self.assertTrue(timed.timed_out)
        self.assertIn('BEFORE_STOP', timed.stdout)
        self.agent.config.security.bash_timeout_seconds = 60
        task = asyncio.create_task(self.agent._execute_command(Command('2', 'python', cmd=code)))
        try:
            deadline = asyncio.get_running_loop().time() + 5
            while not any(p.stat().st_size > 30000 for p in self.agent.outputs.root.glob('*.part')):
                self.assertLess(asyncio.get_running_loop().time(), deadline)
                await asyncio.sleep(0.01)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
            record = self.agent.context.records[-1]
            self.assertEqual(record.status, 'cancelled')
            self.assertIn('BEFORE_STOP', record.stdout)
            self.assertIn('BEFORE_STOP', await self.agent._recall_tool({'ref': record.output_refs['stdout'], 'offset': 39990, 'limit': 22}))
            self.assertEqual(list(self.agent.outputs.root.glob('*.part')), [])
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_configured_credentials_are_redacted_across_capture_chunks(self):
        secret = 'fixture-provider-credential-29'
        text = 'prefix 中文 ' + secret + ' suffix\r\n'

        class Pipe:
            def __init__(self):
                raw = text.encode()
                self.parts = [raw[i:i+3] for i in range(0, len(raw), 3)] + [b'']

            async def read(self, _size):
                return self.parts.pop(0)

        capture = OutputCapture(self.agent.outputs, 100, [secret])
        await capture.read(Pipe())
        expected = text.replace(secret, '[REDACTED]')
        self.assertEqual(capture.text, expected)
        self.assertEqual(self.agent.outputs.path_for(capture.ref).read_bytes(), expected.encode())

    async def test_short_results_stay_small_and_new_session_resets_originals_and_ids(self):
        self.agent.conversation.record_request(0, 'old request', 'old request')
        self.agent.messages.append({'role': 'user', 'content': 'old request'})
        result = await self.produce('short')
        self.assertNotIn('Original', self.agent._format_tool_result(result))
        self.assertNotIn('<tf-recall', self.agent.build_system_prompt())
        old_root = self.agent.outputs.root
        self.agent.reset_session()
        self.assertFalse(old_root.exists())
        self.assertEqual(self.agent.conversation.requests, [])
        self.assertEqual(self.agent.context.output_index, {})
        self.assertEqual(self.agent.outputs.entries, {})
        again = await self.produce('new', identifier='1')
        self.assertTrue(again.success)
        self.assertEqual(again.stdout, 'new')


if __name__ == '__main__':
    unittest.main()
