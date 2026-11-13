"""Exercise predictable state updates through real SSE, FIFO and persistence."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest

import httpx

from src.agent_loop import AgentConfig, AgentLoop
from src.compaction import CompactionConfig
from src.parser import StreamingParser
from src.provider import ProviderConfig
from src.security import SecurityPolicy
from src.session import SessionStore


def plan(status='in_progress', evidence=(), **extra):
    return {'explanation': '阶段选择', 'steps': [{'id': 's1', 'title': '生成文件',
            'acceptance': '文件包含目标正文', 'status': status, 'evidence': list(evidence)}], **extra}


def tag(identifier, name, arguments, attributes=''):
    return f'<tf-{name} id="{identifier}"{attributes}>' + json.dumps(arguments, ensure_ascii=False) + f'</tf-{name}>\n'


class Wire(httpx.AsyncByteStream):
    def __init__(self, chunks, protocol='openai', after_chunk=None):
        self.chunks, self.protocol, self.after_chunk = chunks, protocol, after_chunk

    async def __aiter__(self):
        def event(value):
            return ('data: ' + json.dumps(value, ensure_ascii=False) + '\n\n').encode('utf8')
        if self.protocol == 'anthropic':
            yield event({'type': 'message_start', 'message': {'usage': {'input_tokens': 10}}})
            yield event({'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}})
        for index, chunk in enumerate(self.chunks):
            value = ({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': chunk}}
                     if self.protocol == 'anthropic' else {'choices': [{'delta': {'content': chunk}, 'finish_reason': None}]})
            yield event(value)
            if self.after_chunk:
                await self.after_chunk(index)
            else:
                await asyncio.sleep(0)
        if self.protocol == 'anthropic':
            yield event({'type': 'content_block_stop', 'index': 0})
            yield event({'type': 'message_delta', 'delta': {'stop_reason': 'end_turn'}, 'usage': {'output_tokens': 10}})
            yield event({'type': 'message_stop'})
        else:
            yield event({'choices': [{'delta': {}, 'finish_reason': 'stop'}],
                         'usage': {'prompt_tokens': 10, 'completion_tokens': 10}})
            yield b'data: [DONE]\n\n'


class StreamStateTests(unittest.IsolatedAsyncioTestCase):
    async def test_plan_write_and_completion_share_one_live_response(self):
        for protocol in ('openai', 'anthropic'):
            with self.subTest(protocol=protocol), tempfile.TemporaryDirectory(prefix='tf-stream-plan-') as directory:
                completed = [asyncio.Event() for _ in range(3)]
                events, requests = [], []

                def observe(event):
                    events.append(event)
                    if event['type'] == 'tool_completed':
                        completed[int(event['id']) - 1].set()

                agent = AgentLoop(AgentConfig(cwd=directory, compaction=CompactionConfig(enabled=False),
                    provider=ProviderConfig(format=protocol, model='fixture', enable_native_tools=False,
                                            max_tokens=4096)), event_sink=observe)
                store = SessionStore(str(Path(directory) / 'session.json'), cwd=directory)
                store.bind(agent)
                stage = {'summary': '已选定正文', 'decisions': ['使用UTF-8'],
                         'files': [{'path': '结果.txt', 'note': '交付文件'}]}
                chunks = [tag('1', 'update_plan', plan(context=stage)),
                          '<tf-write id="2" path="结果.txt">目标正文</tf-write>\n',
                          tag('3', 'update_plan', plan('completed', ['2'])), '完成。']

                async def after(index):
                    if index < 3:
                        await asyncio.wait_for(completed[index].wait(), 3)
                        self.assertFalse(any(e['type'] == 'run_finished' for e in events))

                async def request(req):
                    requests.append(json.loads(req.content))
                    return httpx.Response(200, stream=Wire(chunks, protocol, after))

                await agent.client.aclose()
                agent.client = httpx.AsyncClient(transport=httpx.MockTransport(request), base_url='http://fixture')
                try:
                    await agent.run('生成中文文件并更新阶段状态')
                    self.assertEqual(len(requests), 1)
                    self.assertEqual((Path(directory) / '结果.txt').read_text(encoding='utf8'), '目标正文')
                    self.assertEqual([r.status for r in agent.context.records], ['success'] * 3)
                    self.assertEqual(agent.task_plan, plan('completed', ['2'], context=stage))
                    self.assertEqual(store.load()['task_plan'], agent.task_plan)
                    self.assertEqual(agent.usage.totals()['api_calls'], 1)
                finally:
                    await agent.close()

    async def test_failed_plan_stops_dependent_write_and_returns_actual_error(self):
        with tempfile.TemporaryDirectory(prefix='tf-plan-failure-') as directory:
            agent = AgentLoop(AgentConfig(cwd=directory, provider=ProviderConfig(enable_native_tools=False)),
                              event_sink=lambda _: None)
            requests = []
            async def request(req):
                requests.append(json.loads(req.content))
                text = (tag('1', 'update_plan', plan('completed', ['missing'])) +
                        '<tf-touch id="2" path="must-not-exist" />\n' if len(requests) == 1 else '已收到失败原因。')
                return httpx.Response(200, stream=Wire([text]))
            await agent.client.aclose()
            agent.client = httpx.AsyncClient(transport=httpx.MockTransport(request), base_url='http://fixture')
            try:
                await agent.run('fixture')
                self.assertEqual(len(requests), 2)
                self.assertFalse((Path(directory) / 'must-not-exist').exists())
                self.assertEqual(agent.task_plan, {'explanation': '', 'steps': []})
                self.assertIn('missing', json.dumps(requests[1], ensure_ascii=False))
                self.assertEqual([r.status for r in agent.context.records], ['failed', 'skipped'])
            finally:
                await agent.close()

    async def test_explicit_result_boundary_and_read_only_state_update(self):
        with tempfile.TemporaryDirectory(prefix='tf-plan-boundary-') as directory:
            agent = AgentLoop(AgentConfig(cwd=directory, security=SecurityPolicy(read_only=True),
                              provider=ProviderConfig(enable_native_tools=False)), event_sink=lambda _: None)
            requests = []
            async def request(req):
                requests.append(json.loads(req.content))
                text = tag('1', 'update_plan', plan(), ' need_result="true"') if len(requests) == 1 else '完成。'
                return httpx.Response(200, stream=Wire([text]))
            await agent.client.aclose()
            agent.client = httpx.AsyncClient(transport=httpx.MockTransport(request), base_url='http://fixture')
            try:
                await agent.run('只读分析也可以保存计划')
                self.assertEqual(len(requests), 2)
                native = await agent._execute_traditional_result('update_plan', plan(), tool_id='native-plan')
                self.assertTrue(native.success, native.error)
                self.assertEqual(agent.task_plan, plan())
            finally:
                await agent.close()

    async def test_information_search_batch_and_partial_json_command(self):
        parser = StreamingParser(allow_legacy_tags=False)
        self.assertEqual(parser.feed('<tf-update_plan id="1">{"steps":[]'), [])
        self.assertIsNotNone(parser.flush())
        with tempfile.TemporaryDirectory(prefix='tf-search-stream-') as directory:
            root = Path(directory)
            (root / '.gitignore').write_text('node_modules/\n', encoding='utf8')
            (root / 'node_modules').mkdir()
            (root / 'node_modules/hidden.txt').write_text('MATCH hidden', encoding='utf8')
            (root / 'visible.txt').write_text('MATCH visible', encoding='utf8')
            agent = AgentLoop(AgentConfig(cwd=directory, provider=ProviderConfig(enable_native_tools=False)),
                              event_sink=lambda _: None)
            requests = []
            async def request(req):
                requests.append(json.loads(req.content))
                text = (tag('1', 'grep', {'pattern': 'MATCH'}) + tag('2', 'glob', {'pattern': '*.txt'}) +
                        tag('3', 'list_files', {'path': '.'}) if len(requests) == 1 else '完成。')
                return httpx.Response(200, stream=Wire([text]))
            await agent.client.aclose()
            agent.client = httpx.AsyncClient(transport=httpx.MockTransport(request), base_url='http://fixture')
            try:
                await agent.run('查找项目文件')
                self.assertEqual(len(requests), 2)
                self.assertEqual([r.tool for r in agent.context.records], ['grep', 'glob', 'list_files'])
                self.assertEqual([r.status for r in agent.context.records], ['success'] * 3)
                self.assertIn('MATCH visible', json.dumps(requests[1]))
                self.assertNotIn('MATCH hidden', json.dumps(requests[1]))
            finally:
                await agent.close()


if __name__ == '__main__':
    unittest.main()
