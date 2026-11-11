"""Anthropic wire blocks survive real SSE consumption, tool turns and snapshots."""
import asyncio
import copy
import json
from pathlib import Path
import tempfile
import unittest

import httpx

from src.agent_loop import AgentConfig, AgentLoop
from src.compaction import CompactionConfig
from src.provider import ProviderConfig
from src.session import SessionStore


def block_events(index, block, deltas=(), *, closed=True):
    events = [{'type': 'content_block_start', 'index': index, 'content_block': block}]
    events.extend({'type': 'content_block_delta', 'index': index, 'delta': delta}
                  for delta in deltas)
    if closed:
        events.append({'type': 'content_block_stop', 'index': index})
    return events


def finish(reason='end_turn'):
    return [{'type': 'message_delta', 'delta': {'stop_reason': reason},
             'usage': {'output_tokens': 42}}, {'type': 'message_stop'}]


class EventStream(httpx.AsyncByteStream):
    def __init__(self, events, boundary='complete'):
        self.events = events
        self.boundary = boundary
        self.waiting = asyncio.Event()
        self.closed = False

    async def __aiter__(self):
        for event in self.events:
            yield ('event: ' + event['type'] + '\ndata: ' +
                   json.dumps(event, ensure_ascii=False) + '\n\n').encode()
        self.waiting.set()
        if self.boundary == 'cancel':
            await asyncio.Event().wait()
        elif self.boundary == 'transport':
            raise httpx.ReadError('fixture disconnect')

    async def aclose(self):
        self.closed = True


class AnthropicRoundtripTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 2.0
        root = Path(__file__).resolve().parents[1] / 'temp' / 'native-roundtrip-20261111'
        root.mkdir(parents=True, exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(dir=root)
        self.agents = []
        (Path(self.directory.name) / 'source.txt').write_text('actual file contents', encoding='utf-8')

    async def asyncTearDown(self):
        for agent in self.agents:
            await agent.close()
        self.directory.cleanup()

    async def agent(self, receive, *, events=None):
        agent = AgentLoop(AgentConfig(cwd=self.directory.name, provider=ProviderConfig(
            base_url='http://127.0.0.1:1', model='fixture', format='anthropic', max_tokens=2048),
            compaction=CompactionConfig(enabled=False)), event_sink=events)
        self.agents.append(agent)
        await agent.client.aclose()
        agent.client = httpx.AsyncClient(base_url='http://127.0.0.1:1', transport=httpx.MockTransport(receive))
        return agent

    async def test_stream_tool_results_snapshot_and_next_request_keep_exact_blocks(self):
        omitted = {'type': 'thinking', 'thinking': '', 'signature': 'opaque-A+/=B'}
        command_text = '先准备。<tf-touch id="1" path="created.txt" />随后读取。'
        summary = {'type': 'thinking', 'thinking': '已收到摘要。\n继续核对。', 'signature': 'summary-signature'}
        redacted = {'type': 'redacted_thinking', 'data': 'redacted-opaque+/='}
        expected = [omitted, {'type': 'text', 'text': command_text}, redacted,
                    {'type': 'tool_use', 'id': 'read-1', 'name': 'read', 'input': {'path': 'source.txt'}},
                    {'type': 'text', 'text': '再检查指定行。'},
                    summary, {'type': 'tool_use', 'id': 'read-2', 'name': 'read',
                              'input': {'path': 'source.txt', 'offset': 1, 'limit': 1}}]
        frames = block_events(0, {'type': 'thinking', 'thinking': '', 'signature': ''}, [
            {'type': 'thinking_delta', 'thinking': ''},
            {'type': 'signature_delta', 'signature': 'opaque-A'},
            {'type': 'signature_delta', 'signature': '+/=B'}])
        frames += block_events(1, {'type': 'text', 'text': ''}, [{'type': 'text_delta', 'text': command_text}])
        frames += block_events(2, redacted)
        frames += block_events(3, {**expected[3], 'input': {}}, [
            {'type': 'input_json_delta', 'partial_json': '{"path":'},
            {'type': 'input_json_delta', 'partial_json': '"source.txt"}'}])
        frames += block_events(4, {'type': 'text', 'text': ''}, [
            {'type': 'text_delta', 'text': expected[4]['text']}])
        frames += block_events(5, {'type': 'thinking', 'thinking': '', 'signature': ''}, [
            {'type': 'thinking_delta', 'thinking': '已收到摘要。\n'},
            {'type': 'thinking_delta', 'thinking': '继续核对。'},
            {'type': 'signature_delta', 'signature': 'summary-signature'}])
        frames += block_events(6, {**expected[6], 'input': {}}, [
            {'type': 'input_json_delta', 'partial_json': json.dumps(expected[6]['input'])}])
        frames += finish('tool_use')
        requests = []
        runtime = []
        tool_snapshots = []

        def observe(event):
            runtime.append(event)
            if event['type'] in ('tool_started', 'tool_completed') and event.get('channel') == 'native':
                tool_snapshots.append(json.loads(json.dumps(agent.to_snapshot())))

        def receive(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, stream=EventStream(frames))

        agent = await self.agent(receive, events=observe)
        agent.messages = [{'role': 'user', 'content': '准备文件并读取两次。'}]
        self.assertTrue(await agent._run_one_turn())
        original = copy.deepcopy(agent.messages)
        assistant = next(m for m in original if m['role'] == 'assistant')
        self.assertEqual(assistant['content'], expected, [event for event in runtime
                         if event['type'] in ('parser_error', 'tool_completed', 'stream_finished')])
        self.assertTrue((Path(self.directory.name) / 'created.txt').exists())
        results = next(m['content'] for m in original if isinstance(m.get('content'), list)
                       and m['role'] == 'user')
        self.assertEqual([r['tool_use_id'] for r in results], ['read-1', 'read-2'])
        self.assertTrue(all('actual file contents' in r['content'] for r in results))
        self.assertNotIn('opaque-A', json.dumps(runtime))
        self.assertNotIn('redacted-opaque', json.dumps(runtime))
        visible = ''.join(event['text'] for event in runtime if event['type'] == 'text_delta')
        self.assertNotIn('<tf-touch', visible)
        self.assertIn('再检查指定行。', visible)
        self.assertEqual(len(tool_snapshots), 4)
        for snapshot in tool_snapshots:
            retained = next(m['content'] for m in snapshot['messages'] if m['role'] == 'assistant')
            self.assertEqual(retained, [block for block in expected if block['type'] != 'tool_use'])

        agent.config.compaction.keep_recent_messages = 1
        self.assertTrue(agent.compact(force=True).changed)
        original = copy.deepcopy(agent.messages)

        def receive_restored(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, stream=EventStream(
                block_events(0, {'type': 'text', 'text': ''},
                             [{'type': 'text_delta', 'text': '核对完成。'}]) + finish()))

        restored = await self.agent(receive_restored)
        store = SessionStore(Path(self.directory.name) / 'session.json')
        store.save(agent.to_snapshot(), history=False)
        restored.load_snapshot(store.load())
        self.assertFalse(await restored._run_one_turn())
        sent = next(m for m in requests[1]['messages'] if m['role'] == 'assistant')
        self.assertEqual(sent['content'], expected)
        self.assertEqual(agent.messages, original)
        restored.config.provider.format = 'openai'
        self.assertNotIn('opaque-A', json.dumps(restored.build_request_body('')))
        restored.config.provider.format = 'anthropic'
        self.assertEqual(next(m for m in restored.build_request_body('')['messages']
                              if m['role'] == 'assistant')['content'], expected)

    async def test_cancel_during_native_execution_keeps_signed_prefix_once(self):
        signed = {'type': 'thinking', 'thinking': '', 'signature': 'native-cancel-signature'}
        frames = block_events(0, signed) + block_events(1, {
            'type': 'tool_use', 'id': 'cancel-read', 'name': 'read', 'input': {'path': 'source.txt'}})
        frames += finish('tool_use')

        def cancel_tool(event):
            if event['type'] == 'tool_started' and event.get('channel') == 'native':
                asyncio.current_task().cancel()

        agent = await self.agent(lambda request: httpx.Response(200, stream=EventStream(frames)),
                                 events=cancel_tool)
        task = asyncio.create_task(agent.run('读取文件。'))
        with self.assertRaises(asyncio.CancelledError):
            await task
        snapshot = json.loads(json.dumps(agent.to_snapshot()))
        self.assertEqual([m['content'] for m in snapshot['messages'] if m['role'] == 'assistant'], [[signed]])
        self.assertEqual([record.status for record in agent.context.records], ['cancelled'])

    async def test_closed_invalid_native_arguments_keep_existing_failure_receipt(self):
        signed = {'type': 'thinking', 'thinking': '', 'signature': 'invalid-input-signature'}
        for raw_input in ('{bad', '[1]'):
            with self.subTest(raw_input=raw_input):
                frames = block_events(0, signed) + block_events(1, {
                    'type': 'tool_use', 'id': 'invalid', 'name': 'touch', 'input': {}},
                    [{'type': 'input_json_delta', 'partial_json': raw_input}]) + finish('tool_use')
                requests = []

                def receive(request):
                    requests.append(json.loads(request.content))
                    response = frames if len(requests) == 1 else block_events(0, {
                        'type': 'text', 'text': ''}, [{'type': 'text_delta', 'text': '参数错误已收到。'}]) + finish()
                    return httpx.Response(200, stream=EventStream(response))

                agent = await self.agent(receive)
                agent.messages = [{'role': 'user', 'content': '检查工具参数。'}]
                self.assertTrue(await agent._run_one_turn())
                self.assertEqual([record.status for record in agent.context.records], ['failed'])
                self.assertIn('Invalid tool arguments', agent.context.records[0].error)
                self.assertFalse(await agent._run_one_turn())
                assistant = next(m for m in requests[1]['messages'] if m['role'] == 'assistant')
                self.assertEqual(assistant['content'], [signed, {
                    'type': 'tool_use', 'id': 'invalid', 'name': 'touch', 'input': {}}])
                result = next(block for m in requests[1]['messages'] if isinstance(m['content'], list)
                              for block in m['content'] if block['type'] == 'tool_result')
                self.assertTrue(result['is_error'])
                self.assertIn('Invalid tool arguments', result['content'])

    async def test_boundaries_replay_only_closed_blocks_and_do_not_execute_native_batch(self):
        complete = [{'type': 'thinking', 'thinking': '', 'signature': 'complete-signature'},
                    {'type': 'redacted_thinking', 'data': 'complete-redacted'}]
        for boundary in ('cancel', 'length', 'transport', 'error', 'need_result'):
            with self.subTest(boundary=boundary):
                frames = block_events(0, complete[0]) + block_events(1, complete[1])
                frames += block_events(2, {'type': 'tool_use', 'id': 'unexecuted', 'name': 'touch',
                                          'input': {'path': 'must-not-exist.txt'}})
                if boundary == 'need_result':
                    frames += block_events(3, {'type': 'text', 'text': ''}, [{
                        'type': 'text_delta', 'text': '<tf-read id="2" path="source.txt" need_result="true" />\n'}],
                        closed=False)
                else:
                    frames += block_events(3, {'type': 'thinking', 'thinking': '', 'signature': ''}, [
                        {'type': 'thinking_delta', 'thinking': 'unfinished-summary'},
                        {'type': 'signature_delta', 'signature': 'unfinished-signature'}], closed=False)
                if boundary == 'length':
                    frames += finish('max_tokens')
                elif boundary == 'error':
                    frames += [{'type': 'error', 'error': {'message': 'fixture stream error'}}]
                stream = EventStream(frames, boundary)
                agent = await self.agent(lambda request: httpx.Response(200, stream=stream))
                agent.messages = [{'role': 'user', 'content': '检查中断恢复。'}]
                if boundary == 'cancel':
                    task = asyncio.create_task(agent.run('开始。'))
                    await asyncio.wait_for(stream.waiting.wait(), 2)
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                else:
                    await agent._run_one_turn()
                if boundary == 'need_result':
                    self.assertEqual(agent.usage.turns[-1].abort_reason, 'need_result')
                    self.assertEqual([(record.id, record.tool, record.status) for record in agent.context.records],
                                     [('2', 'read', 'success')])
                self.assertTrue(stream.closed)
                self.assertFalse((Path(self.directory.name) / 'must-not-exist.txt').exists())
                self.assertFalse(any(record.id == 'unexecuted' for record in agent.context.records))
                snapshot = json.loads(json.dumps(agent.to_snapshot()))
                resumed_requests = []

                def resume(request):
                    resumed_requests.append(json.loads(request.content))
                    return httpx.Response(200, stream=EventStream(block_events(
                        0, {'type': 'text', 'text': ''},
                        [{'type': 'text_delta', 'text': '已恢复。'}]) + finish()))

                restored = await self.agent(resume)
                restored.load_snapshot(snapshot)
                self.assertFalse(await restored._run_one_turn())
                sent = resumed_requests[0]['messages']
                self.assertEqual([m['content'] for m in sent if m['role'] == 'assistant'], [complete])
                self.assertNotIn('unfinished-', json.dumps(sent))
                self.assertNotIn('unexecuted', json.dumps(sent))


if __name__ == '__main__':
    unittest.main()
