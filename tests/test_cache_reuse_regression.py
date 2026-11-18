import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.agent_loop import AgentConfig, AgentLoop
from src.cli import render_status
from src.compaction import CompactionConfig
from src.provider import ProviderConfig
from src.usage_tracker import SessionUsage, TurnUsage


class RequestPrefixRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_append_and_post_compaction_requests_keep_the_wire_prefix(self):
        for protocol in ("openai", "anthropic"):
            with self.subTest(protocol=protocol):
                agent = AgentLoop(AgentConfig(
                    cwd=str(Path(__file__).resolve().parents[1]),
                    compaction=CompactionConfig(keep_recent_messages=2, max_summary_chars=300),
                    provider=ProviderConfig(
                        base_url="http://127.0.0.1:1", model="fixture",
                        format=protocol, max_tokens=2048, enable_native_tools=True,
                    ),
                ), event_sink=lambda _: None)
                try:
                    agent.messages = [
                        {"role": "user", "content": f"history-{index} " + "x" * 240}
                        for index in range(4)
                    ]
                    system = agent.build_system_prompt()
                    first = agent.build_request_body(system)
                    agent.messages = agent._messages_with_current_runtime()

                    agent.messages.append({"role": "user", "content": "ordinary append"})
                    appended = agent.build_request_body(system)
                    self.assertEqual(
                        appended["messages"][:len(first["messages"])], first["messages"]
                    )
                    self.assertEqual(
                        {key: value for key, value in appended.items() if key != "messages"},
                        {key: value for key, value in first.items() if key != "messages"},
                    )
                    agent.messages = agent._messages_with_current_runtime()

                    self.assertTrue(agent.compact(force=True).changed)
                    compacted = agent.build_request_body(system)
                    self.assertNotEqual(compacted["messages"], appended["messages"])
                    agent.messages = agent._messages_with_current_runtime()
                    agent.messages.append({"role": "user", "content": "after compaction"})
                    later = agent.build_request_body(system)
                    self.assertEqual(
                        later["messages"][:len(compacted["messages"])], compacted["messages"]
                    )
                    self.assertEqual(
                        {key: value for key, value in later.items() if key != "messages"},
                        {key: value for key, value in compacted.items() if key != "messages"},
                    )
                finally:
                    await agent.client.aclose()


