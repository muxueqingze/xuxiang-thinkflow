import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.agent_loop import AgentLoop, AgentConfig
from src.context import ContextManager
from src.desktop_service import DesktopService
from src.executor import ExecutionResult
from src.parser import Command
from src.provider import ProviderConfig
from src.usage_tracker import SessionUsage, TurnUsage
from src.cli import render_status
from src import renderer
from src.interfaces import ExternalInterfaces, InterfaceConfig, CustomToolConfig


class MetadataTests(unittest.TestCase):
    def test_cache_rate_weights_only_inputs_with_observable_hits(self):
        usage = SessionUsage()
        for index, (raw, anthropic) in enumerate((
            ({"input_tokens": 100, "input_tokens_details": {"cached_tokens": 60}}, False),
            ({"prompt_tokens": 300, "prompt_tokens_details": {"cached_tokens": 0}}, False),
            ({"prompt_tokens": 100}, False),
            ({"cache_read_input_tokens": 20}, True),
            ({}, False),
        )):
            turn = TurnUsage(turn=index, http_attempts=1, successful_responses=1)
            turn.observe_usage(raw, anthropic=anthropic)
            usage.add_turn(turn)
        totals = usage.totals()
        self.assertEqual(totals["cached_tokens"], 80)
        self.assertEqual((totals["cache_hit_tokens"], totals["cache_denominator_tokens"]), (60, 400))
        self.assertEqual((totals["cache_hit_ratio"], totals["cache_coverage"]), (0.15, 0.8))
        self.assertEqual(totals["cache_observed_turns"], 2)
        self.assertEqual(SessionUsage.from_dict(usage.to_dict()).totals(), totals)

        snapshot = usage.to_dict()
        for turn in snapshot["turns"]:
            del turn["input_complete"]
        # Earlier versions retained the Responses-style raw field but missed its value.
        snapshot["turns"][0].update(cached_tokens=0, cache_read_reported=False, non_cached_input_tokens=None)
        self.assertEqual(SessionUsage.from_dict(snapshot).totals(), totals)

    def test_restore_partial_stream_with_openai_cache_fields(self):
        for details in ({"prompt_tokens_details": {"cached_tokens": 40}},
                        {"prompt_cache_hit_tokens": 40, "prompt_cache_miss_tokens": 40}):
            with self.subTest(details=details):
                turn = TurnUsage(turn=1, http_attempts=1)
                turn.observe_usage({"input_tokens": 80, **details})
                snapshot = turn.to_dict()
                del snapshot["input_complete"]
                restored = SessionUsage.from_dict({"turns": [snapshot]})
                self.assertEqual(restored.totals()["cache_hit_ratio"], 0.5)
                self.assertEqual(restored.totals()["cache_denominator_tokens"], 80)
                self.assertFalse(restored.turns[0].usage_reported)

    def test_inconsistent_cache_breakdowns_remain_unknown(self):
        for fields in ({"prompt_cache_miss_tokens": 11}, {"prompt_cache_hit_tokens": 11},
                       {"prompt_cache_hit_tokens": 7, "prompt_cache_miss_tokens": 5}):
            with self.subTest(fields=fields):
                turn = TurnUsage(turn=1)
                turn.observe_usage({"prompt_tokens": 10, **fields})
                totals = SessionUsage(turns=[turn]).totals()
                self.assertIsNone(totals["cache_hit_ratio"])
                self.assertIsNone(totals["non_cached_input_tokens"])
                self.assertEqual(totals["cache_coverage"], 0)

    def test_missing_usage_and_legacy_estimates_are_not_measured_savings(self):
        usage = SessionUsage()
        usage.add_turn(TurnUsage(turn=1, commands_executed=3))
        reported = TurnUsage(turn=2, commands_executed=4, delayed_successes=2, http_attempts=1, successful_responses=1)
        reported.observe_usage({"prompt_tokens": 10, "completion_tokens": 2}, final=True)
        usage.add_turn(reported)
        restored = SessionUsage.from_dict(usage.to_dict())
        self.assertEqual(restored.estimated_saved_api_calls, 2)
        self.assertFalse(restored.turns[0].usage_reported)
        self.assertTrue(restored.turns[1].usage_reported)

    def test_legacy_snapshot_keeps_reported_numbers_without_inventing_attempts(self):
        usage = SessionUsage.from_dict({
            "start_time": 1000, "elapsed": 10,
            "turns": [{"turn": 1, "prompt_tokens": 17, "usage_reported": True}],
        })
        totals = usage.totals()
        self.assertEqual((totals["logical_turns"], totals["api_calls"], totals["legacy_turns"]), (1, 0, 1))
        self.assertFalse(totals["usage_complete"])
        self.assertTrue(usage.turns[0].usage_present)
        self.assertFalse(usage.turns[0].usage_reported)
        self.assertEqual(totals["prompt_tokens"], 17)
        self.assertFalse(totals["output_reported"])
        self.assertIsNone(usage.end_time)
        self.assertEqual(SessionUsage.from_dict(usage.to_dict()).elapsed, 10)

    def test_native_recovery_preserves_observation(self):
        context = ContextManager()
        context.record(Command(id="native_1", tool="grep"),
                       ExecutionResult(success=True, tool="grep", content="line 52: missing invariant"), flow="blocking")
        self.assertIn("line 52: missing invariant", context.build_injection())

    def test_large_transcript_has_bounded_state_and_preserves_export(self):
        service = DesktopService()
        service.transcript = [{"role": "assistant", "content": "x" * 40000} for _ in range(150)]
        state = service.state()
        self.assertEqual(state["transcript_window"], {"total": 150, "shown": 120})
        self.assertLess(len(json.dumps(state)), 5000000)
        self.assertEqual(len(service.transcript), 150)
        self.assertEqual(len(service.transcript[0]["content"]), 40000)

    def test_receipt_budget_preserves_hash_and_pending_observation(self):
        context = ContextManager()
        for index in range(10):
            context.record(Command(id=str(index), tool="write", content="a" * 20000, path="file"),
                           ExecutionResult(success=True, tool="write"))
            context.records[-1].injected = 1
        expected_hash = context.records[-1].content_hash
        context.record(Command(id="11", tool="read", path="file"),
                       ExecutionResult(success=True, tool="read", content="must retain"), flow="blocking")
        context.compact_history(keep_records=4, payload_budget=1000)
        self.assertEqual(context.archived_records, 7)
        self.assertEqual(context.records[-2].content_hash, expected_hash)
        self.assertEqual(context.records[-1].stdout, "must retain")
        self.assertLess(sum(len(r.content or "") for r in context.records), 1500)


