import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.agent_loop import AgentLoop, AgentConfig
from src.context import ContextManager
from src.desktop_service import DesktopService
from src.executor import ExecutionResult
from src.parser import Command
from src.provider import ProviderConfig
from src.usage_tracker import SessionUsage, TurnUsage
from src.interfaces import ExternalInterfaces, InterfaceConfig, CustomToolConfig


class MetadataTests(unittest.TestCase):
    def test_missing_usage_and_legacy_estimates_are_not_measured_savings(self):
        usage = SessionUsage()
        usage.add_turn(TurnUsage(turn=1, commands_executed=3))
        usage.add_turn(TurnUsage(turn=2, commands_executed=4, delayed_successes=2, usage_reported=True))
        restored = SessionUsage.from_dict(usage.to_dict())
        self.assertEqual(restored.estimated_saved_api_calls, 2)
        self.assertFalse(restored.turns[0].usage_reported)
        self.assertTrue(restored.turns[1].usage_reported)

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


class AnthropicUsageTests(unittest.IsolatedAsyncioTestCase):
    async def test_extension_output_is_drained_with_bounded_retention(self):
        with tempfile.TemporaryDirectory(prefix="thinkflow-output-") as temp:
            interfaces = ExternalInterfaces(InterfaceConfig(), cwd=temp)
            tool = CustomToolConfig(name="large_output", description="isolated output limit test", parameters={}, command=[sys.executable, "-c",
                "import sys;sys.stdout.write('x'*2097152);sys.stderr.write('y'*2097152)"])
            output = await interfaces.run_custom_tool(tool, {})
            self.assertLess(len(output), 81000)
            self.assertIn("THINKFLOW TRUNCATED", output)

    async def test_usage_start_and_delta_are_combined(self):
        class Response:
            async def aiter_lines(self):
                for data in (
                    {"type": "message_start", "message": {"usage": {"input_tokens": 10, "cache_read_input_tokens": 20}}},
                    {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 7}},
                ):
                    yield "data: " + json.dumps(data)
        agent = AgentLoop(AgentConfig(provider=ProviderConfig(format="anthropic")), event_sink=lambda _: None)
        agent.usage.add_turn(TurnUsage(turn=1))
        try:
            _ = [event async for event in agent._process_stream(Response())]
            turn = agent.usage.turns[0]
            self.assertEqual((turn.prompt_tokens, turn.completion_tokens, turn.cached_tokens), (30, 7, 20))
            self.assertTrue(turn.usage_reported)
        finally:
            await agent.close()


if __name__ == "__main__":
    unittest.main()
