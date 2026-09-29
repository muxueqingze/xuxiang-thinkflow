"""Offline runtime contracts: no provider requests or real shell processes."""

import asyncio
import contextlib
import io
import json
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.agent_loop import AgentConfig, AgentLoop
from src.executor import ExecutionResult, Executor
from src.parser import Command
from src.security import SecurityPolicy
from src.streaming import EventType, StreamEvent
from src.tool_registry import ToolResult, ToolSpec
from src.interfaces import InterfaceConfig, ImageGenerationConfig, ExternalInterfaces


class FakeResponse:
    def __init__(self):
        self.closed = False

    async def aclose(self):
        self.closed = True


def thinking(text):
    return StreamEvent(type=EventType.THINKING_DELTA, thinking_text=text)


def stop(reason="stop"):
    return StreamEvent(type=EventType.MESSAGE_STOP, finish_reason=reason)


class HarnessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.agents = []
        asyncio.get_running_loop().slow_callback_duration = 2.0

    async def asyncTearDown(self):
        for agent in self.agents:
            await agent.close()

    def agent(self, *, config=None, sink=None, approval=None):
        events = []
        agent = AgentLoop(config or AgentConfig(), event_sink=sink or events.append,
                          approval_handler=approval)
        self.agents.append(agent)
        response = FakeResponse()
        agent._send_stream_request = AsyncMock(return_value=response)
        return agent, events, response

    @staticmethod
    def stream(agent, events):
        async def stream(_response):
            for event in events:
                yield event
        agent._process_stream = stream

    async def test_delayed_fifo_keeps_one_turn_and_one_receipt_per_command(self):
        agent, events, _ = self.agent()
        executed = []

        async def execute(command):
            await asyncio.sleep(.001)
            executed.append(command.id)
            return ExecutionResult(success=True, tool=command.tool)

        agent.executor.execute = execute
        self.stream(agent, [thinking('<tf-write id="1" path="a">a</tf-write>'
                                    '<tf-append id="2" path="a">b</tf-append>'), stop()])
        await agent.run("write")
        self.assertEqual(executed, ["1", "2"])
        self.assertEqual(agent._turn_count, 1)
        self.assertEqual([e["id"] for e in events if e["type"] == "tool_completed"], ["1", "2"])

    async def test_blocking_read_returns_before_dependent_write(self):
        agent, _, _ = self.agent()
        agent.executor.execute = AsyncMock(return_value=ExecutionResult(success=True, tool="read", content="fact"))
        self.stream(agent, [thinking('<tf-read id="1" path="a" />'
                                    '<tf-write id="2" path="b">guess</tf-write>'), stop()])
        self.assertTrue(await agent._run_one_turn())
        self.assertEqual(agent.executor.execute.await_count, 1)
        self.assertIn("fact", agent.messages[-1]["content"])
        self.assertEqual(agent.context.records[0].tool, "read")
        self.assertEqual(agent.context.records[1].status, "skipped")
        self.assertEqual(agent.context.next_stamp, 3)

    async def test_failure_interrupts_stalled_stream_and_skips_queue(self):
        agent, _, response = self.agent()
        executed = []

        async def execute(command):
            executed.append(command.id)
            return ExecutionResult(success=False, tool=command.tool, error="root failure")

        async def stream(_):
            yield thinking('<tf-write id="1" path="a">a</tf-write>'
                           '<tf-write id="2" path="b">b</tf-write>')
            await asyncio.Event().wait()

        agent.executor.execute = execute
        agent._process_stream = stream
        self.assertTrue(await asyncio.wait_for(agent._run_one_turn(), .5))
        self.assertTrue(response.closed)
        self.assertEqual(executed, ["1"])
        self.assertEqual([r.status for r in agent.context.records], ["failed", "skipped"])
        self.assertIn("root failure", agent.messages[-1]["content"])

    async def test_cancel_joins_worker_and_drops_unstarted_commands(self):
        agent, events, response = self.agent()
        started = asyncio.Event()
        executed = []

        async def execute(command):
            executed.append(command.id)
            started.set()
            await asyncio.Event().wait()

        async def stream(_):
            yield thinking('<tf-write id="1" path="a">a</tf-write>'
                           '<tf-write id="2" path="b">b</tf-write>')
            await asyncio.Event().wait()

        agent.executor.execute = execute
        agent._process_stream = stream
        task = asyncio.create_task(agent.run("write"))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(response.closed)
        self.assertEqual(executed, ["1"])
        self.assertEqual({r.status for r in agent.context.records}, {"cancelled", "skipped"})
        self.assertEqual(events[-1]["type"], "run_finished")
        self.assertTrue(events[-1]["cancelled"])
        self.assertFalse([t for t in asyncio.all_tasks() if "CommandExecutionQueue" in repr(t.get_coro())])

    async def test_cancel_during_queue_drain_leaves_no_worker(self):
        agent, _, _ = self.agent()
        started = asyncio.Event()

        async def execute(command):
            started.set()
            await asyncio.Event().wait()

        agent.executor.execute = execute
        self.stream(agent, [thinking('<tf-write id="1" path="a">a</tf-write>'), stop()])
        task = asyncio.create_task(agent.run("write"))
        await started.wait()
        await asyncio.sleep(.01)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse([t for t in asyncio.all_tasks() if "CommandExecutionQueue" in repr(t.get_coro())])

    async def test_native_interleaved_indices_and_ledger(self):
        agent, events, _ = self.agent()
        seen = []

        async def execute(command):
            seen.append(command.path)
            return ExecutionResult(success=True, tool="read", content=command.path)

        agent.executor.execute = execute
        self.stream(agent, [
            StreamEvent(type=EventType.TOOL_USE_START, tool_id="a", tool_name="read", tool_index=0),
            StreamEvent(type=EventType.TOOL_USE_START, tool_id="b", tool_name="read", tool_index=1),
            StreamEvent(type=EventType.TOOL_USE_DELTA, tool_index=0, tool_input='{"path":"a"}'),
            StreamEvent(type=EventType.TOOL_USE_DELTA, tool_index=1, tool_input='{"path":"b"}'),
            stop("tool_calls"),
        ])
        await agent._run_one_turn()
        self.assertEqual(seen, ["a", "b"])
        self.assertEqual([r.id for r in agent.context.records], ["a", "b"])
        self.assertTrue(all(r.injected for r in agent.context.records))
        self.assertEqual([e["channel"] for e in events if e["type"] == "tool_completed"], ["native", "native"])

    async def test_invalid_native_json_does_not_execute(self):
        agent, _, _ = self.agent()
        agent.executor.execute = AsyncMock()
        await agent._handle_traditional_tools([{"id": "a", "name": "write", "input": "{bad"}])
        agent.executor.execute.assert_not_awaited()
        self.assertEqual(agent.context.records[-1].status, "failed")
        self.assertIn("Invalid tool arguments", agent.messages[-1]["content"])

    async def test_invalid_native_field_type_returns_auditable_error(self):
        agent, _, _ = self.agent()
        agent.executor.execute = AsyncMock()
        result = await agent._execute_traditional_tool("write", {"path": 5, "content": {}}, tool_id="bad")
        agent.executor.execute.assert_not_awaited()
        self.assertIn("must be a string", result)
        self.assertEqual(agent.context.records[-1].status, "failed")

    async def test_approval_is_per_call_and_does_not_change_policy(self):
        approval = AsyncMock(side_effect=[True, False])
        config = AgentConfig(security=SecurityPolicy(approval_mode="request_all"))
        agent, events, _ = self.agent(config=config, approval=approval)
        agent.executor.execute = AsyncMock(return_value=ExecutionResult(success=True, tool="bash", exit_code=0))
        first = await agent._execute_command(Command(id="1", tool="bash", cmd="test", need_result=True))
        second = await agent._execute_traditional_tool("bash", {"cmd": "test"}, tool_id="native1")
        self.assertTrue(first.success)
        self.assertIn("APPROVAL DENIED", second)
        self.assertEqual(agent.executor.execute.await_count, 1)
        self.assertEqual(config.security.approval_mode, "request_all")
        self.assertEqual(len([e for e in events if e["type"] == "approval_required"]), 2)

    async def test_durable_sink_failure_prevents_side_effect(self):
        def sink(event):
            if event["type"] == "tool_started":
                raise OSError("disk full")

        agent, _, _ = self.agent(sink=sink)
        agent.executor.execute = AsyncMock()
        self.stream(agent, [thinking('<tf-write id="1" path="a">a</tf-write>'), stop()])
        with self.assertRaisesRegex(OSError, "disk full"):
            await agent.run("write")
        agent.executor.execute.assert_not_awaited()

    async def test_sink_completion_sees_committed_ledger(self):
        checked = []
        agent = None

        def sink(event):
            if event["type"] == "tool_completed":
                checked.append(agent.context.records[-1].id)

        agent, _, _ = self.agent(sink=sink)
        agent.executor.execute = AsyncMock(return_value=ExecutionResult(success=True, tool="write"))
        await agent._execute_command(Command(id="1", tool="write", path="a", content="a"))
        self.assertEqual(checked, ["1"])

    async def test_turn_and_failure_limits(self):
        for failed, expected in [(False, "max_run_turns"), (True, "max_consecutive_failures")]:
            agent, events, _ = self.agent(config=AgentConfig(max_run_turns=4, max_consecutive_failures=2))

            async def turn():
                agent._last_turn_failed = failed
                return True

            agent._run_one_turn = turn
            await agent.run("continue")
            self.assertEqual(events[-1]["stopped_reason"], expected)
            self.assertEqual(events[-1]["turns"], 2 if failed else 4)

    async def test_time_limit_cancels_hung_request(self):
        agent, events, _ = self.agent(config=AgentConfig(max_run_seconds=.02))

        async def send(*_):
            await asyncio.Event().wait()

        agent._send_stream_request = send
        await asyncio.wait_for(agent.run("run"), .5)
        self.assertEqual(events[-1]["stopped_reason"], "max_run_seconds")

    async def test_headless_does_not_write_terminal(self):
        agent, events, _ = self.agent()
        self.stream(agent, [StreamEvent(type=EventType.TEXT_DELTA, text="hello"), stop()])
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            await agent.run("hello")
        self.assertEqual(output.getvalue(), "")
        self.assertEqual("".join(e["text"] for e in events if e["type"] == "text_delta"), "hello")

    async def test_truncated_command_retry_does_not_corrupt_body(self):
        agent, _, _ = self.agent()
        agent.executor.execute = AsyncMock(return_value=ExecutionResult(success=True, tool="write"))
        self.stream(agent, [thinking('<tf-write id="1" path="a">partial'), stop("length")])
        await agent._run_one_turn()
        self.stream(agent, [thinking('<tf-write id="2" path="a">complete</tf-write>'), stop()])
        await agent._run_one_turn()
        self.assertEqual(agent.executor.execute.await_args.args[0].content, "complete")

    async def test_conflicting_id_cannot_be_reported_as_success(self):
        agent, _, _ = self.agent()
        agent.executor.execute = AsyncMock(return_value=ExecutionResult(success=True, tool="write"))
        first = Command(id="1", tool="write", path="a", content="a")
        await agent._execute_command(first)
        self.assertTrue((await agent._execute_command(first)).success)
        self.assertFalse((await agent._execute_command(Command(id="1", tool="write", path="a", content="b"))).success)
        self.assertEqual(agent.executor.execute.await_count, 1)

    async def test_failed_receipt_replay_preserves_failure_feedback(self):
        agent, _, _ = self.agent()
        agent.executor.execute = AsyncMock(return_value=ExecutionResult(success=False, tool="write", error="disk failed"))
        command = Command(id="1", tool="write", path="a", content="a")
        await agent._execute_command(command)
        agent.context.clear_flags()
        self.assertFalse((await agent._execute_command(command)).success)
        self.assertIn("disk failed", agent.context.build_failure_message())
        self.assertEqual(agent.executor.execute.await_count, 1)

    async def test_file_thread_settles_before_cancel_returns(self):
        started = threading.Event()
        release = threading.Event()

        def operation():
            started.set()
            release.wait(1)

        task = asyncio.create_task(Executor._file_io(operation))
        while not started.is_set():
            await asyncio.sleep(.001)
        task.cancel()
        await asyncio.sleep(.005)
        self.assertFalse(task.done())
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_shell_cancellation_cleans_owned_process(self):
        process = AsyncMock()
        process.returncode = None
        started = asyncio.Event()
        release = asyncio.Event()

        async def read(size):
            started.set()
            await release.wait()
            return b""

        async def wait():
            await release.wait()
            return -9

        process.stdout.read.side_effect = read
        process.stderr.read.side_effect = read
        process.wait.side_effect = wait
        executor = Executor()

        async def cleanup(proc):
            process.returncode = -9
            release.set()

        executor._stop_process_tree = AsyncMock(side_effect=cleanup)
        with patch("asyncio.create_subprocess_shell", AsyncMock(return_value=process)):
            task = asyncio.create_task(executor.execute(Command(id="1", tool="bash", cmd="fake")))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        executor._stop_process_tree.assert_awaited_once_with(process)

    async def test_native_duplicate_receipt_survives_snapshot_and_conflict(self):
        agent, _, _ = self.agent()
        agent.executor.execute = AsyncMock(return_value=ExecutionResult(success=True, tool="append", bytes_written=1))
        inputs = {"path": "a", "content": "X"}
        first = await agent._execute_traditional_tool("append", inputs, tool_id="native_same")
        self.assertEqual(await agent._execute_traditional_tool("append", inputs, tool_id="native_same"), first)
        self.assertEqual(agent.executor.execute.await_count, 1)
        restored, _, _ = self.agent()
        restored.load_snapshot(agent.to_snapshot())
        restored.executor.execute = AsyncMock()
        self.assertEqual(await restored._execute_traditional_tool("append", inputs, tool_id="native_same"), first)
        conflict = await restored._execute_traditional_tool("append", {**inputs, "content": "Y"}, tool_id="native_same")
        self.assertIn("conflicts", conflict)
        restored.executor.execute.assert_not_awaited()

    async def test_native_fingerprint_includes_non_file_parameters(self):
        agent, _, _ = self.agent()
        handler = AsyncMock(return_value="answer")
        agent.tool_registry.register(ToolSpec(name="lookup", description="test", parameters={}), handler)
        await agent._execute_traditional_tool("lookup", {"query": "A", "options": {"limit": 2}}, tool_id="same")
        restored, _, _ = self.agent()
        restored.load_snapshot(agent.to_snapshot())
        restored.tool_registry.register(ToolSpec(name="lookup", description="test", parameters={}), handler)
        result = await restored._execute_traditional_tool("lookup", {"query": "B", "options": {"limit": 2}}, tool_id="same")
        self.assertIn("conflicts", result)
        self.assertEqual(handler.await_count, 1)

    async def test_text_and_native_share_id_guard(self):
        agent, _, _ = self.agent()
        agent.executor.execute = AsyncMock(return_value=ExecutionResult(success=True, tool="append"))
        await agent._execute_command(Command(id="1", tool="append", path="a", content="X"))
        result = await agent._execute_traditional_tool("append", {"path": "a", "content": "X"}, tool_id="1")
        self.assertIn("conflicts", result)
        self.assertEqual(agent.executor.execute.await_count, 1)

    async def test_native_failure_skips_rest_and_keeps_provider_pairs(self):
        for provider_format in ("openai", "anthropic"):
            agent, _, _ = self.agent()
            agent.config.provider.format = provider_format
            agent.executor.execute = AsyncMock(return_value=ExecutionResult(success=False, tool="edit", error="missing old text"))
            await agent._handle_traditional_tools([
                {"id": "a", "name": "edit", "input": '{"path":"a","old_text":"x","new_text":"y"}'},
                {"id": "b", "name": "write", "input": '{"path":"b","content":"wrong"}'},
            ])
            self.assertEqual(agent.executor.execute.await_count, 1)
            self.assertEqual([r.status for r in agent.context.records], ["failed", "skipped"])
            if provider_format == "openai":
                self.assertEqual([m["tool_call_id"] for m in agent.messages if m["role"] == "tool"], ["a", "b"])
                self.assertIn("Skipped", agent.messages[-1]["content"])
            else:
                self.assertEqual([r["tool_use_id"] for r in agent.messages[-1]["content"]], ["a", "b"])
                self.assertTrue(all(r["is_error"] for r in agent.messages[-1]["content"]))

    async def test_unfinished_eof_is_error_not_completed(self):
        for text in ("partial answer", '<tf-write id="1" path="a">partial'):
            agent, events, _ = self.agent()

            class PartialResponse(FakeResponse):
                async def aiter_lines(self):
                    yield "data: " + json.dumps({"choices": [{"delta": {"content": text}}]})

            agent._send_stream_request = AsyncMock(return_value=PartialResponse())
            agent.executor.execute = AsyncMock()
            await agent.run("task")
            self.assertEqual(events[-1]["stopped_reason"], "error")
            self.assertIn("incomplete", agent.last_error)
            agent.executor.execute.assert_not_awaited()

    async def test_extension_failure_uses_explicit_result_status(self):
        agent, _, _ = self.agent()
        await agent._execute_traditional_tool("fetch_url", {}, tool_id="missing")
        self.assertEqual(agent.context.records[-1].status, "failed")
        self.assertTrue(agent._last_turn_failed)
        agent.tool_registry.register(ToolSpec(name="normal_text", description="test", parameters={}),
                                     AsyncMock(return_value="ERROR is a word in the document"))
        await agent._execute_traditional_tool("normal_text", {}, tool_id="ordinary")
        self.assertEqual(agent.context.records[-1].status, "success")
        agent.tool_registry.register(ToolSpec(name="explicit_failure", description="test", parameters={}),
                                     AsyncMock(return_value=ToolResult.failure("arbitrary failure")))
        await agent._execute_traditional_tool("explicit_failure", {}, tool_id="typed")
        self.assertEqual(agent.context.records[-1].status, "failed")

    async def test_read_only_blocks_image_command_before_creation(self):
        image = ImageGenerationConfig(enabled=True, provider="command", command=["must-not-run"])
        agent, _, _ = self.agent(config=AgentConfig(security=SecurityPolicy(read_only=True),
                                                  interfaces=InterfaceConfig(image_generation=image)))
        with patch("asyncio.create_subprocess_exec", AsyncMock()) as spawn:
            output = await agent._execute_traditional_tool("image_generate", {"prompt": "test"}, tool_id="img")
        spawn.assert_not_awaited()
        self.assertIn("read-only", output)
        self.assertEqual(agent.context.records[-1].status, "failed")

    async def test_extension_process_cancel_and_timeout_clean_owned_tree(self):
        for cancel in (True, False):
            process = AsyncMock()
            started = asyncio.Event()
            released = asyncio.Event()

            async def read(*_):
                started.set()
                await released.wait()
                return b""

            process.stdout.read.side_effect = read
            process.stderr.read.side_effect = read
            process.wait.side_effect = released.wait
            process.stdin = Mock(drain=AsyncMock())

            async def cleanup(_):
                released.set()

            with patch.object(Executor, "_stop_process_tree", AsyncMock(side_effect=cleanup)) as stop_tree:
                task = asyncio.create_task(ExternalInterfaces._communicate_owned(process, b"{}", 10 if cancel else .01))
                await started.wait()
                if cancel:
                    task.cancel()
                with self.assertRaises(asyncio.CancelledError if cancel else asyncio.TimeoutError):
                    await task
                stop_tree.assert_awaited_once_with(process)

    async def test_failed_bash_returns_stdout_exit_and_truncation(self):
        agent, _, _ = self.agent()
        agent.executor.execute = AsyncMock(return_value=ExecutionResult(success=False, tool="bash", exit_code=2,
                                                                       stdout="ASSERT: expected 2 got 1", truncated=True))
        await agent._handle_traditional_tools([{"name": "bash", "id": "failed", "input": '{"cmd":"test"}'}])
        content = agent.messages[-1]["content"]
        self.assertIn("exit_code: 2", content)
        self.assertIn("ASSERT: expected 2 got 1", content)
        self.assertIn("TRUNCATED", content)


if __name__ == "__main__":
    unittest.main()
