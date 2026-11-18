import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from src.agent_loop import AgentConfig, AgentLoop
from src.provider import ProviderConfig


def choice(delta, finish=None):
    return {'choices': [{'delta': delta, 'finish_reason': finish}]}


def native_write(finish):
    return choice({'tool_calls': [{'index': 0, 'id': 'write-1', 'function': {
        'name': 'write', 'arguments': json.dumps({'path': 'result.txt', 'content': 'saved'})}}]}, finish)


class Stream(httpx.AsyncByteStream):
    def __init__(self, *frames):
        self.frames = frames
        self.closed = False

    async def __aiter__(self):
        for frame in self.frames:
            if isinstance(frame, Exception):
                raise frame
            if isinstance(frame, asyncio.Event):
                frame.set()
                await asyncio.Future()
            data = json.dumps(frame) if isinstance(frame, dict) else frame
            yield f'data: {data}\n\n'.encode()

    async def aclose(self):
        self.closed = True


class StreamCompletionTests(unittest.IsolatedAsyncioTestCase):
    def scripted(self, *streams, protocol='openai', max_auto_continues=0):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        requests, events = [], []

        def reply(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, stream=streams[len(requests) - 1])

        client = httpx.AsyncClient(base_url='https://fixture.invalid', transport=httpx.MockTransport(reply))
        with patch.object(AgentLoop, '_new_http_client', return_value=client):
            agent = AgentLoop(AgentConfig(cwd=directory, provider=ProviderConfig(
                model='fixture', format=protocol, max_tokens=1024), max_auto_continues=max_auto_continues,
                max_retries=0), event_sink=events.append)
        self.addAsyncCleanup(agent.close)
        return agent, requests, events

    async def test_disconnect_after_tool_finish_returns_one_receipt_then_short_answer(self):
        tool = Stream(native_write('tool_calls'), httpx.RemoteProtocolError('lost trailer'))
        answer = Stream(choice({'content': 'OK'}, 'stop'), httpx.ReadError('lost trailer'))
        agent, requests, events = self.scripted(tool, answer)
        await agent.run('Write the file and confirm.')
        self.assertEqual(agent.stopped_reason, 'completed')
        self.assertEqual(len(requests), 2)
        self.assertEqual([(m['tool_call_id'], m['role']) for m in requests[1]['messages']
                          if m['role'] == 'tool'], [('write-1', 'tool')])
        self.assertEqual([(r.id, r.status) for r in agent.context.records], [('write-1', 'success')])
        self.assertEqual((Path(agent.config.cwd) / 'result.txt').read_text(), 'saved')
        self.assertEqual(''.join(e['text'] for e in events if e['type'] == 'text_delta'), 'OK')
        self.assertTrue(tool.closed and answer.closed)

    async def test_empty_or_reasoning_only_after_tool_is_not_success(self):
        for delta in ({}, {'content': ' \n', 'reasoning_content': 'checking'}):
            with self.subTest(delta=delta):
                agent, requests, events = self.scripted(Stream(native_write('tool_calls'), '[DONE]'),
                                                       Stream(choice(delta, 'stop'), '[DONE]'), max_auto_continues=8)
                await agent.run('Write the file and confirm.')
                self.assertEqual(agent.stopped_reason, 'error')
                self.assertTrue(agent.last_error)
                self.assertEqual(len(requests), 2)
                self.assertEqual([(r.id, r.status) for r in agent.context.records], [('write-1', 'success')])
                self.assertEqual([e['kind'] for e in events if e['type'] == 'provider_protocol_error'],
                                 ['empty_assistant_response'])

    async def test_anthropic_text_in_block_start_is_a_real_short_answer(self):
        stream = Stream({'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': 'OK'}},
                        {'type': 'content_block_stop', 'index': 0}, {'type': 'message_stop'})
        agent, requests, events = self.scripted(stream, protocol='anthropic')
        await agent.run('Confirm.')
        self.assertEqual(''.join(e['text'] for e in events if e['type'] == 'text_delta'), 'OK')
        self.assertEqual(agent.stopped_reason, 'completed')
        self.assertEqual(len(requests), 1)

    async def test_incomplete_native_batch_never_executes(self):
        for tail, reason in (([], 'error'), ([httpx.ReadError('lost body')], 'max_auto_continues'),
                             ([{'error': {'message': 'upstream failed'}}, '[DONE]'], 'error')):
            with self.subTest(tail=tail):
                agent, requests, _ = self.scripted(Stream(native_write(None), *tail))
                await agent.run('Write the file.')
                self.assertEqual(agent.stopped_reason, reason)
                if tail and isinstance(tail[0], dict):
                    self.assertEqual(agent.last_error, 'upstream failed')
                self.assertEqual(len(requests), 1)
                self.assertEqual(agent.context.records, [])
                self.assertFalse((Path(agent.config.cwd) / 'result.txt').exists())

    async def test_cancel_while_waiting_for_trailer_does_not_execute(self):
        waiting = asyncio.Event()
        stream = Stream(native_write('tool_calls'), waiting)
        agent, requests, _ = self.scripted(stream)
        task = asyncio.create_task(agent.run('Write the file.'))
        try:
            await asyncio.wait_for(waiting.wait(), 3)
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(stream.closed)
        self.assertEqual(agent.stopped_reason, 'cancelled')
        self.assertEqual(len(requests), 1)
        self.assertEqual(agent.context.records, [])
        self.assertFalse((Path(agent.config.cwd) / 'result.txt').exists())
