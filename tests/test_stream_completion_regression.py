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
    def scripted(self, *streams, protocol='openai', max_auto_continues=0, require_final_response=True):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        requests, events = [], []

        def reply(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, stream=streams[len(requests) - 1])

        client = httpx.AsyncClient(base_url='https://fixture.invalid', transport=httpx.MockTransport(reply))
        with patch.object(AgentLoop, '_new_http_client', return_value=client):
            agent = AgentLoop(AgentConfig(cwd=directory, provider=ProviderConfig(
                model='fixture', format=protocol, max_tokens=1024), max_auto_continues=max_auto_continues,
                max_retries=0, require_final_response=require_final_response), event_sink=events.append)
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

    async def test_progress_without_requested_artifact_is_corrected_once(self):
        def reply(text):
            return Stream(choice({'content': text}, 'stop'), '[DONE]')

        agent, requests, events = self.scripted(
            reply('我先检查。'),
            reply('我现在写入。<tf-write id="1" path="result.txt">42</tf-write>已写入。'),
            reply('我先检查。'), max_auto_continues=1)
        result = Path(agent.config.cwd, 'result.txt')

        def check_result():
            if not result.exists() or result.read_bytes() != b'42':
                return 'result.txt 应包含 42'

        await agent.run('将 42 写入 result.txt', check_completion=check_result)
        self.assertEqual(agent.stopped_reason, 'completed')
        self.assertEqual(result.read_bytes(), b'42')
        self.assertEqual(len(requests), 2)
        self.assertIn('result.txt 应包含 42', requests[1]['messages'][-1]['content'])
        self.assertEqual(sum(e['type'] == 'tool_completed' for e in events), 1)

        await agent.run('只复述：我先检查。')
        self.assertEqual(agent.stopped_reason, 'completed')
        self.assertEqual(len(requests), 3)

    async def test_failed_completion_check_respects_budget_and_preserves_receipt(self):
        for budget, calls in ((0, 1), (8, 2)):
            with self.subTest(budget=budget):
                agent, requests, events = self.scripted(
                    Stream(choice({'content': '<tf-touch id="1" path="partial" />第一步完成。'}, 'stop'), '[DONE]'),
                    Stream(choice({'content': '【答复】我先检查。'}, 'stop'), '[DONE]'),
                    max_auto_continues=budget)
                await agent.run('完成两个步骤', check_completion=lambda: '仍缺第二项产物')
                self.assertEqual(len(requests), calls)
                self.assertTrue(Path(agent.config.cwd, 'partial').is_file())
                self.assertEqual(len(agent.context.records), 1)
                self.assertEqual(agent.stopped_reason, 'incomplete_response')
                self.assertEqual(agent.last_error, '仍缺第二项产物')
                self.assertEqual([e['stopped_reason'] for e in events if e['type'] == 'run_finished'],
                                 ['incomplete_response'])

    async def test_completion_check_only_runs_after_tool_results_reach_model(self):
        for protocol in ('openai', 'anthropic'):
            with self.subTest(protocol=protocol):
                def reply(text):
                    if protocol == 'openai':
                        return Stream(choice({'content': text}, 'stop'), '[DONE]')
                    return Stream({'type': 'content_block_start', 'index': 0,
                                   'content_block': {'type': 'text', 'text': text}},
                                  {'type': 'content_block_stop', 'index': 0}, {'type': 'message_stop'})

                agent, requests, _ = self.scripted(reply('<tf-read id="1" path="ticket.txt" />'),
                                                   reply('42'), protocol=protocol)
                Path(agent.config.cwd, 'ticket.txt').write_bytes(b'42')
                checkpoints = []

                def check_read():
                    checkpoints.append(len(requests))
                    self.assertEqual([(r.tool, r.status) for r in agent.context.records], [('read', 'success')])

                await agent.run('读取 ticket.txt', check_completion=check_read)
                self.assertEqual(checkpoints, [2])
                self.assertEqual(len(requests), 2)
                self.assertEqual(agent.stopped_reason, 'completed')

    async def test_user_steering_supersedes_original_completion_check(self):
        agent, requests, _ = self.scripted(Stream(choice({'content': '我先检查。'}, 'stop'), '[DONE]'),
                                          Stream(choice({'content': '已取消。'}, 'stop'), '[DONE]'))
        commands = [[{'prompt': '取消写入，只确认取消。'}], []]

        async def steer():
            return commands.pop(0)

        agent.steering_source = steer
        await agent.run('写入 result.txt', check_completion=lambda: self.fail('过期的写入要求'))
        self.assertEqual(agent.stopped_reason, 'completed')
        self.assertEqual(len(requests), 2)
        self.assertFalse(Path(agent.config.cwd, 'result.txt').exists())

    async def test_blocked_plan_can_ask_user_before_checking_artifact(self):
        plan = {'explanation': '需要文件名', 'steps': [{'id': 's1', 'title': '写入文件', 'acceptance': '文件存在',
                           'status': 'blocked', 'evidence': []}]}
        question = f'<tf-update_plan id="1">{json.dumps(plan)}</tf-update_plan>请提供文件名。'
        agent, requests, _ = self.scripted(Stream(choice({'content': question}, 'stop'), '[DONE]'))
        await agent.run('写入文件，先问我文件名。', check_completion=lambda: self.fail('正在等用户'))
        self.assertEqual(agent.task_plan['steps'][0]['status'], 'blocked')
        self.assertEqual(len(requests), 1)
        self.assertEqual(agent.stopped_reason, 'awaiting_input')

    async def test_last_turn_does_not_leave_unsent_completion_feedback_for_next_run(self):
        agent, requests, _ = self.scripted(Stream(choice({'content': '我先检查。'}, 'stop'), '[DONE]'),
                                          Stream(choice({'content': '42'}, 'stop'), '[DONE]'),
                                          max_auto_continues=2)
        agent.config.max_run_turns = 1
        await agent.run('写入 result.txt', check_completion=lambda: 'still missing artifact')
        self.assertEqual(agent.stopped_reason, 'max_run_turns')
        await agent.run('只回答 42')
        self.assertNotIn('still missing artifact', json.dumps(requests[1]))
        self.assertEqual(agent.stopped_reason, 'completed')

    async def test_empty_or_reasoning_only_after_tool_is_not_success(self):
        for delta in ({}, {'content': ' \n', 'reasoning_content': 'checking'}):
            with self.subTest(delta=delta):
                agent, requests, events = self.scripted(Stream(native_write('tool_calls'), '[DONE]'),
                                                       Stream(choice(delta, 'stop'), '[DONE]'),
                                                       Stream(choice(delta, 'stop'), '[DONE]'), max_auto_continues=8)
                await agent.run('Write the file and confirm.')
                self.assertEqual(agent.stopped_reason, 'incomplete_response')
                self.assertTrue(agent.last_error)
                self.assertEqual(len(requests), 3)
                self.assertEqual([(r.id, r.status) for r in agent.context.records], [('write-1', 'success')])
                self.assertEqual([e['kind'] for e in events if e['type'] == 'provider_protocol_error'],
                                 ['empty_assistant_response'])

    async def test_native_receipt_is_enough_for_silent_host(self):
        agent, requests, _ = self.scripted(Stream(native_write('tool_calls'), '[DONE]'),
                                          Stream(choice({}, 'stop'), '[DONE]'), require_final_response=False)
        await agent.run('Write the file.')
        self.assertEqual(agent.stopped_reason, 'completed')
        self.assertEqual(len(requests), 2)
        self.assertEqual(len(agent.context.records), 1)

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
