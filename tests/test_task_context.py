"""Stage context survives the existing plan, compaction and session paths."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.agent_loop import AgentConfig, AgentLoop
from src.compaction import CompactionConfig
from src.desktop_service import DesktopService
from src.parser import Command
from src.provider import ProviderConfig
from src.session import SessionStore
from src.task_plan import validate_plan


def plan():
    return {'explanation': 'Continue the implementation.', 'steps': [{
        'id': 'view', 'title': 'Connect the view', 'acceptance': 'Search displays results',
        'status': 'in_progress', 'evidence': [],
    }]}


def stage_context():
    return {
        'summary': 'Loading is complete; connect the existing list component.',
        'decisions': ['Keep the local index: the service has no query endpoint.'],
        'files': [{'path': 'src/store.py', 'note': 'load_records is the data entry point'}],
    }


def add_history(agent):
    for index in range(8):
        agent.messages.extend([
            {'role': 'assistant', 'content': f'Investigated section {index}. ' + 'details ' * 150},
            {'role': 'user', 'content': f'Observation {index}. ' + 'result ' * 150},
        ])


class TaskContextValidationTests(unittest.TestCase):
    def test_legacy_shape_and_optional_context_update_semantics(self):
        original = plan()
        self.assertEqual(validate_plan(original), original)
        previous = validate_plan({**original, 'context': stage_context()})
        preserved = validate_plan(original, previous=previous)
        self.assertEqual(preserved['context'], previous['context'])
        preserved['context']['files'][0]['note'] = 'branch only'
        self.assertNotEqual(preserved['context'], previous['context'])
        self.assertNotIn('context', original)
        replacement = {'summary': 'Use the new query endpoint.'}
        self.assertEqual(validate_plan({**original, 'context': replacement}, previous=previous)['context'],
                         replacement)
        self.assertEqual(validate_plan({**original, 'context': {}}, previous=previous)['context'], {})

    def test_context_json_contract_rejects_invalid_fields_without_mutating_previous(self):
        previous = {**plan(), 'context': stage_context()}
        saved = copy.deepcopy(previous)
        invalid = [None, [], {'next_steps': []}, {'summary': 1}, {'summary': 'x' * 2001},
                   {'decisions': 'text'}, {'decisions': [1]}, {'decisions': ['x'] * 11},
                   {'decisions': ['x' * 501]}, {'files': {}}, {'files': [{}] * 11},
                   {'files': [{'path': 'src/store.py'}]},
                   {'files': [{'path': '', 'note': 'empty path'}]},
                   {'files': [{'path': 'x' * 1001, 'note': ''}]},
                   {'files': [{'path': 'src/store.py', 'note': 1}]},
                   {'files': [{'path': 'src/store.py', 'note': 'x' * 501}]},
                   {'files': [{'path': 'src/store.py', 'note': '', 'verified': True}]}]
        for context in invalid:
            with self.subTest(context=str(context)[:80]):
                with self.assertRaises(ValueError):
                    validate_plan({**plan(), 'context': context}, previous=previous)
                self.assertEqual(previous, saved)

    def test_context_does_not_supply_completion_evidence(self):
        candidate = {**plan(), 'context': stage_context()}
        candidate['steps'][0]['status'] = 'completed'
        with self.assertRaisesRegex(ValueError, '完成项必须引用成功工具回执'):
            validate_plan(candidate, records=[])


class TaskContextRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_parent = Path(__file__).resolve().parents[1] / 'temp' / 'context-research-20261113'
        self.temp_parent.mkdir(parents=True, exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(dir=self.temp_parent, prefix='test-')
        self.root = Path(self.directory.name)
        self.agents = []
        self.requests = []

    async def asyncTearDown(self):
        for agent in self.agents:
            await agent.close()
        self.directory.cleanup()
        if not any(self.temp_parent.iterdir()):
            self.temp_parent.rmdir()

    async def new_agent(self, provider='openai', native=False):
        agent = AgentLoop(AgentConfig(
            cwd=str(self.root), provider=ProviderConfig(
                base_url='https://fixture.invalid', model='fixture-model', format=provider,
                enable_native_tools=native, max_tokens=1024),
            compaction=CompactionConfig(keep_recent_messages=4, max_summary_chars=3000)),
            event_sink=lambda _: None)
        await agent.client.aclose()

        def unexpected_request(request):
            self.requests.append(request)
            raise AssertionError('State updates and compaction must not call a model')

        agent.client = httpx.AsyncClient(base_url='https://fixture.invalid',
                                       transport=httpx.MockTransport(unexpected_request))
        self.agents.append(agent)
        return agent

    async def test_compaction_persisted_resume_and_branch_keep_current_context(self):
        agent = await self.new_agent()
        store = SessionStore(str(self.root / 'session.json'), cwd=str(self.root))
        store.bind(agent)
        request = 'Implement search, keeping the existing data contract.'
        agent.conversation.record_request(0, request, request)
        agent.messages.append({'role': 'user', 'content': request})
        candidate = {**plan(), 'context': stage_context()}
        await agent._update_plan_tool(candidate)
        # Exercise the plan_updated persistence hook, before an explicit final save.
        self.assertEqual(store.load()['task_plan'], candidate)
        for _ in range(2):
            add_history(agent)
            self.assertTrue(agent.compact(force=True).changed)
            body = json.dumps(agent.build_request_body(''), ensure_ascii=False)
            for text in (request, candidate['context']['decisions'][0], 'src/store.py'):
                self.assertIn(text, body)
        self.assertNotIn(candidate['context']['decisions'][0], json.dumps(agent.messages))
        store.save(agent.to_snapshot())
        restored = await self.new_agent()
        branch = await self.new_agent()
        saved = store.load()
        restored.load_snapshot(saved)
        branch.load_snapshot(saved)
        self.assertEqual(restored.task_plan, agent.task_plan)
        self.assertEqual(branch.build_request_body(''), restored.build_request_body(''))
        await branch._update_plan_tool({**plan(), 'context': {'summary': 'Branch decision'}})
        self.assertNotIn(candidate['context']['decisions'][0], json.dumps(branch.build_request_body('')))
        self.assertEqual(restored.task_plan, candidate)
        self.assertEqual(agent.task_plan, candidate)
        self.assertEqual(saved['task_plan'], candidate)
        self.assertIn(request, restored.conversation.recall('request:1'))
        self.assertEqual(self.requests, [])

    async def test_current_context_reaches_each_provider_mode(self):
        for provider in ('openai', 'anthropic'):
            for native in (False, True):
                with self.subTest(provider=provider, native=native):
                    agent = await self.new_agent(provider, native)
                    agent.messages = [{'role': 'user', 'content': 'Continue.'}]
                    candidate = {**plan(), 'context': stage_context()}
                    await agent._update_plan_tool(candidate)
                    body = json.dumps(agent.build_request_body(''))
                    self.assertIn(candidate['context']['decisions'][0], body)
                    await agent._update_plan_tool(plan())
                    self.assertEqual(json.dumps(agent.build_request_body('')), body)
                    await agent._update_plan_tool({**plan(), 'context': {}})
                    self.assertNotIn(candidate['context']['decisions'][0], json.dumps(agent.build_request_body('')))

    async def test_legacy_request_bodies_are_unchanged_in_each_provider_mode(self):
        for provider in ('openai', 'anthropic'):
            for native in (False, True):
                with self.subTest(provider=provider, native=native):
                    agent = await self.new_agent(provider, native)
                    agent.messages = [{'role': 'user', 'content': 'Continue the existing task.'}]
                    agent.task_plan = plan()
                    before = json.dumps(agent.build_request_body(''), ensure_ascii=False)
                    await agent._update_plan_tool(plan())
                    self.assertEqual(json.dumps(agent.build_request_body(''), ensure_ascii=False), before)
                    snapshot = agent.to_snapshot()
                    self.assertNotIn('context', snapshot['task_plan'])
                    restored = await self.new_agent(provider, native)
                    restored.load_snapshot(snapshot)
                    self.assertEqual(json.dumps(restored.build_request_body(''), ensure_ascii=False), before)

    async def test_navigation_does_not_grant_read_baseline(self):
        agent = await self.new_agent()
        path = self.root / 'source.py'
        path.write_text('original = 1\n', encoding='utf-8')
        await agent._update_plan_tool({**plan(), 'context': {
            'files': [{'path': str(path), 'note': 'Already read and ready to change'}]}})
        result = await agent._execute_command(Command(
            '1', 'edit', path=str(path), old_text='original = 1', new_text='original = 2'))
        self.assertFalse(result.success)
        self.assertIn('read_required', result.error)
        self.assertEqual(path.read_text(encoding='utf-8'), 'original = 1\n')

    async def test_desktop_resume_and_fork_use_existing_plan_persistence(self):
        service = DesktopService()
        workspace = self.root / 'workspace'
        workspace.mkdir()
        try:
            with patch('src.desktop_service.resolve_system_prompt', return_value='Local test only.'):
                await service.dispatch('initialize', {'data_dir': str(self.root / 'desktop'), 'config': {
                    'base_url': 'https://fixture.invalid', 'model': 'fixture-model',
                    'interfaces': {'skills': {'enabled': False}, 'web': {'enabled': False}},
                }})
                await service.dispatch('open_workspace', {'cwd': str(workspace)})
                original_id = service.session_id
                candidate = {**plan(), 'context': stage_context()}
                await service.agent._update_plan_tool(candidate)
                await service.dispatch('resume_session', {'session_id': original_id})
                self.assertEqual(service.agent.task_plan, candidate)
                await service.dispatch('fork_session')
                branch_id = service.session_id
                self.assertNotEqual(branch_id, original_id)
                self.assertEqual(service.agent.task_plan, candidate)
                await service.agent._update_plan_tool({**plan(), 'context': {}})
                await service.dispatch('resume_session', {'session_id': original_id})
                self.assertEqual(service.agent.task_plan, candidate)
                await service.dispatch('resume_session', {'session_id': branch_id})
                self.assertEqual(service.agent.task_plan['context'], {})
        finally:
            await service.close()


if __name__ == '__main__':
    unittest.main()
