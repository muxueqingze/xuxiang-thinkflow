"""Natural input boundaries, request parity and retained active requirements."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest

import httpx

from src.agent_loop import AgentConfig, AgentLoop
from src.compaction import CompactionConfig
from src.desktop_service import DesktopService
from src.provider import ProviderConfig
from src.session import SessionStore
from test_stream_state import Wire


class SteeringTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_source_preserves_exact_requests_and_budget_keeps_unconsumed_input(self):
        with tempfile.TemporaryDirectory(prefix='tf-steering-parity-') as directory:
            bodies, source_calls = [], []
            async def empty():
                source_calls.append(True)
                return []
            for source in (None, empty):
                agent = AgentLoop(AgentConfig(cwd=directory,
                    provider=ProviderConfig(model='fixture', enable_native_tools=False)),
                    event_sink=lambda _: None, steering_source=source)
                async def request(req):
                    bodies.append(json.loads(req.content))
                    return httpx.Response(200, stream=Wire(['完成。']))
                await agent.client.aclose()
                agent.client = httpx.AsyncClient(transport=httpx.MockTransport(request), base_url='http://fixture')
                try:
                    await agent.run('同一要求')
                    self.assertEqual(agent.usage.totals()['api_calls'], 1)
                finally:
                    await agent.close()
            self.assertEqual(bodies[0], bodies[1])
            self.assertEqual(source_calls, [True])
            source_calls.clear()
            agent = AgentLoop(AgentConfig(cwd=directory, max_run_turns=1,
                provider=ProviderConfig(enable_native_tools=False)), event_sink=lambda _: None, steering_source=empty)
            await agent.client.aclose()
            agent.client = httpx.AsyncClient(transport=httpx.MockTransport(request), base_url='http://fixture')
            try:
                await agent.run('最后一个可用回合')
                self.assertEqual(source_calls, [])
                self.assertEqual(agent.stopped_reason, 'completed')
            finally:
                await agent.close()

    async def test_tool_batch_finishes_before_input_and_two_compactions_keep_all_active_requests(self):
        for protocol in ('openai', 'anthropic'):
            with self.subTest(protocol=protocol), tempfile.TemporaryDirectory(prefix='tf-steering-context-') as directory:
                root = Path(directory)
                requests, events = [], []
                pending = [{'prompt': '补充甲\n[附件快照] 已冻结正文', 'user_request': '补充甲'},
                           {'prompt': '补充乙：保留原接口。'}]
                async def source():
                    if not pending:
                        return []
                    self.assertEqual((root / 'first.txt').read_text(), 'effect')
                    self.assertEqual([(r.tool, r.status) for r in agent.context.records],
                                     [('write', 'success'), ('read', 'success')])
                    incoming, pending[:] = list(pending), []
                    return incoming
                agent = AgentLoop(AgentConfig(cwd=directory, compaction=CompactionConfig(enabled=False),
                    provider=ProviderConfig(format=protocol, model='fixture', enable_native_tools=False, max_tokens=4096)),
                    event_sink=events.append, steering_source=source)
                store = SessionStore(str(root / 'session.json'), cwd=directory)
                store.bind(agent)
                async def request(req):
                    requests.append(json.loads(req.content))
                    text = ('<tf-write id="1" path="first.txt">effect</tf-write>\n'
                            '<tf-read id="2" path="first.txt" />\n' if len(requests) == 1 else '完成。')
                    return httpx.Response(200, stream=Wire([text], protocol))
                await agent.client.aclose()
                agent.client = httpx.AsyncClient(transport=httpx.MockTransport(request), base_url='http://fixture')
                try:
                    await agent.run('主要求：保留全部业务行为。')
                    self.assertEqual(len(requests), 2)
                    second = json.dumps(requests[1], ensure_ascii=False)
                    self.assertEqual(second.count('补充甲'), 1)
                    self.assertEqual(second.count('补充乙'), 1)
                    self.assertEqual(agent.conversation.active_refs, ['request:1', 'request:2', 'request:3'])
                    self.assertIn('已冻结正文', agent.conversation.recall('request:2:input'))
                    self.assertNotIn('已冻结正文', agent.conversation.recall('request:2'))
                    agent.config.compaction = CompactionConfig(keep_recent_messages=2)
                    for _ in range(2):
                        agent.messages.extend({'role': 'assistant', 'content': '旧工作记录 ' * 100} for _ in range(4))
                        agent.compact(force=True)
                        for text in agent.conversation.active_inputs.values():
                            self.assertIn({'role': 'user', 'content': text}, agent.messages)
                    snapshot = agent.to_snapshot()
                    branch = AgentLoop(agent.config, event_sink=lambda _: None)
                    try:
                        branch.load_snapshot(snapshot)
                        self.assertEqual(branch.conversation.active_inputs, agent.conversation.active_inputs)
                        branch.conversation.record_request(len(branch.messages), '新任务', '新任务')
                        self.assertEqual(branch.conversation.active_refs, [])
                        self.assertEqual(len(agent.conversation.active_refs), 3)
                    finally:
                        await branch.close()
                    restored = store.load()
                    self.assertEqual(restored['conversation']['active_refs'], ['request:1', 'request:2', 'request:3'])
                finally:
                    await agent.close()

    async def test_paused_steering_survives_desktop_restart_as_queued_work(self):
        with tempfile.TemporaryDirectory(prefix='tf-steering-restore-') as directory:
            root = Path(directory)
            data, workspace = root / 'data', root / 'workspace'
            workspace.mkdir()
            config = {'base_url': 'http://fixture', 'model': 'fixture', 'security_profile': 'open'}
            service = DesktopService()
            entered, release = asyncio.Event(), asyncio.Event()
            requests = []
            async def hold(_):
                entered.set()
                await release.wait()
            async def request(req):
                requests.append(json.loads(req.content))
                return httpx.Response(200, stream=Wire(['完成。'], after_chunk=hold if len(requests) == 1 else None))
            async def connect(current):
                await current.dispatch('initialize', {'data_dir': str(data), 'config': config})
                await current.dispatch('open_workspace', {'cwd': str(workspace)})
                await current.agent.client.aclose()
                current.agent.client = httpx.AsyncClient(transport=httpx.MockTransport(request), base_url='http://fixture')
            await connect(service)
            try:
                sid = service.session_id
                await service.submit_input({'command_id': 'first', 'prompt': '主任务', 'session_id': sid})
                await asyncio.wait_for(entered.wait(), 3)
                await service.set_queue_paused(True)
                await service.submit_input({'command_id': 'later', 'prompt': '下一个任务', 'session_id': sid})
                params = {'command_id': 'correction', 'prompt': '补充原任务', 'session_id': sid, 'delivery': 'steer'}
                await service.submit_input(params)
                release.set()
                await service.task
                self.assertEqual(len(requests), 1)
                self.assertEqual(service._find_input('correction')['status'], 'queued')
                await service.close()
                service = DesktopService()
                await connect(service)
                self.assertEqual(service.session_id, sid)
                self.assertTrue(service.queue_paused)
                duplicate = await service.submit_input(params)
                self.assertEqual(duplicate['receipt']['status'], 'queued')
                self.assertEqual(len(service.input_commands), 3)
                await service.set_queue_paused(False)
                await service.task
                self.assertEqual(len(requests), 3)
                self.assertNotIn('补充原任务', json.dumps(requests[1], ensure_ascii=False))
                self.assertIn('补充原任务', json.dumps(requests[2], ensure_ascii=False))
                self.assertEqual([item['status'] for item in service.input_commands], ['completed'] * 3)
            finally:
                release.set()
                await service.close()

    async def test_crash_checkpoint_does_not_steer_the_next_queued_task(self):
        with tempfile.TemporaryDirectory(prefix='tf-steering-crash-') as directory:
            root = Path(directory)
            workspace = root / 'workspace'
            workspace.mkdir()
            config = {'base_url': 'http://fixture', 'model': 'fixture'}
            requests, events = [], []
            entered = asyncio.Event()
            async def hold(_):
                entered.set()
                await asyncio.Future()
            async def request(req):
                requests.append(json.loads(req.content))
                return httpx.Response(200, stream=Wire(['运行中'], after_chunk=hold if len(requests) == 1 else None))
            async def connect(current):
                await current.dispatch('initialize', {'data_dir': str(root / 'data'), 'config': config})
                await current.dispatch('open_workspace', {'cwd': str(workspace)})
                await current.agent.client.aclose()
                current.agent.client = httpx.AsyncClient(transport=httpx.MockTransport(request), base_url='http://fixture')
            service = DesktopService(events.append)
            await connect(service)
            try:
                sid = service.session_id
                await service.submit_input({'command_id': 'a', 'prompt': 'TASK_A', 'session_id': sid})
                await asyncio.wait_for(entered.wait(), 3)
                await service.submit_input({'command_id': 'b', 'prompt': 'TASK_B', 'session_id': sid})
                correction = {'command_id': 's', 'prompt': 'CORRECTION_FOR_A', 'session_id': sid, 'delivery': 'steer'}
                await service.submit_input(correction)
                # Capture the real running checkpoint before graceful teardown
                # can settle or demote the queued correction.
                checkpoint, saved_path = service.store.load(), service.store.path
                self.assertEqual(checkpoint['desktop']['input_commands'][0]['status'], 'running')
                original_hash = checkpoint['desktop']['input_commands'][-1]['content_hash']
                await service.close()
                SessionStore(str(saved_path), cwd=str(workspace)).save(checkpoint, history=False)
                service = DesktopService(events.append)
                await connect(service)
                self.assertNotIn('delivery', service._find_input('s'))
                self.assertEqual(service._find_input('s')['content_hash'], original_hash)
                self.assertNotIn('delivery', service.store.load()['desktop']['input_commands'][-1])
                self.assertEqual((await service.submit_input(correction))['receipt']['status'], 'queued')
                await service.set_queue_paused(False)
                await service.task
                self.assertEqual(len(requests), 3)
                self.assertFalse(any(event['type'] == 'steering_applied' for event in events))
                self.assertEqual(service.agent.conversation.active_refs, [])
                self.assertEqual([item['status'] for item in service.input_commands],
                                 ['interrupted', 'completed', 'completed'])
            finally:
                await service.close()


if __name__ == '__main__':
    unittest.main()
