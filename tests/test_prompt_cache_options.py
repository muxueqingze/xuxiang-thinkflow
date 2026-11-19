"""Offline wire contracts for per-provider prompt caching options."""
import json
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import httpx

from src.cli import apply_provider_profile
from src.configuration import Configuration
from src.provider import create_provider
from src.runtime import build_agent_config, create_agent


class PromptCacheOptionsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.root = self.enterContext(TemporaryDirectory())
        self.base = {'model': 'fixture', 'base_url': 'https://offline.invalid', 'max_tokens': 4096,
                     'enable_native_tools': True, 'native_tools': ['read']}
        self.requests = []

        def capture(request):
            self.requests.append(json.loads(request.content))
            return httpx.Response(200)

        client = partial(httpx.AsyncClient, transport=httpx.MockTransport(capture))
        self.enterContext(patch.object(httpx, 'AsyncClient', client))

    def agent(self, config):
        agent = create_agent({**self.base, **config}, 'stable instructions', self.root, event_sink=lambda _: None)
        self.addAsyncCleanup(agent.close)
        return agent

    async def send_agent(self, agent):
        body = agent.build_request_body(agent.build_system_prompt())
        response = await agent._send_stream_request(agent.get_request_path(), body)
        await response.aclose()
        return self.requests[-1]

    async def test_only_explicit_protocol_options_reach_the_wire(self):
        key = ' route:fixture-v1 '
        both = {'prompt_cache_key': key, 'anthropic_prompt_cache': True}
        cases = [('openai', {}, {}), ('anthropic', {}, {}),
                 ('openai', {'prompt_cache_key': ' \t', 'anthropic_prompt_cache': True}, {}),
                 ('openai', {'prompt_cache_key': None}, {}),
                 ('anthropic', {'prompt_cache_key': key, 'anthropic_prompt_cache': False}, {}),
                 ('openai', both, {'prompt_cache_key': key}),
                 ('anthropic', both, {'cache_control': {'type': 'ephemeral'}})]
        for protocol, settings, expected in cases:
            with self.subTest(protocol=protocol, settings=settings):
                agent = self.agent({'provider': protocol, **settings})
                agent.messages = [{'role': 'user', 'content': 'hello'}]
                adapter = create_provider(agent.config.provider)
                self.addAsyncCleanup(adapter.close)
                response = await adapter.stream_create(agent.messages, 'stable instructions')
                await response.aclose()
                for body in (self.requests[-1], await self.send_agent(agent)):
                    self.assertEqual({k: body[k] for k in ('prompt_cache_key', 'cache_control') if k in body}, expected)
                    self.assertNotIn('cache_control', json.dumps(body['messages']))

    async def test_opt_in_preserves_tools_system_and_history_across_append(self):
        for protocol in ('openai', 'anthropic'):
            with self.subTest(protocol=protocol):
                agent = self.agent({'provider': protocol, 'prompt_cache_key': 'fixture', 'anthropic_prompt_cache': True})
                agent.messages = [{'role': 'user', 'content': 'first'}]
                first = await self.send_agent(agent)
                default = self.agent({'provider': protocol})
                default.messages = list(agent.messages)
                field = 'prompt_cache_key' if protocol == 'openai' else 'cache_control'
                expected = 'fixture' if protocol == 'openai' else {'type': 'ephemeral'}
                self.assertEqual(first, {**await self.send_agent(default), field: expected})
                agent.messages = agent._messages_with_current_runtime()
                agent.messages.extend([{'role': 'assistant', 'content': 'answer'}, {'role': 'user', 'content': 'next'}])
                following = await self.send_agent(agent)
                history = first.pop('messages')
                extended = following.pop('messages')
                self.assertGreater(len(extended), len(history))
                self.assertEqual(history, extended[:len(history)])
                self.assertTrue(first['tools'])
                self.assertEqual(first, following)

    async def test_frozen_state_only_appends_changes_and_survives_compaction(self):
        for protocol in ('openai', 'anthropic'):
            with self.subTest(protocol=protocol):
                agent = self.agent({'provider': protocol})
                agent.config.compaction.keep_recent_messages = 1
                agent.messages = [{'role': 'user', 'content': 'Perform the next step.'}]
                plan = {'steps': [], 'explanation': '', 'context': {'summary': 'Use contract A.'}}
                await agent._update_plan_tool(plan)
                first = await self.send_agent(agent)
                # This is the persisted representation used before field deltas.
                agent.messages[0]['_thinkflow_runtime'] = agent.build_runtime_status_message()['content']
                self.assertEqual(await self.send_agent(agent), first)
                for stamp in range(2, 7):
                    agent.context.next_stamp = stamp
                    agent.messages.extend([{'role': 'assistant', 'content': 'Continuing.'},
                                           {'role': 'user', 'content': 'Next step.'}])
                    agent.messages = agent._messages_with_current_runtime()
                    current = await self.send_agent(agent)
                    self.assertEqual(current['messages'][:len(first['messages'])], first['messages'])
                    history = json.dumps(current['messages'], ensure_ascii=False)
                    self.assertEqual(history.count('[CURRENT TASK PLAN]'), 1)
                    self.assertEqual(history.count('[EXECUTION ENVIRONMENT]'), 1)
                    self.assertIn(f'起始戳记：{stamp}', history)
                    first = current
                self.assertTrue(agent.compact(force=True).changed)
                rebuilt = await self.send_agent(agent)
                history = json.dumps(rebuilt['messages'], ensure_ascii=False)
                self.assertEqual(history.count('Use contract A.'), 1)
                self.assertEqual(history.count('[EXECUTION ENVIRONMENT]'), 1)
                agent.messages = agent._messages_with_current_runtime()
                restored = self.agent({'provider': protocol})
                restored.load_snapshot(json.loads(json.dumps(agent.to_snapshot())))
                self.assertEqual(await self.send_agent(restored), rebuilt)
                restored.messages.extend([{'role': 'assistant', 'content': 'Done.'},
                                          {'role': 'user', 'content': 'Continue.'}])
                restored.context.next_stamp += 1
                following = await self.send_agent(restored)
                self.assertEqual(following['messages'][:len(rebuilt['messages'])], rebuilt['messages'])

    async def test_runtime_changes_report_new_values_and_clear_obsolete_ones(self):
        agent = self.agent({'provider': 'openai'})
        agent.messages = [{'role': 'user', 'content': 'start'}]
        agent.native_write_fallback = True
        plan = {'steps': [], 'explanation': '', 'context': {'summary': 'A'}}
        await agent._update_plan_tool(plan)
        agent.messages = agent._messages_with_current_runtime()
        initial = await self.send_agent(agent)
        await agent._update_plan_tool(dict(reversed(plan.items())))
        self.assertEqual(await self.send_agent(agent), initial)
        for summary in ('B', 'A'):
            await agent._update_plan_tool({**plan, 'context': {'summary': summary}})
            agent.messages = agent._messages_with_current_runtime()
            tail = (await self.send_agent(agent))['messages'][-1]['content']
            self.assertEqual(json.loads(tail.split('[CURRENT TASK PLAN]\n')[1])['context']['summary'], summary)
            self.assertNotIn('[EXECUTION ENVIRONMENT]', tail)
        agent.native_write_fallback = False
        agent.executor.cwd = str(Path(self.root) / 'next')
        await agent._update_plan_tool({**plan, 'context': {}})
        agent.messages = agent._messages_with_current_runtime()
        cleared = await self.send_agent(agent)
        tail = cleared['messages'][-1]['content']
        self.assertIn('[EXECUTION ENVIRONMENT]', tail)
        self.assertIn('next', tail)
        self.assertIn('[CURRENT TASK PLAN]\n{"explanation": "", "steps": []}', tail)
        self.assertIn('已恢复常规工具配置', tail)
        self.assertEqual(await self.send_agent(agent), cleared)

    async def test_large_input_reference_follows_the_request_and_survives_stamp_updates(self):
        agent = self.agent({'provider': 'openai'})
        for index in (1, 2):
            source = f'source-{index} ' * 1001
            user_input = f'Process attachment {index}'
            agent.conversation.record_request(len(agent.messages), user_input, source)
            agent.messages.append({'role': 'user', 'content': user_input})
            agent.messages = agent._messages_with_current_runtime()
            body = await self.send_agent(agent)
            self.assertIn(f'tf-python 加 ref="request:{index}:input"', body['messages'][-1]['content'])
            agent.context.next_stamp += 1
            agent.messages = agent._messages_with_current_runtime()
            tail = (await self.send_agent(agent))['messages'][-1]['content']
            self.assertNotIn('tf-python 加 ref=', tail)
            self.assertEqual(agent.conversation.original_text(f'request:{index}:input'), source)

    async def test_selection_switch_and_explicit_clear_do_not_reuse_another_profile_key(self):
        settings = {'active_provider': 'group', 'providers': {
            'group': {**self.base, 'prompt_cache_key': 'shared-fixture'},
            'gateway': self.base,
            'native': {**self.base, 'provider': 'anthropic', 'anthropic_prompt_cache': True},
        }}
        layers = Configuration(Path(self.root), [('fixture', settings)])
        config, _ = layers.resolve(use_env=False)
        agent = self.agent(config)
        self.assertEqual((await self.send_agent(agent))['prompt_cache_key'], 'shared-fixture')
        for profile, expected in zip(agent.config.provider.provider_profiles, (
                {'prompt_cache_key': 'shared-fixture'}, {}, {'cache_control': {'type': 'ephemeral'}})):
            apply_provider_profile(agent, profile, profile.model)
            body = await self.send_agent(agent)
            self.assertEqual({k: body[k] for k in ('prompt_cache_key', 'cache_control') if k in body}, expected)
        layers.layers.append(('override', {'prompt_cache_key': ''}))
        cleared = self.agent(layers.resolve(use_env=False)[0])
        apply_provider_profile(cleared, cleared.config.provider.provider_profiles[0], 'fixture')
        self.assertNotIn('prompt_cache_key', await self.send_agent(cleared))

    async def test_switch_keeps_run_wide_overrides_and_profile_defaults(self):
        layers = Configuration(Path(self.root), [('user', {
            'active_provider': 'one', 'prompt_cache_key': 'group',
            'providers': {'one': self.base, 'two': {**self.base, 'prompt_cache_key': 'two-key'},
                          'native': {**self.base, 'provider': 'anthropic', 'anthropic_prompt_cache': True}},
        })])
        for overrides, expected in (({}, [{'prompt_cache_key': 'group'}, {'prompt_cache_key': 'two-key'},
                                           {'cache_control': {'type': 'ephemeral'}}]),
                                    ({'prompt_cache_key': None, 'anthropic_prompt_cache': False}, [{}, {}, {}])):
            agent = self.agent(layers.resolve(overrides, use_env=False)[0])
            for profile, options in zip(agent.config.provider.provider_profiles, expected):
                apply_provider_profile(agent, profile, profile.model)
                request = await self.send_agent(agent)
                self.assertEqual({k: request[k] for k in ('prompt_cache_key', 'cache_control') if k in request}, options)

    def test_invalid_options_do_not_silently_opt_in(self):
        for field, value in [('anthropic_prompt_cache', 'false'), ('prompt_cache_key', 123)]:
            for config in ({field: value}, {'providers': {'inactive': {field: value}}}):
                with self.subTest(config=config), self.assertRaisesRegex(ValueError, field):
                    build_agent_config(config, system_prompt='', cwd=self.root)