def sse(data):
    return ("data: " + json.dumps(data) + "\n\n").encode()


class RuntimeUsageTests(unittest.IsolatedAsyncioTestCase):
    async def run_fixture(self, receive, provider_format):
        asyncio.get_running_loop().slow_callback_duration = 2
        agent = AgentLoop(AgentConfig(
            cwd=str(Path(__file__).resolve().parents[1]),
            provider=ProviderConfig(format=provider_format, max_tokens=4096, enable_native_tools=False),
            retry_backoff_seconds=0, max_run_turns=1,
        ), event_sink=lambda _: None)
        await agent.client.aclose()
        agent.client = httpx.AsyncClient(
            base_url="https://fixture.invalid", transport=httpx.MockTransport(receive),
        )
        self.addAsyncCleanup(agent.close)
        await agent.run("Check the requested information.")
        return agent

    async def test_extension_output_is_drained_with_bounded_retention(self):
        with tempfile.TemporaryDirectory(prefix="thinkflow-output-") as temp:
            interfaces = ExternalInterfaces(InterfaceConfig(), cwd=temp)
            tool = CustomToolConfig(name="large_output", description="isolated output limit test", parameters={}, command=[sys.executable, "-c",
                "import sys;sys.stdout.write('x'*2097152);sys.stderr.write('y'*2097152)"])
            try:
                result = await interfaces.run_custom_tool(tool, {})
                self.assertTrue(result.success)
                self.assertTrue(result.truncated)
                self.assertLess(len(result.stdout), 81000)
                self.assertEqual(result.output_sizes["stdout"], 2097152)
            finally:
                interfaces.executor.close()

    async def test_anthropic_usage_requires_final_output_and_known_input_components(self):
        for final, cache_write, expected in ((False, 4, False), (True, None, False), (True, 4, True)):
            with self.subTest(final=final, cache_write=cache_write):
                start = {"input_tokens": 10, "output_tokens": 1, "cache_read_input_tokens": 20}
                if cache_write is not None:
                    start["cache_creation_input_tokens"] = cache_write
                content = sse({"type": "message_start", "message": {"usage": start}})
                content += sse({"type": "content_block_start", "index": 0,
                                "content_block": {"type": "text", "text": ""}})
                content += sse({"type": "content_block_delta", "index": 0,
                                "delta": {"type": "text_delta", "text": "x" * 1399}})
                content += sse({"type": "content_block_stop", "index": 0})
                if final:
                    content += sse({"type": "message_delta", "delta": {"stop_reason": "end_turn"},
                                    "usage": {"output_tokens": 7}})
                    content += sse({"type": "message_stop"})
                agent = await self.run_fixture(lambda _: httpx.Response(200, content=content), "anthropic")
                turn = agent.usage.turns[0]
                totals = agent.usage.totals()
                self.assertEqual((turn.prompt_tokens, turn.completion_tokens, turn.cached_tokens),
                                 (30 + (cache_write or 0), 7 if final else 1, 20))
                self.assertEqual(turn.cache_write_tokens, cache_write)
                self.assertEqual(turn.usage_reported, expected)
                self.assertEqual(totals["usage_complete"], expected)
                self.assertEqual(totals["partial_usage_requests"], int(not expected))
                self.assertEqual(totals["unknown_usage_requests"], 0)
                self.assertEqual(totals["cache_hit_ratio"], None if cache_write is None else 20 / 34)

    async def test_status_displays_cache_breakdown_from_compatible_stream(self):
        for details, expected in (({}, "未知"), ({"cached_tokens": 0}, "0.0%（0/10 tokens）")):
            with self.subTest(details=details):
                packet = {"choices": [], "usage": {
                    "input_tokens": 10, "input_tokens_details": details, "output_tokens": 1,
                }}
                content = sse({"choices": [{"delta": {"content": "Done."}, "finish_reason": "stop"}]})
                content += sse(packet) + b"data: [DONE]\n\n"
                agent = await self.run_fixture(lambda _: httpx.Response(200, content=content), "openai")
                with renderer.console.capture() as capture:
                    render_status(agent)
                self.assertIn("缓存命中率 " + expected, capture.get())

    async def test_retry_attempts_and_usage_survive_session_snapshot(self):
        statuses = iter((503, 503, 200))
        seen = []

        def receive(_):
            status = next(statuses)
            seen.append(status)
            if status != 200:
                return httpx.Response(status, text="busy")
            content = sse({"choices": [{"delta": {"content": "Finished."}, "finish_reason": "stop"}]})
            content += sse({"choices": [], "usage": {
                "prompt_tokens": 22, "completion_tokens": 17, "prompt_cache_hit_tokens": 9,
                "completion_tokens_details": {"reasoning_tokens": 5}, "total_tokens": 39,
            }})
            return httpx.Response(200, content=content + b"data: [DONE]\n\n")

        agent = await self.run_fixture(receive, "openai")
        snapshot = json.loads(json.dumps(agent.to_snapshot()))
        agent.load_snapshot(snapshot)
        usage = agent.usage.to_dict()
        self.assertEqual(usage, snapshot["usage"])
        totals = usage["totals"]
        self.assertEqual((totals["logical_turns"], totals["api_calls"], totals["successful_responses"]), (1, len(seen), 1))
        self.assertEqual(seen, [503, 503, 200])
        self.assertEqual((totals["reported_turns"], totals["partial_usage_requests"], totals["unknown_usage_requests"]),
                         (1, 0, 2))
        self.assertFalse(totals["usage_complete"])
        self.assertEqual((totals["total_tokens"], totals["non_cached_input_tokens"]), (39, 13))
        self.assertEqual(usage["turns"][0]["raw_usage"]["completion_tokens_details"], {"reasoning_tokens": 5})
        run = usage["runs"][0]
        turn = usage["turns"][0]
        self.assertLessEqual(run["start_time"], turn["timestamp"])
        self.assertLessEqual(turn["end_time"], run["end_time"])
        self.assertEqual(usage["elapsed"], run["elapsed"])
        self.assertGreater(usage["elapsed"], 0)

    async def test_feedback_cutoff_does_not_invent_tail_usage(self):
        class Cutoff(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield sse({"choices": [{"delta": {
                    "reasoning_content": '<tf-read id="1" path="README.md" need_result="true" />',
                }}]})
                raise AssertionError("read past the need_result cutoff")

        agent = await self.run_fixture(lambda _: httpx.Response(200, stream=Cutoff()), "openai")
        totals = agent.usage.totals()
        self.assertEqual(agent.usage.turns[0].abort_reason, "need_result")
        self.assertEqual((totals["api_calls"], totals["successful_responses"], totals["unknown_usage_requests"]), (1, 1, 1))
        self.assertFalse(totals["input_reported"])
        self.assertFalse(totals["output_reported"])


if __name__ == "__main__":
    unittest.main()
