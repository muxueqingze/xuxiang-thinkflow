"""Actual registry dispatch and information batches, without a model service."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.agent_loop import AgentConfig, AgentLoop
from src.compaction import CompactionConfig
from src.executor import ExecutionResult
from src.parser import Command, StreamingParser
from src.provider import ProviderConfig
from src.tool_registry import ToolResult, ToolSpec


class DispatchContracts(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix='thinkflow-dispatch-')))
        self.agent = AgentLoop(AgentConfig(cwd=str(self.root), compaction=CompactionConfig(enabled=False),
            provider=ProviderConfig(base_url='http://fixture.test', model='fixture')), event_sink=lambda event: None)
        self.addAsyncCleanup(self.agent.close)

    async def test_registry_preserves_builtin_failure_and_override_in_each_channel(self):
        missing = await self.agent.tool_registry.execute('read', {'path': 'missing.txt'})
        self.assertIsInstance(missing, ExecutionResult)
        self.assertFalse(missing.success)
        calls = []

        async def replacement(arguments):
            calls.append(arguments)
            return ToolResult('replacement body')

        self.agent.tool_registry.register(ToolSpec('read', 'replacement',
            {'type': 'object', 'properties': {'path': {'type': 'string'}}}), replacement)
        direct = await self.agent.tool_registry.execute('read', {'path': 'direct'})
        text = await self.agent._execute_command(Command('1', 'read', path='text'))
        native = await self.agent._execute_traditional_result('read', {'path': 'native'}, tool_id='native')
        self.assertEqual(str(direct), 'replacement body')
        self.assertEqual((text.content, native.content), ('replacement body', 'replacement body'))
        self.assertEqual([call['path'] for call in calls], ['direct', 'text', 'native'])

    async def test_tools_registered_after_start_are_discoverable_and_callable(self):
        async def lookup(arguments):
            return ToolResult('found')

        self.agent.tool_registry.register(ToolSpec('project_lookup', 'Find a project symbol',
            {'type': 'object', 'properties': {}}), lookup)
        catalog = next(t['function']['description'] for t in self.agent._openai_provider_tools()
                       if t['function']['name'] == 'load_tools')
        self.assertIn('project_lookup: Find a project symbol', catalog)
        await self.agent._load_tools({'names': ['project_lookup']})
        self.assertIn('project_lookup', self.agent._enabled_native_tool_names())
        result = await self.agent._execute_traditional_result('project_lookup', {}, tool_id='lookup')
        self.assertEqual(result.content, 'found')

    async def test_receipt_replay_preserves_content_and_process_output_separately(self):
        (self.root / 'present.txt').write_text('first\nsecond\nthird\n', encoding='utf-8')
        calls = []

        async def process(arguments):
            calls.append(arguments)
            return ExecutionResult(True, tool='process_fixture', stdout='output\n',
                                   content='artifact: result.png', exit_code=0)

        self.agent.tool_registry.register(ToolSpec('process_fixture', 'fixture',
            {'type': 'object', 'properties': {}}), process)
        for name, arguments in [('read', {'path': 'present.txt', 'offset': 2, 'limit': 1}),
                                ('list_files', {'path': '.'}), ('process_fixture', {})]:
            first = await self.agent._execute_traditional_tool(name, arguments, tool_id=name)
            repeated = await self.agent._execute_traditional_tool(name, arguments, tool_id=name)
            self.assertEqual(first, repeated, name)
        self.assertEqual(calls, [{}])
        snapshot = json.loads(json.dumps(self.agent.to_snapshot()))
        self.agent.load_snapshot(snapshot)
        restored = await self.agent._execute_traditional_tool('process_fixture', {}, tool_id='process_fixture')
        self.assertIn('artifact: result.png', restored)
        self.assertEqual(restored.count('output\n'), 1)
        self.assertEqual(calls, [{}])

    async def test_native_read_failure_keeps_independent_result_but_stops_write_chain(self):
        (self.root / 'present.txt').write_text('INDEPENDENT_CONTENT', encoding='utf-8')
        await self.agent._handle_traditional_tools([
            {'id': 'a', 'name': 'read', 'input': json.dumps({'path': 'missing.txt'})},
            {'id': 'b', 'name': 'read', 'input': json.dumps({'path': 'present.txt'})},
        ])
        self.assertEqual([r.status for r in self.agent.context.records], ['failed', 'success'])
        self.assertIn('INDEPENDENT_CONTENT', self.agent.messages[-1]['content'])
        await self.agent._handle_traditional_tools([
            {'id': 'c', 'name': 'read', 'input': json.dumps({'path': 'missing.txt'})},
            {'id': 'd', 'name': 'write', 'input': json.dumps({'path': 'must-not-exist.txt', 'content': 'bad'})},
        ])
        self.assertEqual(self.agent.context.records[-1].status, 'skipped')
        self.assertFalse((self.root / 'must-not-exist.txt').exists())

    async def test_text_read_failure_and_later_read_return_in_same_feedback(self):
        await self._assert_text_read_batch()

    async def test_explicit_read_boundary_settles_prior_independent_read_failure(self):
        await self._assert_text_read_batch(need_result=True)

    async def _assert_text_read_batch(self, need_result=False):
        (self.root / 'present.txt').write_text('INDEPENDENT_CONTENT', encoding='utf-8')
        self.agent.config.provider.enable_native_tools = False
        requests = []

        class ReadBatch(httpx.AsyncByteStream):
            async def __aiter__(self):
                boundary = ' need_result="true"' if need_result else ''
                for text in ('<tf-read id="1" path="missing.txt" />\n',
                             f'<tf-read id="2" path="present.txt"{boundary} />\n'):
                    yield ('data: ' + json.dumps({'choices': [{'delta': {'content': text}}]}) + '\n\n').encode()
                    await asyncio.sleep(.02)
                yield b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'

        async def send(request):
            body = json.loads(request.content)
            requests.append(body)
            if len(requests) == 1:
                return httpx.Response(200, stream=ReadBatch())
            self.assertIn('INDEPENDENT_CONTENT', json.dumps(body, ensure_ascii=False))
            return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"done"},"finish_reason":"stop"}]}\n\n')

        await self.agent.client.aclose()
        self.agent.client = httpx.AsyncClient(base_url='http://fixture.test', transport=httpx.MockTransport(send))
        await self.agent.run('Read both independent files.')
        self.assertEqual(len(requests), 2)
        self.assertEqual([r.status for r in self.agent.context.records], ['failed', 'success'])
        self.assertEqual(self.agent.stopped_reason, 'completed')

    def test_tag_name_accepts_newline_and_tab_without_losing_quoted_attributes(self):
        for separator in (' ', '\n', '\t', '\r\n  '):
            parser = StreamingParser()
            commands = parser.feed(f'<tf-write{separator}id="1" path="a b.txt">body</tf-write>')
            parser.flush()
            self.assertFalse(parser.errors)
            self.assertEqual([(c.path, c.content) for c in commands], [('a b.txt', 'body')])


if __name__ == '__main__':
    unittest.main()