class UsageAccountingRegressionTests(unittest.TestCase):
    def render_cache_status(self, usage):
        config = AgentConfig(cwd=str(Path(__file__).resolve().parents[1]))
        agent = SimpleNamespace(
            message_stats=lambda: {
                "messages": 0,
                "chars": 0,
                "compactions": 0,
                "last_input_tokens_estimate": None,
            },
            config=config,
            executor=SimpleNamespace(cwd=config.cwd),
            usage=usage,
        )
        with patch("src.cli.renderer.console") as console:
            render_status(agent)
        return "\n".join(
            str(call.args[0]) for call in console.print.call_args_list if call.args
        )

    def test_openai_nested_cache_alias_is_counted(self):
        turn = TurnUsage(turn=1)
        turn.observe_usage({
            "input_tokens": 100,
            "input_tokens_details": {"cached_tokens": 60},
            "output_tokens": 4,
        }, final=True)

        self.assertEqual(turn.cached_tokens, 60)
        self.assertEqual(turn.non_cached_input_tokens, 40)
        self.assertTrue(turn.cache_read_reported)

    def test_reported_zero_cache_is_distinct_from_missing_cache_data(self):
        turn = TurnUsage(turn=1)
        turn.observe_usage({
            "prompt_tokens": 25,
            "prompt_tokens_details": {"cached_tokens": 0},
            "completion_tokens": 1,
        }, final=True)

        self.assertEqual(turn.cached_tokens, 0)
        self.assertEqual(turn.non_cached_input_tokens, 25)
        self.assertTrue(turn.cache_read_reported)

    def test_invalid_cache_breakdown_is_not_used_as_negative_input(self):
        for cache_fields in (
            {"prompt_cache_miss_tokens": 12},
            {"prompt_tokens_details": {"cached_tokens": 11}},
        ):
            with self.subTest(cache_fields=cache_fields):
                turn = TurnUsage(turn=1)
                turn.observe_usage({
                    "prompt_tokens": 10,
                    **cache_fields,
                    "completion_tokens": 1,
                }, final=True)

                self.assertEqual(turn.cached_tokens, 0)
                self.assertIsNone(turn.non_cached_input_tokens)
                self.assertFalse(turn.cache_read_reported)

    def test_totals_expose_cache_denominator_rate_and_coverage(self):
        usage = SessionUsage()
        complete = TurnUsage(
            turn=1, prompt_tokens=100, cached_tokens=60,
            non_cached_input_tokens=40, input_reported=True,
            cache_read_reported=True, http_attempts=1, successful_responses=1,
        )
        partial = TurnUsage(
            turn=2, prompt_tokens=50, input_reported=True,
            http_attempts=1, successful_responses=1,
        )
        usage.add_turn(complete)
        usage.add_turn(partial)

        totals = usage.totals()
        self.assertEqual(totals["cache_denominator_tokens"], 100)
        self.assertEqual(totals["cache_observed_turns"], 1)
        self.assertAlmostEqual(totals["cache_hit_ratio"], 0.6)
        self.assertAlmostEqual(totals["cache_coverage"], 100 / 150)
        self.assertEqual(totals["cache_hit_tokens"], 60)
        self.assertEqual(totals["cache_input_tokens"], 150)
        self.assertEqual(totals["cache_unobserved_input_tokens"], 50)
        self.assertFalse(totals["cache_usage_complete"])

    def test_status_renders_observed_rate_denominator_and_coverage_gap(self):
        usage = SessionUsage()
        usage.add_turn(TurnUsage(
            turn=1, prompt_tokens=100, cached_tokens=60, input_reported=True,
            cache_read_reported=True,
        ))
        usage.add_turn(TurnUsage(turn=2, prompt_tokens=50, input_reported=True))

        output = self.render_cache_status(usage)

        self.assertIn("60.0% (60/100 tokens)", output)
        self.assertIn("66.7% (100/150 input tokens; gap 50)", output)

    def test_status_keeps_unknown_rate_distinct_from_zero_coverage(self):
        usage = SessionUsage()
        usage.add_turn(TurnUsage(turn=1, prompt_tokens=25, input_reported=True))

        output = self.render_cache_status(usage)

        self.assertIn("unknown (no cache denominator)", output)
        self.assertIn("0.0% (0/25 input tokens; gap 25)", output)

    def test_status_does_not_render_unknown_usage_as_zero(self):
        output = self.render_cache_status(SessionUsage())

        self.assertIn("unknown (no cache denominator)", output)
        self.assertIn("unknown (no input denominator)", output)
        self.assertNotIn("0.0%", output)

    def test_anthropic_cache_rate_uses_read_write_inclusive_prompt(self):
        turn = TurnUsage(turn=1, http_attempts=1, successful_responses=1)
        turn.observe_usage({
            "input_tokens": 10,
            "cache_read_input_tokens": 20,
            "cache_creation_input_tokens": 5,
            "output_tokens": 2,
        }, anthropic=True, final=True)
        usage = SessionUsage()
        usage.add_turn(turn)

        totals = usage.totals()
        self.assertEqual(totals["prompt_tokens"], 35)
        self.assertEqual(totals["cache_denominator_tokens"], 35)
        self.assertAlmostEqual(totals["cache_hit_ratio"], 20 / 35)
        self.assertEqual(totals["non_cached_input_tokens"], 15)
        self.assertTrue(totals["cache_usage_complete"])

    def test_cache_metrics_are_unknown_without_a_denominator(self):
        totals = SessionUsage().totals()

        self.assertEqual(totals["cache_denominator_tokens"], 0)
        self.assertIsNone(totals["cache_hit_ratio"])
        self.assertIsNone(totals["cache_coverage"])
        self.assertFalse(totals["cache_usage_complete"])

    def test_legacy_turns_keep_cache_completeness_unknown(self):
        usage = SessionUsage()
        usage.add_turn(TurnUsage(
            turn=1, prompt_tokens=100, cached_tokens=60,
            non_cached_input_tokens=40, input_reported=True,
            cache_read_reported=True, http_attempts=1, successful_responses=1,
        ))
        usage.add_turn(TurnUsage(
            turn=2, prompt_tokens=20, cached_tokens=0,
            non_cached_input_tokens=20, input_reported=True,
        ))

        self.assertFalse(usage.totals()["cache_usage_complete"])


if __name__ == "__main__":
    unittest.main()
