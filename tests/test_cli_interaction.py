import asyncio
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from src import renderer
from src.agent_loop import AgentConfig, AgentLoop
from src.cli import (build_slash_completer, current_permission_mode,
                     handle_slash_command, interactive_loop_v2)
from src.executor import ExecutionResult
from src.interactive_controller import InteractiveController
from src.parser import Command
from src.provider import ProviderConfig, ProviderProfileConfig
from src.session import SessionStore
from src.tui import run_tui


async def wait_for(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.01)


class CliInteractionTests(unittest.IsolatedAsyncioTestCase):
    def make_agent(self, cwd, provider=None):
        agent = AgentLoop(AgentConfig(
            cwd=str(cwd), provider=provider or ProviderConfig(model="fixture-model"),
        ))
        self.addAsyncCleanup(agent.close)
        return agent

    async def test_real_composers_preserve_multiline_key_input_and_paste(self):
        for full_screen in (False, True):
            with self.subTest(full_screen=full_screen), tempfile.TemporaryDirectory() as root:
                agent = self.make_agent(Path(root))
                calls = []

                async def turn():
                    calls.append(agent.messages[-1]["content"])
                    if calls[-1] == "active":
                        await asyncio.Future()
                    return False

                agent._run_one_turn = turn
                with create_pipe_input() as terminal, create_app_session(input=terminal, output=DummyOutput()):
                    if full_screen:
                        runner = run_tui(
                            agent, store=None, autosave=False,
                            handle_slash=handle_slash_command,
                            current_permission_mode=current_permission_mode,
                            completer=build_slash_completer(agent),
                        )
                    else:
                        runner = interactive_loop_v2(agent, autosave=False)
                    app = asyncio.create_task(runner)
                    try:
                        terminal.send_text("  one\ntwo\x1b\rthree  \r")
                        await wait_for(lambda: len(calls) == 1)
                        terminal.send_text("\x1b[200~first\n  second\n\x1b[201~\r")
                        await wait_for(lambda: len(calls) == 2)
                        terminal.send_text("active\r")
                        await wait_for(lambda: len(calls) == 3)
                        terminal.send_text("keep draft\x1b")
                        await wait_for(lambda: agent.stopped_reason == "cancelled")
                        terminal.send_text("\r")
                        await wait_for(lambda: len(calls) == 4)
                        terminal.send_text("/exit\r")
                        await asyncio.wait_for(app, 3)
                    finally:
                        if not app.done():
                            app.cancel()
                            await asyncio.gather(app, return_exceptions=True)
                self.assertEqual(calls, ["  one\ntwo\nthree  ", "first\n  second\n", "active", "keep draft"])
                requests = agent.conversation.to_dict()["requests"]
                self.assertEqual([request["text"] for request in requests], calls)

    async def test_cancel_pauses_fifo_and_close_awaits_save(self):
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as root, patch.object(renderer, "console", Console(file=output)):
            agent = self.make_agent(Path(root))
            calls = []
            saved = []
            first = asyncio.Event()
            release = asyncio.Event()
            second = asyncio.Event()

            async def turn():
                text = agent.messages[-1]["content"]
                calls.append(text)
                if text == "first":
                    first.set()
                    try:
                        await asyncio.Future()
                    finally:
                        await release.wait()
                if text == "last":
                    second.set()
                    await asyncio.Future()
                return False

            agent._run_one_turn = turn

            class Store:
                def save(self, snapshot):
                    saved.append([item["content"] for item in snapshot["messages"]])

            controller = InteractiveController(agent, Store(), True, lambda: None)
            controller.submit("first")
            await asyncio.wait_for(first.wait(), 3)
            controller.submit("  follow\nup  ")
            controller.handle_command("/btw", "note\n  indented  ")
            controller.submit("last")
            self.assertIn("待发送 3", controller.status())
            controller.handle_command("/queue", "")
            self.assertIn("2. note indented（补充当前任务）", output.getvalue())
            self.assertTrue(controller.handle_command("/resume", "1"))
            self.assertTrue(controller.cancel())
            self.assertTrue(controller.cancel())
            self.assertEqual(saved, [])
            release.set()
            await wait_for(lambda: not controller.running)
            self.assertEqual(calls, ["first"])
            self.assertIn("队列暂停 3", controller.status())
            controller.handle_command("/queue", "")
            self.assertIn("2. note indented\n", output.getvalue())
            controller.handle_command("/queue", "remove 1")
            controller.handle_command("/queue", "edit 1  revised\n  indented  ")
            controller.handle_command("/queue", "resume")
            await asyncio.wait_for(second.wait(), 3)
            controller.submit("never dispatch on exit")
            await controller.close()
            self.assertEqual(calls, ["first", " revised\n  indented  ", "last"])
            self.assertEqual(saved, [calls[:1], calls[:2], calls[:3]])
            self.assertFalse(controller.running)

    async def test_failed_run_leaves_follow_up_paused(self):
        with tempfile.TemporaryDirectory() as root, patch.object(renderer, "console", Console(file=io.StringIO())):
            agent = self.make_agent(Path(root))

            async def turn():
                agent.last_error = "fake provider failure"
                return False

            agent._run_one_turn = turn
            controller = InteractiveController(agent, None, False, lambda: None)
            controller.submit("bad")
            controller.submit("wait")
            await wait_for(lambda: not controller.running)
            self.assertTrue(controller.paused)
            self.assertEqual([item.text for item in controller.pending], ["wait"])
            await controller.close()

    async def test_real_composers_steer_after_stream_and_tool_batch(self):
        for full_screen in (False, True):
            with self.subTest(full_screen=full_screen), tempfile.TemporaryDirectory() as root:
                marker = Path(root) / "effect.txt"
                requests = []
                finish_first_stream = asyncio.Event()
                final_run_finished = asyncio.Event()

                async def reply(reader, writer):
                    headers = await reader.readuntil(b"\r\n\r\n")
                    content_length = next(
                        int(line.split(b":", 1)[1])
                        for line in headers.splitlines() if line.lower().startswith(b"content-length:")
                    )
                    requests.append(json.loads(await reader.readexactly(content_length)))
                    writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n")

                    def frame(content, finish=None):
                        record = {"choices": [{"delta": {"content": content}, "finish_reason": finish}]}
                        writer.write(f"data: {json.dumps(record)}\n\n".encode())

                    if len(requests) == 1:
                        frame('<tf-write id="1" path="effect.txt">batch finished</tf-write>\n')
                        await writer.drain()
                        await finish_first_stream.wait()
                    frame("Response complete.", "stop")
                    writer.write(b"data: [DONE]\n\n")
                    await writer.drain()
                    writer.close()
                    await writer.wait_closed()

                server = await asyncio.start_server(reply, "127.0.0.1", 0)
                async with server:
                    port = server.sockets[0].getsockname()[1]
                    agent = self.make_agent(root, ProviderConfig(
                        model="fixture-model", base_url=f"http://127.0.0.1:{port}", api_key="local-fixture",
                    ))
                    store = SessionStore(str(Path(root) / "session.json"), cwd=root)
                    store.bind(agent)

                    def observe_run(event):
                        if event["type"] == "run_finished" and len(requests) == 3:
                            final_run_finished.set()

                    agent.events.observers.append(observe_run)
                    supplemental = "  first detail\n  continuation"
                    last_detail = "second detail"
                    with (
                        create_pipe_input() as terminal,
                        create_app_session(input=terminal, output=DummyOutput()),
                        patch.object(renderer, "render_info", wraps=renderer.render_info) as info,
                        patch.object(renderer, "render_user_message", wraps=renderer.render_user_message) as rendered,
                    ):
                        if full_screen:
                            runner = run_tui(
                                agent, store=store, autosave=True,
                                handle_slash=handle_slash_command,
                                current_permission_mode=current_permission_mode,
                                completer=build_slash_completer(agent, store),
                            )
                        else:
                            runner = interactive_loop_v2(agent, store=store, autosave=True)
                        app = asyncio.create_task(runner)
                        try:
                            terminal.send_text("initial request\r")
                            await wait_for(marker.exists)
                            terminal.send_text("separate task\r")
                            terminal.send_text("/btw discard draft\r")
                            terminal.send_text(f"\x1b[200~/queue edit 2 {supplemental}\x1b[201~\r")
                            terminal.send_text(f"/steer {last_detail}\r")
                            await wait_for(lambda: any("批次结束后发送（3 条）" in call.args[0]
                                                       for call in info.call_args_list))
                            self.assertEqual(len(requests), 1)
                            self.assertEqual([call.args[0] for call in rendered.call_args_list], ["initial request"])
                            self.assertEqual(marker.read_text(encoding="utf-8"), "batch finished")
                            # File replacement precedes the executor's completed
                            # receipt; wait for that fact rather than filesystem timing.
                            await wait_for(lambda: bool(agent.context.records))
                            self.assertEqual(agent.context.records[0].status, "success")
                            finish_first_stream.set()
                            await asyncio.wait_for(final_run_finished.wait(), 3)
                            terminal.send_text("/exit\r")
                            await asyncio.wait_for(app, 3)
                        finally:
                            finish_first_stream.set()
                            if not app.done():
                                app.cancel()
                                await asyncio.gather(app, return_exceptions=True)
                    self.assertEqual(len(requests), 3)
                    self.assertIn('status="success"', requests[1]["messages"][-1]["content"])
                    self.assertIn("effect.txt", requests[1]["messages"][-1]["content"])
                    # Runtime status attaches to the final user message on the wire.
                    request_texts = [message["content"].partition("\n\n")[0]
                                     for message in requests[1]["messages"] if message["role"] == "user"]
                    self.assertEqual(
                        [text for text in request_texts
                         if text in {"initial request", supplemental, last_detail, "separate task"}],
                        ["initial request", supplemental, last_detail],
                    )
                    self.assertEqual(
                        [call.args[0] for call in rendered.call_args_list],
                        ["initial request", supplemental, last_detail, "separate task"],
                    )
                    self.assertEqual(
                        [request["text"] for request in store.load()["conversation"]["requests"]],
                        ["initial request", supplemental, last_detail, "separate task"],
                    )

    async def test_tool_summary_and_local_recall(self):
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as root, patch.object(renderer, "console", Console(file=output, width=100)):
            agent = self.make_agent(Path(root))
            agent.context.record(Command(tool="bash", id="1"), ExecutionResult(
                success=False, tool="bash", error="fixture failure", stderr="first line\n[original]",
            ))
            with renderer.compact_tool_output():
                for command_id in ("2", "3"):
                    renderer.render_command_start("read", command_id, "readme")
                    renderer.render_command_exec("read", command_id, "readme", True)
                renderer.render_command_exec("bash", "1", "check", False, "fixture failure")
            renderer.render_tool_details(agent.context, "1 stderr 11")
        transcript = output.getvalue()
        self.assertEqual(transcript.count("已完成 read ×2"), 1)
        self.assertIn("失败 bash#1 check fixture failure", transcript)
        self.assertIn("[original]", transcript)

    async def test_thinking_choices_reach_provider_requests(self):
        cases = [
            (ProviderConfig(
                base_url="http://fixture.test", model="deepseek-flash", profile_name="gateway",
                provider_profiles=[ProviderProfileConfig(name="gateway")],
            ), [
                ("/thinking low", {"thinking": {"type": "enabled"}, "reasoning_effort": "low"}, "low"),
                ("/thinking high", {"thinking": {"type": "enabled"}, "reasoning_effort": "high"}, "high"),
                ("/thinking max", {"thinking": {"type": "enabled"}, "reasoning_effort": "max"}, "max"),
                ("/thinking off", {"thinking": {"type": "disabled"}}, "关闭"),
                ("/thinking high", {"thinking": {"type": "enabled"}, "reasoning_effort": "high"}, "high"),
                ("/model compatible-model", {}, "模型默认"),
                ("/thinking xhigh", {"reasoning_effort": "xhigh"}, "xhigh"),
                ("/thinking none", {"reasoning_effort": "none"}, "none"),
                ("/thinking default", {}, "模型默认"),
            ]),
            (ProviderConfig(
                base_url="http://fixture.test", model="anthropic-model", format="anthropic", max_tokens=8192,
            ), [
                ("/thinking medium", {"thinking": {"type": "enabled", "budget_tokens": 4096}}, "4,096 tokens"),
                ("/thinking 8192", {"thinking": {"type": "enabled", "budget_tokens": 4096}}, "4,096 tokens"),
                ("/thinking 2048", {"thinking": {"type": "enabled", "budget_tokens": 2048}}, "2,048 tokens"),
                ("/thinking off", {}, "关闭"),
            ]),
        ]
        with tempfile.TemporaryDirectory() as root, patch.object(
            renderer, "console", Console(file=io.StringIO(), theme=renderer.THEME),
        ):
            for provider, steps in cases:
                requests = []

                async def send(request):
                    requests.append(json.loads(request.content))
                    if provider.format == "anthropic":
                        records = [
                            {"type": "content_block_delta", "index": 0,
                             "delta": {"type": "text_delta", "text": "ok"}},
                            {"type": "message_delta", "delta": {"stop_reason": "end_turn"}},
                            {"type": "message_stop"},
                        ]
                    else:
                        records = [{"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}]
                    return httpx.Response(200, text="".join(f"data: {json.dumps(record)}\n\n" for record in records))

                agent = AgentLoop(AgentConfig(cwd=root, provider=provider))
                try:
                    await agent.client.aclose()
                    agent.client = httpx.AsyncClient(base_url=provider.base_url, transport=httpx.MockTransport(send))
                    controller = InteractiveController(agent, None, False, lambda: None)
                    for command, expected, label in steps:
                        handle_slash_command(command, agent)
                        await agent.run("fixture")
                        self.assertEqual(agent.stopped_reason, "completed")
                        options = {key: requests[-1][key] for key in ("thinking", "reasoning_effort") if key in requests[-1]}
                        self.assertEqual(options, expected, command)
                        self.assertIn(f"思考 {label}", controller.status_line(120, "workspace-write"))
                    self.assertEqual(len(requests), len(steps))
                finally:
                    await agent.close()
