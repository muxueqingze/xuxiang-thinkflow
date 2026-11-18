"""Provider input estimates and deterministic history retention."""

import sys
import json
import tempfile
import unittest
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.compaction import CompactionConfig, compact_messages
from src.context_budget import estimate_message_tokens, estimate_request_tokens
from src.conversation import ConversationContext
from src.agent_loop import AgentConfig, AgentLoop
from src.provider import ProviderConfig
from src.usage_tracker import TurnUsage
from test_output_recall import SSE


class ContextBudgetTests(unittest.TestCase):
    def test_estimate_counts_serialized_input_fields_and_ignores_generation_options(self):
        body = {
            'model': 'x' * 1000,
            'messages': [{'role': 'user', 'content': '中文'}],
            'system': '规则',
            'tools': [{'name': '读取'}],
            'max_tokens': 20_000,
            'stream': True,
        }
        encoded = '{"messages":[{"role":"user","content":"中文"}],"system":"规则","tools":[{"name":"读取"}]}'
        expected = (len(encoded.encode('utf-8')) + 3) // 4
        self.assertEqual(estimate_request_tokens(body), expected)

    def test_recent_budget_keeps_the_longest_contiguous_suffix(self):
        old = {'role': 'user', 'content': 'old'}
        large = {'role': 'assistant', 'content': 'large ' * 100}
        tail = [
            {'role': 'user', 'content': '短问题'},
            {'role': 'assistant', 'content': '短答复'},
        ]
        config = CompactionConfig.from_config({'compaction': {
            'max_input_tokens': 1000,
            'keep_recent_tokens': sum(map(estimate_message_tokens, tail)),
            'keep_recent_messages': 1,
        }})
        compacted, stats = compact_messages([old, large, *tail], config, force=True)
        self.assertEqual(config.max_input_tokens, 1000)
        self.assertEqual(stats.compacted_messages, 2)
        self.assertEqual(compacted[1:], tail)

        unchanged, stats = compact_messages(tail, config, force=True)
        self.assertFalse(stats.changed)
        self.assertEqual(unchanged, tail)

    def test_oversize_final_result_keeps_its_entire_native_tool_batch(self):
        openai = [
            {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'first'}, {'id': 'second'}]},
            {'role': 'tool', 'tool_call_id': 'first', 'content': 'one'},
            {'role': 'tool', 'tool_call_id': 'second', 'content': 'two'},
        ]
        anthropic = [
            {'role': 'assistant', 'content': [
                {'type': 'tool_use', 'id': 'first'}, {'type': 'tool_use', 'id': 'second'},
            ]},
            {'role': 'user', 'content': [
                {'type': 'tool_result', 'tool_use_id': 'first', 'content': 'one'},
                {'type': 'tool_result', 'tool_use_id': 'second', 'content': 'two'},
            ]},
        ]
        config = CompactionConfig(keep_recent_tokens=1)
        for batch in (openai, anthropic):
            with self.subTest(batch=batch):
                old = {'role': 'user', 'content': 'history'}
                compacted, stats = compact_messages([old, *batch], config, force=True)
                self.assertEqual(stats.compacted_messages, 1)
                self.assertEqual(compacted[1:], batch)

    def test_current_execution_input_is_pinned_and_latest_message_is_kept(self):
        history = ConversationContext()
        original = 'Resolve the issue using the attachment.'
        execution_input = original + '\n\n[Attachment]\n' + '正文' * 1000
        history.record_request(0, original, execution_input)
        tail = {'role': 'assistant', 'content': 'latest ' * 100}
        messages = [{'role': 'user', 'content': execution_input + '\n\n[Observation] output'}, tail]

        compacted, stats = compact_messages(
            messages, CompactionConfig(keep_recent_tokens=1), force=True, history=history,
        )
        self.assertTrue(stats.changed)
        self.assertEqual(compacted[1:], [{'role': 'user', 'content': execution_input}, tail])
        self.assertIn('role=observation | content=[Observation] output', compacted[0]['content'])
        self.assertEqual(history.request_positions, {1: 'request:1'})

    def test_character_pressure_makes_room_between_compactions(self):
        history = ConversationContext()
        config = CompactionConfig()
        messages = []
        boundaries = []
        for i in range(49):
            messages.append({'role': 'assistant', 'content': str(i) + 'x' * 9000})
            messages, stats = compact_messages(messages, config, history=history)
            if stats.changed:
                self.assertLessEqual(stats.after_chars, config.max_chars * 3 // 4)
                boundaries.append(i)
        self.assertGreaterEqual(len(boundaries), 2)
        self.assertTrue(all(b - a >= 5 for a, b in zip(boundaries, boundaries[1:])))

    def test_oversized_active_request_and_steering_leave_a_rolling_window(self):
        history = ConversationContext()
        active = ['attachment with exact contents ' * 12000, 'retain the revised constraint']
        history.record_request(0, active[0], active[0])
        history.record_request(1, active[1], active[1], continuation=True)
        messages = [{'role': 'user', 'content': text} for text in active]
        messages += [{'role': 'assistant', 'content': 'x' * 1000} for _ in range(100)]
        config = CompactionConfig(max_messages=0, max_chars=8000)
        messages, first = compact_messages(messages, config, history=history)
        self.assertTrue(first.changed)
        self.assertGreater(first.after_chars, config.max_chars)
        for _ in range(10):
            messages.append({'role': 'assistant', 'content': 'small update'})
            messages, stats = compact_messages(messages, config, history=history)
            self.assertFalse(stats.changed)
        for text in active:
            self.assertEqual(sum(m['content'] == text for m in messages), 1)

    def test_count_limit_includes_summary_and_disabled_compaction_is_respected(self):
        config = CompactionConfig.from_config({'compaction': {
            'max_messages': 2, 'keep_recent_messages': 2,
        }})
        self.assertEqual((config.max_input_tokens, config.keep_recent_tokens), (0, 0))
        messages = [{'role': 'user', 'content': str(index)} for index in range(4)]
        compacted, stats = compact_messages(messages, config)
        self.assertEqual(stats.compacted_messages, 3)
        self.assertEqual(compacted[1:], messages[-1:])
        self.assertLessEqual(len(compacted), config.max_messages)

        config.enabled = False
        config.max_input_tokens = config.keep_recent_tokens = 1
        unchanged, stats = compact_messages(messages, config)
        self.assertFalse(stats.changed)
        self.assertEqual(unchanged, messages)


class RequestBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='thinkflow-request-budget-')
        self.root = Path(self.temp.name)
        self.events = []
        self.agent = AgentLoop(AgentConfig(cwd=str(self.root),
            provider=ProviderConfig(base_url='https://example.test', model='fixture', api_key=''),
            compaction=CompactionConfig(max_chars=0, max_messages=0, max_summary_chars=500,
                max_item_chars=100, max_input_tokens=2000, keep_recent_tokens=500)),
            event_sink=self.events.append)
        self.requests = []
        def receive(request):
            self.requests.append(json.loads(request.content))
            return httpx.Response(200, stream=SSE('Completed.'))
        await self.agent.client.aclose()
        self.agent.client = httpx.AsyncClient(base_url='https://example.test', transport=httpx.MockTransport(receive))

    async def asyncTearDown(self):
        await self.agent.close()
        self.temp.cleanup()
        self.assertFalse(self.root.exists())

    async def test_budget_uses_final_request_and_keeps_current_input_without_an_extra_call(self):
        self.agent.messages = [{'role': 'assistant', 'content': 'older detail ' * 4000},
                               {'role': 'user', 'content': 'previous phase complete'}]
        current = 'Current complete requirement 中文: preserve this exact instruction.'
        await self.agent.run(current)
        self.assertEqual(len(self.requests), 1)
        self.assertGreater(self.agent.compaction_count, 0)
        self.assertIn(current, json.dumps(self.requests[0], ensure_ascii=False))
        events = [event for event in self.events if event['type'] == 'context_budget']
        self.assertEqual(len(events), 1)
        self.assertGreater(events[0]['before_tokens_estimate'], events[0]['after_tokens_estimate'])
        self.assertEqual(self.agent.last_input_tokens_estimate, estimate_request_tokens(self.requests[0]))

    async def test_projected_read_and_old_usage_do_not_trigger_an_unneeded_compaction(self):
        original = 'retained source\n' * 1800
        await self.agent._handle_traditional_tools([
            {'id': 'write', 'name': 'write', 'input': json.dumps({'path': 'data.txt', 'content': original})},
            {'id': 'read', 'name': 'read', 'input': json.dumps({'path': 'data.txt'})},
        ])
        original_read = self.agent.messages[-1]['content']
        projected = self.agent.build_request_body(self.agent.build_system_prompt())
        self.agent.config.compaction.max_input_tokens = estimate_request_tokens(projected) + 500
        self.agent.usage.add_turn(TurnUsage(turn=0, prompt_tokens=1000000, usage_reported=True))
        await self.agent.run('Continue with the retained source.')
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.agent.compaction_count, 0)
        self.assertIn('Exact same complete content', json.dumps(self.requests[0]))
        self.assertTrue(any(message.get('content') == original_read for message in self.agent.messages))
        self.assertEqual(self.agent.last_input_tokens_estimate, estimate_request_tokens(self.requests[0]))

    async def test_disabled_compaction_respects_input_budget_option(self):
        self.agent.config.compaction.enabled = False
        self.agent.config.compaction.max_input_tokens = 1
        self.agent.messages = [{'role': 'assistant', 'content': 'keep original ' * 1000}]
        await self.agent.run('Finish.')
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.agent.compaction_count, 0)
        self.assertFalse(any(event['type'] == 'context_budget' for event in self.events))


if __name__ == '__main__':
    unittest.main()
