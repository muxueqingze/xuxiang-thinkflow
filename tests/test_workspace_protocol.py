import asyncio
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import httpx

from src.agent_loop import AgentLoop, AgentConfig
from src.provider import ProviderConfig
from src.workspace import WorkspaceView
from src.task_plan import validate_plan
from src.context import ContextManager
from src.executor import ExecutionResult
from src.parser import Command


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.view = WorkspaceView(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_frozen_attachment_refuses_changed_file_and_credentials(self):
        path = self.root / 'main.py'
        path.write_text('print(1)', encoding='utf-8')
        read = self.view.read_file('main.py')
        args = [{'path': 'main.py', 'revision': read['revision']}]
        self.assertIn('print(1)', self.view.prepare_attachments(args))
        path.write_text('print(2)', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '已变化'):
            self.view.prepare_attachments(args)
        path.write_text('api_key="fixture-not-a-real-secret"', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '凭据'):
            self.view.read_file('main.py')

    def test_boundaries_binary_search_and_duplicate_attachment(self):
        (self.root / 'sub').mkdir()
        (self.root / 'sub/a.txt').write_text('hello')
        (self.root / 'image.bin').write_bytes(b'a\x00b')
        with self.assertRaises(ValueError):
            self.view.read_file('image.bin')
        with self.assertRaises(PermissionError):
            self.view.read_file('../outside')
        with self.assertRaises(PermissionError):
            self.view.read_file('.env')
        files = self.view.files(query='a.txt')['entries']
        self.assertEqual([f['path'] for f in files], ['sub/a.txt'])
        args = {'path': 'sub/a.txt', 'revision': hashlib.sha256(b'hello').hexdigest()}
        with self.assertRaisesRegex(ValueError, '重复'):
            self.view.prepare_attachments([args, args])

    def test_completed_plan_requires_successful_receipt(self):
        step = {'id': 'a', 'title': '修复', 'acceptance': '验证通过', 'status': 'completed', 'evidence': ['tool1']}
        value = {'explanation': '', 'steps': [step]}
        with self.assertRaises(ValueError):
            validate_plan(value, records=[])
        context = ContextManager()
        context.record(Command(id='tool1', tool='bash'), ExecutionResult(success=True, tool='bash'))
        self.assertEqual(validate_plan(value, records=context.records), value)
        value['steps'][0]['evidence'] = []
        with self.assertRaises(ValueError):
            validate_plan(value)


class ReasoningProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_reasoning_and_content_stay_with_native_call_across_turns(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'a.txt').write_text('test')
            agent = AgentLoop(AgentConfig(cwd=directory,
                provider=ProviderConfig(base_url='https://fixture.invalid', model='deepseek-flash', thinking_mode='enabled')),
                event_sink=lambda e: None)
            requests = []
            async def handler(request):
                payload = json.loads(request.content)
                requests.append(payload)
                if len(requests) == 1:
                    frames = [
                        {'delta': {'reasoning_content': 'fixture reasoning'}},
                        {'delta': {'content': 'checking'}},
                        {'delta': {'tool_calls': [{'index': 0, 'id': 'read-a', 'function': {'name': 'read', 'arguments': '{"path":"a.txt"}'}}]}},
                        {'delta': {}, 'finish_reason': 'tool_calls'}]
                else:
                    frames = [{'delta': {'reasoning_content': 'fixture followup', 'content': 'done'}},
                              {'delta': {}, 'finish_reason': 'stop'}]
                raw = ''.join('data: ' + json.dumps({'choices': [frame]}) + '\n\n' for frame in frames) + 'data: [DONE]\n\n'
                return httpx.Response(200, text=raw)
            await agent.client.aclose()
            agent.client = httpx.AsyncClient(base_url='https://fixture.invalid', transport=httpx.MockTransport(handler))
            try:
                await agent.run('read file')
                self.assertEqual(len(requests), 2)
                assistant = [m for m in requests[1]['messages'] if m['role'] == 'assistant']
                self.assertEqual(len(assistant), 1)
                self.assertEqual(assistant[0]['reasoning_content'], 'fixture reasoning')
                self.assertEqual(assistant[0]['content'], 'checking')
                self.assertEqual(assistant[0]['tool_calls'][0]['id'], 'read-a')
                self.assertEqual(requests[0]['thinking'], {'type': 'enabled'})
                snapshot = agent.to_snapshot()
                agent.load_snapshot(snapshot)
                self.assertEqual(agent.messages[-1]['reasoning_content'], 'fixture followup')
            finally:
                await agent.close()
