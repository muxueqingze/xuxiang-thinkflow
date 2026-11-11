"""Long-running requests survive compaction without a summarization API call."""
import asyncio
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.agent_loop import AgentConfig, AgentLoop
from src.compaction import COMPACTION_HEADER, CompactionConfig, compact_messages
from src.conversation import ConversationContext
from src.desktop_service import DesktopService
from src.parser import Command, StreamingParser
from src.provider import ProviderConfig
from src.session import SessionStore


def progress(messages, count=12):
    for index in range(count):
        messages.extend([
            {'role': 'assistant', 'content': f'Completed step {index}: ' + 'output ' * 120},
            {'role': 'user', 'content': f'[Tool observation {index}] ' + 'details ' * 120},
        ])


class CompactionHistoryTests(unittest.TestCase):
    def test_current_request_is_verbatim_once_across_repeated_compaction(self):
        request = 'Start\n' + 'A' * 6000 + '\nKEEP_MIDDLE_CONTRACT\n' + 'Z' * 6000
        messages = [{'role': 'user', 'content': request}]
        history = ConversationContext()
        history.record_request(0, request, request)
        config = CompactionConfig(max_messages=8, keep_recent_messages=4, max_summary_chars=8000)
        progress(messages)
        messages, first = compact_messages(messages, config, history=history)
        self.assertTrue(first.changed)
        self.assertEqual(sum(m.get('content') == request for m in messages), 1)
        first_entry = history.summary_entries[-1]
        progress(messages, 2)
        messages, second = compact_messages(messages, config, force=True, history=history)
        self.assertTrue(second.changed)
        self.assertEqual(sum(m.get('content') == request for m in messages), 1)
        self.assertEqual(messages[0]['content'].count(COMPACTION_HEADER), 1)
        self.assertFalse(any(COMPACTION_HEADER in entry for entry in history.summary_entries))
        self.assertIn(first_entry, history.summary_entries)
        self.assertIn('KEEP_MIDDLE_CONTRACT', history.recall('request:1', 5980, 90))

    def test_old_request_is_referenced_and_latest_revision_keeps_its_order(self):
        original = 'prefix ' * 1000 + 'ORIGINAL_MIDDLE_RULE' + ' suffix' * 1000
        history = ConversationContext()
        messages = [{'role': 'user', 'content': original}]
        history.record_request(0, original, original)
        progress(messages)
        config = CompactionConfig(keep_recent_messages=4, max_summary_chars=8000)
        messages, _ = compact_messages(messages, config, force=True, history=history)
        revision = 'Change only the output name to amended.txt.'
        history.record_request(len(messages), revision, revision)
        messages.append({'role': 'user', 'content': revision})
        progress(messages)
        messages, _ = compact_messages(messages, config, force=True, history=history)
        self.assertEqual([item['text'] for item in history.requests], [original, revision])
        self.assertEqual(sum(m.get('content') == revision for m in messages), 1)
        self.assertNotIn(original, [m.get('content') for m in messages])
        self.assertIn('request:1', messages[0]['content'])
        self.assertIn('request:2', messages[0]['content'])
        self.assertIn('ORIGINAL_MIDDLE_RULE', history.recall('request:1', 6990, 80))
        self.assertIn(revision, history.recall('request:2'))

    def test_recall_ranges_are_exact_and_invalid_ranges_are_explicit(self):
        history = ConversationContext()
        text = '中文\nZoë\n' * 4000
        history.record_request(0, text, text)
        result = history.recall('request:1', 17, 31)
        self.assertEqual(result.split('\n', 1)[1].rsplit('\n[More', 1)[0], text[17:48])
        self.assertIn('offset=48', result)
        for ref, offset, limit in [('request:2', 0, 1), ('bad', 0, 1),
                                   ('request:1', -1, 1), ('request:1', 0, 0),
                                   ('request:1', 0, 200001), ('request:1', True, 1)]:
            with self.subTest(ref=ref, offset=offset, limit=limit), self.assertRaises(ValueError):
                history.recall(ref, offset, limit)


class SSE(httpx.AsyncByteStream):
    def __init__(self, text):
        self.text = text

    async def __aiter__(self):
        yield ('data: ' + json.dumps({'choices': [{'delta': {'content': self.text},
                                                 'finish_reason': 'stop'}]}) + '\n\n').encode()
        yield b'data: [DONE]\n\n'


class ConversationRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 2
        self.directory = tempfile.TemporaryDirectory(prefix='thinkflow-conversation-')
        self.root = Path(self.directory.name)
        self.config = AgentConfig(cwd=str(self.root), provider=ProviderConfig(
            base_url='https://fixture.invalid', model='deepseek-flash'),
            compaction=CompactionConfig(keep_recent_messages=4, max_summary_chars=8000))
        self.agents = []
        self.agent = self.new_agent()

    def new_agent(self):
        agent = AgentLoop(self.config, event_sink=lambda _: None)
        self.agents.append(agent)
        return agent

    async def asyncTearDown(self):
        for agent in self.agents:
            await agent.close()
        self.directory.cleanup()
        self.assertFalse(self.root.exists())

    async def transport(self, agent, receive):
        await agent.client.aclose()
        agent.client = httpx.AsyncClient(base_url='https://fixture.invalid',
                                        transport=httpx.MockTransport(receive))

    async def admit(self, text, *, user_request=None):
        await self.transport(self.agent, lambda _: httpx.Response(200, stream=SSE('Recorded.')))
        await self.agent.run(text, user_request=user_request)

    async def test_snapshot_restore_and_fork_keep_exact_requests_without_extra_requests(self):
        text = 'head ' * 1200 + 'EXACT_RESUME_CONTRACT' + ' tail' * 1200
        await self.admit(text)
        progress(self.agent.messages)
        calls = []
        await self.transport(self.agent, lambda request: calls.append(request))
        self.agent.compact(force=True)
        progress(self.agent.messages, 2)
        self.agent.compact(force=True)
        self.assertEqual(calls, [])
        store = SessionStore(str(self.root / 'session.json'))
        store.save(self.agent.to_snapshot())
        restored = self.new_agent()
        restored.load_snapshot(store.load())
        branch = self.new_agent()
        branch.load_snapshot(store.load())
        self.assertEqual(restored.conversation.requests, self.agent.conversation.requests)
        self.assertIn(text, restored.conversation.recall('request:1', limit=200000))
        branch.conversation.record_request(len(branch.messages), 'branch revision', 'branch revision')
        self.assertEqual(branch.conversation.current_ref, 'request:2')
        self.assertEqual(self.agent.conversation.current_ref, 'request:1')
        self.assertEqual(restored.conversation.current_ref, 'request:1')

    async def test_user_text_is_separate_from_attachment_expansion_and_observations(self):
        original = 'Update the attached implementation.'
        execution = original + '\n\n[Attached snapshot]\nsource = 42'
        await self.admit(execution, user_request=original)
        progress(self.agent.messages)
        self.agent.compact(force=True)
        self.assertEqual(len(self.agent.conversation.requests), 1)
        self.assertEqual(self.agent.conversation.requests[0]['text'], original)
        self.assertIn(execution, [m.get('content') for m in self.agent.messages])
        self.assertNotIn('source = 42', self.agent.conversation.recall('request:1'))
        self.assertNotIn('request:1:input', self.agent.messages[0]['content'])

    async def test_desktop_continue_recalls_frozen_attachment_after_compaction_resume_and_fork(self):
        service = DesktopService()
        workspace = self.root / 'workspace'
        workspace.mkdir()
        file = workspace / 'spec.txt'
        contract = 'FROZEN_MIDDLE_CONTRACT preserve the original version.'
        file.write_bytes(('head ' * 3000 + '\n' + contract + '\n' + ' tail' * 3000).encode())
        revision = hashlib.sha256(file.read_bytes()).hexdigest()
        original = 'Implement the attached specification.'
        requests = []

        def receive(request):
            requests.append(json.loads(request.content))
            text = ('<tf-recall id="1" ref="request:1:input" query="FROZEN_MIDDLE_CONTRACT" limit="120" />'
                    if len(requests) == 3 else 'Recorded.')
            return httpx.Response(200, stream=SSE(text))

        async def submit(command_id, prompt, **kwargs):
            await service.dispatch('submit_input', {'command_id': command_id, 'prompt': prompt,
                                                     'session_id': service.session_id, **kwargs})
            await asyncio.wait_for(service.task, timeout=5)
            self.assertEqual(service.status, 'idle', service.last_error)

        try:
            with patch('src.desktop_service.resolve_system_prompt', return_value='Local test only.'):
                await service.dispatch('initialize', {'data_dir': str(self.root / 'data'), 'config': {
                    'base_url': 'https://fixture.invalid', 'model': 'deepseek-flash', 'enable_native_tools': False,
                    'interfaces': {'skills': {'enabled': False}, 'web': {'enabled': False}},
                }})
                await service.dispatch('open_workspace', {'cwd': str(workspace)})
                initial_id = service.session_id
                await self.transport(service.agent, receive)
                await submit('attachment', original, attachments=[{'path': file.name, 'revision': revision}])
                file.write_bytes(b'CURRENT_DISK_VERSION')
                await submit('interim', 'Continue.')
                service.agent.config.compaction.keep_recent_messages = 4
                for _ in range(2):
                    progress(service.agent.messages, 3)
                    await service.dispatch('compact')
                self.assertEqual(service.agent.compaction_count, 2)
                self.assertEqual(len(requests), 2)
                self.assertIn('request:1:input', service.agent.messages[0]['content'])
                self.assertNotIn(contract, json.dumps(service.agent.messages))

                await service.dispatch('resume_session', {'session_id': initial_id})
                restored = await service.agent.tool_registry.execute('recall', {
                    'ref': 'request:1:input', 'limit': 200000,
                })
                self.assertTrue(restored.success, restored.error)
                frozen_input = str(restored)
                self.assertIn('[Original frozen execution input request:1:input;', frozen_input)
                self.assertIn(f'"path": "{file.name}", "revision": "{revision}"', frozen_input)
                self.assertIn(contract, frozen_input)
                self.assertNotIn('CURRENT_DISK_VERSION', frozen_input)
                execution_input = frozen_input.split('\n', 1)[1]
                exact = service.agent.conversation.recall(
                    'request:1:input', execution_input.index(contract), len(contract),
                )
                self.assertEqual(exact.splitlines()[1], contract)
                self.assertEqual(service.agent.conversation.recall('request:1').split('\n', 1)[1], original)
                with self.assertRaisesRegex(ValueError, 'No separate execution input'):
                    service.agent.conversation.recall('request:2:input')

                await service.dispatch('fork_session')
                self.assertNotEqual(service.session_id, initial_id)
                await self.transport(service.agent, receive)
                await submit('branch-continue', 'Continue from the original specification.')
                self.assertEqual(len(requests), 4)
                self.assertIn('request:1:input', json.dumps(requests[2]['messages']))
                self.assertNotIn(contract, json.dumps(requests[2]['messages']))
                self.assertIn(contract, json.dumps(requests[3]['messages']))
                self.assertNotIn('CURRENT_DISK_VERSION', json.dumps(requests[3]['messages']))
                self.assertEqual(file.read_bytes(), b'CURRENT_DISK_VERSION')
        finally:
            await service.close()

    async def test_recall_is_exposed_after_compaction_and_both_adapters_match(self):
        self.agent.conversation.summary = '[THINKFLOW COMPACTED CONTEXT] legacy snapshot'
        self.assertNotIn('recall', self.agent._enabled_native_tool_names())
        self.agent.conversation.summary = ''
        await self.admit('Original requirement: preserve Unicode 中文.')
        before = self.agent.build_request_body('')
        self.assertNotIn('recall', {t['function']['name'] for t in before['tools']})
        self.assertNotIn('<tf-recall', self.agent.build_system_prompt())
        await self.agent._load_tools({'names': ['recall']})
        self.assertIn('recall', self.agent._enabled_native_tool_names())
        self.assertIn('<tf-recall', self.agent.build_system_prompt())
        self.agent.additional_native_tools.clear()
        progress(self.agent.messages)
        self.agent.compact(force=True)
        self.assertIn('recall', {t['function']['name'] for t in self.agent.build_request_body('')['tools']})
        self.assertIn('<tf-recall', self.agent.build_system_prompt())
        text_command = StreamingParser(allow_legacy_tags=False).feed(
            '<tf-recall id="7" ref="request:1" offset="0" limit="100" />')[0]
        streamed = await self.agent._execute_command(text_command)
        native = await self.agent._execute_traditional_result('recall',
            {'ref': 'request:1', 'offset': 0, 'limit': 100}, tool_id='native-recall')
        self.assertTrue(streamed.success)
        self.assertEqual(streamed.content, native.content)
        self.assertIn(streamed.content, self.agent.context.build_auto_result_message())
        self.config.provider.max_tokens = 1024
        for provider in ('openai', 'anthropic'):
            self.config.provider.format = provider
            await self.agent._handle_traditional_tools([{
                'id': f'provider-{provider}', 'name': 'recall',
                'input': json.dumps({'ref': 'request:1', 'offset': 0, 'limit': 100}),
            }])
            content = self.agent.messages[-1]['content']
            delivered = content if provider == 'openai' else content[0]['content']
            self.assertIn(streamed.content, delivered)
        self.assertIn('recall', {t['name'] for t in self.agent.build_request_body('')['tools']})
        self.config.provider.enable_native_tools = False
        self.assertIn('<tf-recall', self.agent.build_system_prompt())

    async def test_two_recalled_requests_share_one_information_boundary(self):
        await self.admit('First original requirement: ALPHA.')
        await self.admit('Revision: also preserve BETA.')
        progress(self.agent.messages)
        self.agent.compact(force=True)
        self.config.provider.enable_native_tools = False
        requests = []

        def receive(request):
            body = json.loads(request.content)
            requests.append(body)
            if len(requests) == 1:
                text = ('<tf-recall id="1" ref="request:1" />'
                        '<tf-recall id="2" ref="request:2" />')
            else:
                feedback = body['messages'][-1]['content']
                self.assertIn('First original requirement: ALPHA.', feedback)
                self.assertIn('Revision: also preserve BETA.', feedback)
                text = '<tf-write id="3" path="contract.txt">ALPHA BETA</tf-write>Done.'
            return httpx.Response(200, stream=SSE(text))

        await self.transport(self.agent, receive)
        await self.agent.run('Complete the task using both requirements.')
        self.assertEqual(len(requests), 2)
        self.assertEqual((self.root / 'contract.txt').read_text(), 'ALPHA BETA')
        self.assertEqual([(r.tool, r.status) for r in self.agent.context.records],
                         [('recall', 'success'), ('recall', 'success'), ('write', 'success')])

    async def test_recall_identity_includes_reference_and_range(self):
        await self.admit('First')
        await self.admit('Second')
        first = await self.agent._execute_command(Command('1', 'recall', ref='request:1'))
        conflicting = await self.agent._execute_command(Command('1', 'recall', ref='request:2'))
        self.assertTrue(first.success)
        self.assertFalse(conflicting.success)


if __name__ == '__main__':
    unittest.main()
