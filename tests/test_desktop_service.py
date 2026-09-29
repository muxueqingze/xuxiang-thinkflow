"""Desktop dispatch contracts; all data and HTTP traffic stay local to tests."""
import asyncio
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.context import ContextManager
from src.desktop_service import DesktopService
from src.usage_tracker import SessionUsage
from src import desktop_service


class LocalSSE:
    """Real loopback HTTP server emitting OpenAI chat completion SSE frames."""

    def __init__(self, chunks):
        self.chunks = chunks
        self.requests = []
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                fixture.requests.append((self.path, json.loads(body)))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()
                chunks = fixture.chunks
                if chunks and isinstance(chunks[0], list):
                    chunks = chunks[min(len(fixture.requests) - 1, len(chunks) - 1)]
                for chunk in chunks:
                    frame = {"choices": [{"delta": {"content": chunk}, "finish_reason": None}]}
                    self.wfile.write(("data: " + json.dumps(frame) + "\n\n").encode())
                    self.wfile.flush()
                frame = {"choices": [{"delta": {}, "finish_reason": "stop"}]}
                self.wfile.write(("data: " + json.dumps(frame) + "\n\ndata: [DONE]\n\n").encode())
                self.wfile.flush()
                self.close_connection = True

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)


class FakeAgent:
    """Fake scheduling only; desktop persistence, dispatch and approval stay real."""

    def __init__(self, event_sink, approval_handler, runner=None):
        self.sink = event_sink
        self.approval_handler = approval_handler
        self.runner = runner
        self.messages = []
        self.context = ContextManager()
        self.usage = SessionUsage(label="desktop-test")
        self.last_error = ""
        self.closed = False
        self.compactions = 0

    async def run(self, prompt):
        self.messages.append({"role": "user", "content": prompt})
        if self.runner:
            await self.runner(self, prompt)
        else:
            self.messages.append({"role": "assistant", "content": "本地回复"})
            self.sink({"type": "text_delta", "text": "本地回复"})

    def to_snapshot(self):
        return copy.deepcopy({"version": 1, "messages": self.messages,
                              "context": self.context.to_dict(), "usage": self.usage.to_dict(),
                              "executed_ids": [], "turn_count": 0})

    def load_snapshot(self, snapshot):
        self.messages = copy.deepcopy(snapshot.get("messages", []))
        self.context = ContextManager.from_dict(snapshot.get("context", {}))
        self.usage = SessionUsage.from_dict(snapshot.get("usage", {}))

    def message_stats(self):
        return {"messages": len(self.messages), "chars": sum(len(m.get("content", "")) for m in self.messages),
                "compactions": self.compactions}

    def compact(self, force=False):
        self.compactions += bool(force)

    async def close(self):
        self.closed = True


class DesktopServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="thinkflow-desktop-test-")
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.other_workspace = self.root / "other-workspace"
        self.other_workspace.mkdir()
        self.events = []
        self.service = DesktopService(lambda event: self.events.append(copy.deepcopy(event)))
        self.runner = None
        self.agents = []
        self.env_patch = patch.dict(os.environ, {"THINKFLOW_HOME": str(self.root / "home")})
        self.env_patch.start()
        self.prompt_patch = patch.object(desktop_service, "resolve_system_prompt", return_value="Local test only.")
        self.prompt_patch.start()

        def create(config, system, cwd, *, event_sink, approval_handler):
            agent = FakeAgent(event_sink, approval_handler, self.runner)
            self.agents.append(agent)
            return agent

        self.agent_patch = patch.object(desktop_service, "create_agent", side_effect=create)
        self.agent_patch.start()

    async def asyncTearDown(self):
        try:
            await self.service.close()
        finally:
            self.agent_patch.stop()
            self.prompt_patch.stop()
            self.env_patch.stop()
            self.temp.cleanup()

    async def initialize(self, config=None):
        return await self.service.dispatch("initialize", {"data_dir": str(self.root / "data"),
                                                           "config": config or {}})

    async def ready(self, **config):
        await self.initialize({"base_url": "http://127.0.0.1:1", "model": "test-local", **config})
        return await self.service.dispatch("open_workspace", {"cwd": str(self.workspace)})

    async def finish(self):
        await asyncio.wait_for(self.service.task, timeout=5)
        return await self.service.dispatch("get_state")

    async def wait_for(self, condition):
        async def poll():
            while not condition():
                await asyncio.sleep(0.005)
        await asyncio.wait_for(poll(), timeout=3)

    def use_real_agent(self):
        self.agent_patch.stop()
        real_create = desktop_service.create_agent
        def isolated_create(config, system, cwd, **kwargs):
            config = {**config, "interfaces": {"skills": {"enabled": False}, "web": {"enabled": False}}}
            return real_create(config, system, cwd, **kwargs)
        self.agent_patch = patch.object(desktop_service, "create_agent", side_effect=isolated_create)
        self.agent_patch.start()

    def assert_secret_absent(self, secret):
        self.assertNotIn(secret, json.dumps(self.events, ensure_ascii=False))
        self.assertNotIn(secret, json.dumps(self.service.state(), ensure_ascii=False))
        for path in (self.root / "data").rglob("*"):
            if path.is_file():
                self.assertNotIn(secret, path.read_text(encoding="utf-8"), str(path))

    async def test_initialize_empty_workspace_and_workspace_selection(self):
        with self.assertRaises(ValueError):
            await self.service.dispatch("get_state")
        with self.assertRaises(ValueError):
            await self.service.dispatch("initialize", {"data_dir": "relative"})
        state = await self.initialize()
        self.assertEqual(state["cwd"], "")
        self.assertEqual(state["sessions"], [])
        self.assertEqual(self.agents, [])
        with self.assertRaises(ValueError):
            await self.service.dispatch("run", {"prompt": "test"})
        with self.assertRaises(ValueError):
            await self.service.dispatch("open_workspace", {"cwd": str(self.root / "missing")})
        state = await self.service.dispatch("open_workspace", {"cwd": str(self.workspace)})
        self.assertEqual(state["cwd"], str(self.workspace.resolve()))
        self.assertTrue(self.service.store.path.is_file())
        self.assertEqual(state["sessions"][0]["id"], state["session_id"])
        with self.assertRaises(ValueError):
            await self.initialize()

    async def test_invalid_initialize_can_be_retried(self):
        with self.assertRaises(ValueError):
            await self.initialize({"provider": "invalid"})
        self.assertIsNone(self.service.data_dir)
        state = await self.initialize()
        self.assertEqual(state["status"], "idle")

    async def test_params_must_be_object_including_empty_containers(self):
        await self.initialize()
        for params in ([], "", 0, False, ["bad"], "bad"):
            with self.subTest(params=params), self.assertRaises(ValueError):
                await self.service.dispatch("get_state", params)

    async def test_config_validation_is_atomic_and_key_stays_private(self):
        key = "TEST_ONLY_SECRET_NEVER_REAL"
        await self.ready(api_key=key)
        before = copy.deepcopy(self.service.config)
        invalid = [{"provider": "bad"}, {"security_profile": "bad"}, {"base_url": "file:///tmp"},
                   {"base_url": "http://name:password@localhost"}, {"base_url": "http://localhost/?key=x"},
                   {"api_path": "//remote/path"}, {"api_path": "relative"}, {"model": None},
                   {"api_key": 42}, {"max_tokens": True}, {"max_tokens": 1.5},
                   {"max_run_turns": 0}, {"max_run_seconds": float("inf")},
                   {"max_run_seconds": float("nan")}, {"max_run_turns": "4"}]
        for config in invalid:
            with self.subTest(config=config), self.assertRaises(ValueError):
                await self.service.dispatch("configure", config)
            self.assertEqual(self.service.config, before)
        await self.service.dispatch("configure", {"api_key": "", "max_tokens": 4096})
        self.assertEqual(self.service.config["api_key"], key)
        self.service._on_agent_event({"type": "tool_started", "id": "1", "tool": "read", "path": key})
        self.service._on_agent_event({"type": "tool_completed", "id": "1", "tool": "read",
                                      "status": "failed", "error": "Synthetic failure " + key})
        await self.service.dispatch("run", {"prompt": "Do not expose " + key})
        state = await self.finish()
        self.assertTrue(state["config"]["has_api_key"])
        self.assertNotIn("api_key", state["config"])
        exported = await self.service.dispatch("export_session")
        self.assertNotIn(key, exported["markdown"])
        self.assertIn("[REDACTED]", exported["markdown"])
        self.assert_secret_absent(key)

    async def test_rotated_and_cleared_key_cannot_reappear(self):
        old, new = "TEST_ONLY_OLD_KEY", "TEST_ONLY_NEW_KEY"
        await self.ready(api_key=old)
        await self.service.dispatch("run", {"prompt": "Secret was " + old})
        await self.finish()
        await self.service.dispatch("configure", {"api_key": new})
        self.assert_secret_absent(old)
        await self.service.dispatch("run", {"prompt": "Secret now " + new})
        await self.finish()
        await self.service.dispatch("configure", {"clear_api_key": True})
        self.assert_secret_absent(old)
        self.assert_secret_absent(new)
        exported = await self.service.dispatch("export_session")
        self.assertNotIn(old, exported["markdown"])
        self.assertNotIn(new, exported["markdown"])

    async def test_sessions_resume_fork_and_workspace_isolation(self):
        await self.ready()
        first = self.service.session_id
        await self.service.dispatch("run", {"prompt": "First local session"})
        await self.finish()
        original_transcript = copy.deepcopy(self.service.transcript)
        original_messages = copy.deepcopy(self.service.agent.messages)
        state = await self.service.dispatch("fork_session")
        branch = state["session_id"]
        self.assertNotEqual(branch, first)
        self.assertEqual(state["messages"], original_transcript)
        self.assertEqual(self.service.agent.messages, original_messages)
        await self.service.dispatch("run", {"prompt": "Branch only"})
        await self.finish()
        state = await self.service.dispatch("resume_session", {"session_id": first})
        self.assertEqual(state["messages"], original_transcript)
        self.assertEqual(self.service.agent.messages, original_messages)
        state = await self.service.dispatch("open_workspace", {"cwd": str(self.other_workspace)})
        self.assertEqual(state["messages"], [])
        self.assertNotIn(first, [s["id"] for s in state["sessions"]])
        with self.assertRaises(FileNotFoundError):
            await self.service.dispatch("resume_session", {"session_id": first})
        for identifier in ("../outside", "a" * 31, "z" * 32):
            with self.assertRaises(ValueError):
                await self.service.dispatch("resume_session", {"session_id": identifier})
        await self.service.dispatch("open_workspace", {"cwd": str(self.workspace)})
        await self.service.dispatch("resume_session", {"session_id": branch})
        self.assertIn("Branch only", [m["content"] for m in self.service.transcript])

    async def test_journal_unfinished_and_corrupt_tail_need_explicit_acknowledgement(self):
        await self.ready()
        identifier = self.service.session_id
        self.service._on_agent_event({"type": "tool_started", "channel": "text", "id": "1",
                                      "tool": "write", "path": "might-exist.txt"})
        with self.service.journal_path.open("a", encoding="utf-8") as stream:
            stream.write('{"truncated":')
        await self.service.dispatch("new_session")
        state = await self.service.dispatch("resume_session", {"session_id": identifier})
        self.assertTrue(state["recovery_required"])
        self.assertEqual(len(state["recovery"]), 2)
        self.assertFalse((self.workspace / "might-exist.txt").exists())
        with self.assertRaises(ValueError):
            await self.service.dispatch("run", {"prompt": "continue"})
        with self.assertRaises(ValueError):
            await self.service.dispatch("fork_session")
        state = await self.service.dispatch("acknowledge_recovery")
        self.assertFalse(state["recovery_required"])
        self.assertEqual(state["status"], "idle")
        self.assertIn("THINKFLOW RECOVERY", self.service.agent.messages[-1]["content"])
        state = await self.service.dispatch("resume_session", {"session_id": identifier})
        self.assertFalse(state["recovery_required"])

    async def test_busy_rejects_changes_cancel_and_shutdown_persist(self):
        entered = asyncio.Event()
        async def block(agent, prompt):
            agent.sink({"type": "text_delta", "text": "部分回复"})
            entered.set()
            await asyncio.Event().wait()
        self.runner = block
        await self.ready()
        original_id = self.service.session_id
        await self.service.dispatch("run", {"prompt": "Hold locally"})
        await asyncio.wait_for(entered.wait(), 2)
        operations = [("configure", {"model": "changed"}), ("new_session", {}), ("fork_session", {}),
                      ("resume_session", {"session_id": original_id}), ("compact", {}),
                      ("open_workspace", {"cwd": str(self.other_workspace)}), ("run", {"prompt": "second"})]
        for method, params in operations:
            with self.subTest(method=method), self.assertRaises(ValueError):
                await self.service.dispatch(method, params)
        self.assertEqual(self.service.session_id, original_id)
        state = await self.service.dispatch("cancel")
        self.assertEqual(state["status"], "cancelled")
        self.assertTrue(self.service.task.done())
        saved = json.loads(self.service.store.path.read_text(encoding="utf-8"))
        self.assertEqual(saved["desktop"]["transcript"][-1]["content"], "部分回复")
        entered.clear()
        await self.service.dispatch("run", {"prompt": "Hold until shutdown"})
        await asyncio.wait_for(entered.wait(), 2)
        result = await self.service.dispatch("shutdown")
        self.assertEqual(result, {"closed": True})
        self.assertTrue(self.service.agent.closed)
        self.assertTrue(self.service.task.done())

    async def test_approval_requires_matching_id_and_boolean_then_rejects_stale(self):
        results = []
        async def approve(agent, prompt):
            results.append(await agent.approval_handler("write", {"path": "not-written.txt"}))
        self.runner = approve
        await self.ready()
        for choice in (False, True):
            await self.service.dispatch("run", {"prompt": "Approve locally"})
            await self.wait_for(lambda: self.service.pending_approval is not None)
            identifier = self.service.pending_approval["request_id"]
            self.assertEqual(self.service.state()["status"], "approval")
            with self.assertRaises(ValueError):
                await self.service.dispatch("approve", {"request_id": "wrong", "approved": True})
            for bad in (1, "true", None):
                with self.assertRaises(ValueError):
                    await self.service.dispatch("approve", {"request_id": identifier, "approved": bad})
            await self.service.dispatch("approve", {"request_id": identifier, "approved": choice})
            state = await self.finish()
            self.assertIsNone(state["pending_approval"])
            with self.assertRaises(ValueError):
                await self.service.dispatch("approve", {"request_id": identifier, "approved": choice})
        self.assertEqual(results, [False, True])

    async def test_cancel_during_approval_clears_pending_request(self):
        async def approve(agent, prompt):
            await agent.approval_handler("bash", {"cmd": "never execute"})
        self.runner = approve
        await self.ready()
        await self.service.dispatch("run", {"prompt": "Wait for approval"})
        await self.wait_for(lambda: self.service.pending_approval is not None)
        state = await self.service.dispatch("cancel")
        self.assertEqual(state["status"], "cancelled")
        self.assertIsNone(state["pending_approval"])
        self.assertIsNone(self.service.approval_future)

    async def test_failure_is_saved_and_partial_reply_can_be_restored(self):
        async def fail(agent, prompt):
            agent.sink({"type": "text_delta", "text": "保留部分结果"})
            raise RuntimeError("local simulated backend failure")
        self.runner = fail
        await self.ready()
        identifier = self.service.session_id
        await self.service.dispatch("run", {"prompt": "Fail locally"})
        state = await self.finish()
        self.assertEqual(state["status"], "error")
        self.assertIn("simulated backend failure", state["last_error"])
        saved = json.loads(self.service.store.path.read_text(encoding="utf-8"))
        self.assertEqual(saved["desktop"]["status"], "error")
        self.assertEqual(saved["desktop"]["transcript"][-1]["content"], "保留部分结果")
        await self.service.dispatch("new_session")
        state = await self.service.dispatch("resume_session", {"session_id": identifier})
        self.assertEqual(state["messages"][-1]["content"], "保留部分结果")

    async def test_save_failure_reports_error_and_finishes_run(self):
        release = asyncio.Event()
        async def wait(agent, prompt):
            await release.wait()
        self.runner = wait
        await self.ready()
        await self.service.dispatch("run", {"prompt": "Simulate disk failure"})
        with patch.object(self.service.store, "save", side_effect=OSError("local simulated disk full")):
            release.set()
            state = await self.finish()
        self.assertEqual(state["status"], "error")
        self.assertIn("会话保存失败", state["last_error"])
        self.assertIn("disk full", state["last_error"])
        self.assertTrue(self.service.task.done())
        self.assertEqual(self.events[-1]["type"], "run_finished")
        self.assertEqual(self.events[-1]["status"], "error")

    async def test_cancelled_tool_receipt_requires_recovery_confirmation(self):
        entered = asyncio.Event()
        async def wait(agent, prompt):
            event = {"id": "1", "tool": "write", "path": "unknown-result.txt", "channel": "text"}
            agent.sink({"type": "tool_started", **event})
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                agent.sink({"type": "tool_completed", "status": "cancelled", **event})
                raise
        self.runner = wait
        await self.ready()
        await self.service.dispatch("run", {"prompt": "Cancel a tool"})
        await asyncio.wait_for(entered.wait(), 2)
        state = await self.service.dispatch("cancel")
        self.assertTrue(state["recovery_required"])
        with self.assertRaises(ValueError):
            await self.service.dispatch("run", {"prompt": "do not automatically replay"})
        state = await self.service.dispatch("acknowledge_recovery")
        self.assertFalse(state["recovery_required"])

    async def test_invalid_prompt_and_missing_endpoint_do_not_mutate_session(self):
        await self.initialize()
        await self.service.dispatch("open_workspace", {"cwd": str(self.workspace)})
        with self.assertRaises(ValueError):
            await self.service.dispatch("run", {"prompt": "Missing model settings"})
        await self.service.dispatch("configure", {"base_url": "http://127.0.0.1:1", "model": "local"})
        for prompt in ("", " \n ", None, 123, "x" * 200001):
            with self.assertRaises(ValueError):
                await self.service.dispatch("run", {"prompt": prompt})
        self.assertEqual(self.service.transcript, [])
        self.assertEqual(self.service.status, "idle")
        self.assertIsNone(self.service.task)

    async def test_real_localhost_endpoint_accepts_no_api_key(self):
        self.use_real_agent()
        with LocalSSE(["本地模型无需密钥。"]) as server:
            await self.ready(base_url=server.url, security_profile="open")
            await self.service.dispatch("run", {"prompt": "Local unauthenticated inference"})
            state = await self.finish()
            self.assertEqual(state["status"], "idle", state["last_error"])
            self.assertEqual(state["messages"][-1]["content"], "本地模型无需密钥。")
            self.assertEqual(len(server.requests), 1)
            await self.service.dispatch("shutdown")

    async def test_real_localhost_risky_tool_waits_for_valid_approval_then_denial_has_no_side_effect(self):
        self.use_real_agent()
        chunks = [['<tf-bash id="1" cmd="echo NEVER_EXECUTE &gt; approval-marker.txt" />'],
                  ["授权已拒绝，未执行命令。"]]
        with LocalSSE(chunks) as server:
            await self.ready(base_url=server.url, api_key="TEST_ONLY_APPROVAL", security_profile="balanced")
            await self.service.dispatch("run", {"prompt": "Ask before executing risky command"})
            await self.wait_for(lambda: self.service.pending_approval is not None)
            self.assertFalse((self.workspace / "approval-marker.txt").exists())
            approvals = [e for e in self.events if e["type"] == "approval_required"]
            self.assertEqual(len(approvals), 1)
            self.assertEqual(approvals[0]["request_id"], self.service.pending_approval["request_id"])
            self.assertEqual(approvals[0]["tool"], "bash")
            await self.service.dispatch("approve", {"request_id": approvals[0]["request_id"], "approved": False})
            state = await self.finish()
            self.assertEqual(state["status"], "idle", state["last_error"])
            self.assertFalse((self.workspace / "approval-marker.txt").exists())
            self.assertEqual(state["ledger"][0]["status"], "failed")
            self.assertIn("APPROVAL DENIED", state["ledger"][0]["error"])
            self.assertEqual(len([e for e in self.events if e["type"] == "run_finished"]), 1)
            self.assertEqual(self.events[-1]["type"], "run_finished")
            await self.service.dispatch("shutdown")

    async def test_real_localhost_sse_executes_tool_and_restores_receipt(self):
        self.use_real_agent()
        chunks = ['<tf-write id="1" path="nested/result.txt">', 'real local output\n</tf-write>', '已保存本地结果。']
        with LocalSSE(chunks) as server:
            await self.ready(base_url=server.url, api_path="/test/chat", api_key="TEST_ONLY_LOCAL_SSE",
                             security_profile="open")
            identifier = self.service.session_id
            await self.service.dispatch("run", {"prompt": "Write local fixture"})
            state = await self.finish()
            self.assertEqual(state["status"], "idle", state["last_error"])
            self.assertEqual((self.workspace / "nested/result.txt").read_text(encoding="utf-8").strip(),
                             "real local output")
            self.assertEqual(len(server.requests), 1)
            route, body = server.requests[0]
            self.assertEqual(route, "/test/chat")
            self.assertTrue(body["stream"])
            self.assertEqual(body["model"], "test-local")
            self.assertTrue(any(m["role"] == "user" and "Write local fixture" in m["content"]
                                for m in body["messages"]))
            self.assertEqual(state["messages"][-1]["content"], "已保存本地结果。")
            self.assertEqual(state["ledger"][0]["status"], "success")
            self.assertEqual(state["usage"]["commands"], 1)
            kinds = [e["type"] for e in self.events]
            self.assertLess(kinds.index("tool_started"), kinds.index("tool_completed"))
            self.assertFalse(self.service._unresolved_intents())
            await self.service.dispatch("new_session")
            state = await self.service.dispatch("resume_session", {"session_id": identifier})
            self.assertEqual(state["ledger"][0]["status"], "success")
            self.assertIn("1", self.service.agent._executed_ids)
            self.assertFalse(state["recovery_required"])
            await self.service.dispatch("shutdown")


if __name__ == "__main__":
    unittest.main(verbosity=2)
