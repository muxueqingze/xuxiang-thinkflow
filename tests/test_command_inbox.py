"""Durable service admission, receipts and queue scheduling; local fixtures only."""
import asyncio
import copy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import subprocess
import queue
import tempfile
import unittest
import threading
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.desktop_service import DesktopService
from src import desktop_service
from test_desktop_service import FakeAgent


class CommandInboxTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="thinkflow-inbox-")
        self.root = Path(self.temp.name).resolve()
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.runner = None
        self.calls, self.events, self.services = [], [], []

        def create(config, system, cwd, *, event_sink, approval_handler):
            async def run(agent, prompt):
                self.calls.append(prompt)
                if self.runner:
                    await self.runner(agent, prompt)
            return FakeAgent(event_sink, approval_handler, run)

        self.create_patch = patch.object(desktop_service, "create_agent", side_effect=create)
        self.prompt_patch = patch.object(desktop_service, "resolve_system_prompt", return_value="Local fixture")
        self.create_patch.start()
        self.prompt_patch.start()
        self.service = await self.start()
        await self.service.dispatch("open_workspace", {"cwd": str(self.workspace)})
        self.session = self.service.session_id

    async def asyncTearDown(self):
        try:
            for service in self.services:
                await service.close()
        finally:
            self.create_patch.stop()
            self.prompt_patch.stop()
            self.temp.cleanup()

    async def start(self):
        service = DesktopService(lambda event: self.events.append(copy.deepcopy(event)))
        self.services.append(service)
        await service.dispatch("initialize", {"data_dir": str(self.root / "data"), "config": {"base_url": "http://127.0.0.1:1", "model": "fixture"}})
        return service

    async def submit(self, identifier, prompt, **extra):
        return await self.service.dispatch("submit_input", {"command_id": identifier, "prompt": prompt, "session_id": self.session, **extra})

    async def receipt(self, identifier, service=None):
        result = await (service or self.service).dispatch("get_input_receipt", {"command_id": identifier, "session_id": self.session})
        return result["receipt"]

    async def finish(self):
        await asyncio.wait_for(self.service.task, 3)

    async def test_lost_ack_duplicate_query_and_payload_conflict(self):
        accepted = await self.submit("stable-id", "Only once")
        saved = self.service.store.load()["desktop"]["input_commands"]
        self.assertEqual(saved[0]["id"], "stable-id")
        self.assertEqual(accepted["receipt"]["status"], "queued")
        duplicate = await self.submit("stable-id", "Only once")
        self.assertEqual(duplicate["receipt"]["content_hash"], accepted["receipt"]["content_hash"])
        with self.assertRaisesRegex(ValueError, "不同内容"):
            await self.submit("stable-id", "Different")
        await self.finish()
        receipt = await self.receipt("stable-id")
        self.assertEqual(receipt["status"], "completed")
        self.assertEqual(self.calls, ["Only once"])
        again = await self.submit("stable-id", "Only once")
        self.assertEqual(again["receipt"]["status"], "completed")
        self.assertEqual(self.calls, ["Only once"])
        self.assertIsNone(await self.receipt("unknown-id"))
        restarted = await self.start()
        await restarted.dispatch("open_workspace", {"cwd": str(self.workspace), "session_id": self.session})
        self.assertEqual((await self.receipt("stable-id", restarted))["status"], "completed")
        self.assertNotEqual(restarted.stream_id, self.service.stream_id)

    async def test_fifo_and_submit_during_approval_with_navigation_locked(self):
        entered = asyncio.Event()
        async def approve(agent, prompt):
            if prompt == "First":
                entered.set()
                await agent.approval_handler("write", {"path": "fixture.txt"})
        self.runner = approve
        await self.submit("one", "First")
        await asyncio.wait_for(entered.wait(), 2)
        await self.submit("two", "Second")
        await self.submit("three", "Third")
        state = self.service.state()
        self.assertEqual([item["id"] for item in state["input_queue"]], ["one", "two", "three"])
        with self.assertRaises(ValueError):
            await self.service.dispatch("new_session")
        await self.service.dispatch("approve", {"request_id": self.service.pending_approval["request_id"], "approved": False})
        await self.finish()
        self.assertEqual(self.calls, ["First", "Second", "Third"])
        self.assertEqual(self.service.state()["input_queue"], [])
        for identifier in ("one", "two", "three"):
            self.assertEqual((await self.receipt(identifier))["status"], "completed")

    async def test_failure_pauses_following_input_until_explicit_resume(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def fail(agent, prompt):
            if prompt == "Failure":
                entered.set()
                await release.wait()
                raise RuntimeError("local fixture failure")
        self.runner = fail
        await self.submit("failure", "Failure")
        await entered.wait()
        await self.submit("next", "Next")
        release.set()
        await self.finish()
        self.assertEqual(self.calls, ["Failure"])
        self.assertEqual((await self.receipt("failure"))["status"], "failed")
        self.assertEqual((await self.receipt("next"))["status"], "queued")
        self.assertTrue(self.service.state()["queue_paused"])
        await self.service.dispatch("resume_queue")
        await self.finish()
        self.assertEqual(self.calls, ["Failure", "Next"])

    async def test_cancel_pauses_fifo_and_does_not_replay_cancelled_input(self):
        entered = asyncio.Event()
        async def block(agent, prompt):
            if prompt == "Block":
                entered.set()
                await asyncio.Event().wait()
        self.runner = block
        await self.submit("block", "Block")
        await entered.wait()
        await self.submit("later", "Later")
        state = await self.service.dispatch("cancel")
        self.assertTrue(state["queue_paused"])
        self.assertEqual((await self.receipt("block"))["status"], "cancelled")
        self.assertEqual(self.calls, ["Block"])
        await self.submit("block", "Block")
        await self.service.dispatch("resume_queue")
        await self.finish()
        self.assertEqual(self.calls, ["Block", "Later"])

    async def test_restart_running_becomes_interrupted_and_queued_remains_paused(self):
        entered = asyncio.Event()
        async def block(agent, prompt):
            entered.set()
            await asyncio.Event().wait()
        self.runner = block
        await self.submit("uncertain", "May have executed")
        await entered.wait()
        await self.submit("pending", "Wait for resume")
        crash_snapshot = self.service.store.path.read_bytes()
        await self.service.dispatch("cancel")
        self.service.store.path.write_bytes(crash_snapshot)
        restarted = await self.start()
        state = await restarted.dispatch("open_workspace", {"cwd": str(self.workspace), "session_id": self.session})
        self.assertTrue(state["queue_paused"])
        self.assertIsNone(restarted.task)
        self.assertEqual((await self.receipt("uncertain", restarted))["status"], "interrupted")
        self.assertEqual([item["id"] for item in state["input_queue"]], ["pending"])
        self.assertEqual(self.calls, ["May have executed"])
        retry = await restarted.dispatch("submit_input", {"command_id": "uncertain", "prompt": "May have executed", "session_id": self.session})
        self.assertEqual(retry["receipt"]["status"], "interrupted")
        self.assertIsNone(restarted.task)

    async def test_admission_save_failure_produces_no_ack_or_execution(self):
        before = self.service.store.path.read_bytes()
        with patch.object(self.service.store, "save", side_effect=OSError("local disk full")):
            with self.assertRaises(OSError):
                await self.submit("not-accepted", "No execute")
        self.assertEqual(self.service.store.path.read_bytes(), before)
        self.assertEqual(self.service.input_commands, [])
        self.assertIsNone(self.service.task)
        self.assertEqual(self.calls, [])
        self.assertIsNone(await self.receipt("not-accepted"))
        await self.submit("not-accepted", "No execute")
        await self.finish()
        self.assertEqual(self.calls, ["No execute"])

    async def test_terminal_save_failure_does_not_claim_completed_or_continue(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def wait(agent, prompt):
            entered.set()
            await release.wait()
        self.runner = wait
        await self.submit("terminal", "Finish")
        await entered.wait()
        await self.submit("later", "Later")
        with patch.object(self.service.store, "save", side_effect=OSError("local disk full")):
            release.set()
            await self.finish()
        self.assertEqual((await self.receipt("terminal"))["status"], "running")
        self.assertEqual(self.service.input_commands[0]["status"], "interrupted")
        self.assertTrue(self.service.queue_paused)
        self.assertEqual(self.calls, ["Finish"])
        self.assertEqual((await self.receipt("later"))["status"], "queued")

    async def test_running_transition_save_failure_keeps_accepted_input_waiting(self):
        original_save = self.service.store.save

        def fail_start(snapshot, **kwargs):
            if any(item["status"] == "running" for item in snapshot["desktop"]["input_commands"]):
                raise OSError("local start-save failure")
            original_save(snapshot, **kwargs)

        with patch.object(self.service.store, "save", side_effect=fail_start):
            accepted = await self.submit("accepted-not-started", "Must wait")
            await self.finish()
        self.assertEqual(accepted["receipt"]["status"], "queued")
        self.assertEqual((await self.receipt("accepted-not-started"))["status"], "queued")
        self.assertEqual(self.service.transcript, [])
        self.assertTrue(self.service.queue_paused)
        self.assertEqual(self.calls, [])
        await self.service.dispatch("resume_queue")
        await self.finish()
        self.assertEqual(self.calls, ["Must wait"])

    async def test_unknown_tool_receipt_preserves_recovery_and_blocks_queue_resume(self):
        async def unknown(agent, prompt):
            event = {"id": "write-unknown", "tool": "write", "path": "result.txt", "channel": "text"}
            agent.sink({"type": "tool_started", **event})
            agent.sink({"type": "tool_completed", "status": "unknown", "change_id": "uncertain-change", **event})
        self.runner = unknown
        await self.submit("unknown-effect", "Write might have happened")
        await self.finish()
        self.assertTrue(self.service.recovery)
        self.assertEqual(self.service.recovery[0]["change_id"], "uncertain-change")
        self.assertEqual((await self.receipt("unknown-effect"))["status"], "interrupted")
        with self.assertRaisesRegex(ValueError, "核对"):
            await self.service.dispatch("resume_queue")
        self.assertEqual(self.calls, ["Write might have happened"])

    async def test_revert_reserves_execution_until_file_mutation_finishes(self):
        entered, release = threading.Event(), threading.Event()
        ordering = []

        class Store:
            def revert(self, change_id, revision):
                ordering.append("revert-start")
                entered.set()
                release.wait(3)
                ordering.append("revert-finish")
                return {"id": "restored"}

            def list_changes(self):
                return [{"id": "restored"}]

        self.service.agent.executor = SimpleNamespace(change_store=Store())
        task = asyncio.create_task(self.service.dispatch("revert_workspace_change", {"change_id": "fixture", "expected_revision": "a" * 64}))
        self.assertTrue(await asyncio.to_thread(entered.wait, 2))
        sending = asyncio.create_task(self.submit("after-revert", "After revert"))
        await asyncio.sleep(0)
        self.assertFalse(sending.done())
        with self.assertRaisesRegex(ValueError, "恢复正在进行"):
            await self.service.dispatch("run", {"prompt": "Forbidden concurrent run"})
        with self.assertRaises(ValueError):
            await self.service.dispatch("new_session")
        self.assertEqual(self.calls, [])
        release.set()
        self.assertEqual((await task)["change"]["id"], "restored")
        await sending
        await self.finish()
        self.assertEqual(ordering, ["revert-start", "revert-finish"])
        self.assertEqual(self.calls, ["After revert"])

    async def test_failed_ui_revert_receipt_pauses_execution_and_survives_restart(self):
        from src.executor import Executor
        from src.changes import ChangeStore
        from src.parser import Command
        target = self.workspace / "restore.txt"
        target.write_text("before", encoding="utf8")
        change_store = ChangeStore(self.root / "private-changes", self.workspace)
        executor = Executor(str(self.workspace), change_store=change_store)
        self.service.agent.executor = executor
        await executor.read("restore.txt")
        written = await executor.execute(Command(tool="write", id="change", path="restore.txt", content="after"))
        self.assertTrue(written.success)
        with patch.object(change_store, "complete", side_effect=OSError("local final-receipt failure")):
            with self.assertRaises(OSError):
                await self.service.dispatch("revert_workspace_change", {"change_id": written.change_id, "expected_revision": written.revision})
        self.assertEqual(target.read_text(encoding="utf8"), "before")
        self.assertTrue(self.service.recovery)
        self.assertTrue(self.service.queue_paused)
        with self.assertRaisesRegex(ValueError, "核对"):
            await self.submit("must-not-run", "Do not continue")
        restarted = await self.start()
        state = await restarted.dispatch("open_workspace", {"cwd": str(self.workspace), "session_id": self.session})
        self.assertTrue(state["recovery_required"])
        self.assertEqual(state["recovery"][0]["tool"], "revert")
        self.assertEqual(self.calls, [])
        await restarted.dispatch("acknowledge_recovery")
        state = await restarted.dispatch("resume_session", {"session_id": self.session})
        self.assertFalse(state["recovery_required"])

    async def test_known_revert_conflict_does_not_enter_recovery(self):
        from src.executor import Executor
        from src.changes import ChangeStore
        from src.parser import Command
        target = self.workspace / "conflict.txt"
        target.write_text("before", encoding="utf8")
        change_store = ChangeStore(self.root / "private-known", self.workspace)
        executor = Executor(str(self.workspace), change_store=change_store)
        self.service.agent.executor = executor
        await executor.read("conflict.txt")
        written = await executor.execute(Command(tool="write", id="write", path="conflict.txt", content="after"))
        target.write_text("external", encoding="utf8")
        with self.assertRaisesRegex(ValueError, "revision_conflict"):
            await self.service.dispatch("revert_workspace_change", {"change_id": written.change_id, "expected_revision": written.revision})
        self.assertEqual(target.read_text(encoding="utf8"), "external")
        self.assertFalse(self.service.recovery)
        self.assertFalse(self.service._unresolved_intents())
        self.assertFalse(self.service.queue_paused)
        await self.submit("safe-after-conflict", "Can continue")
        await self.finish()
        self.assertEqual(self.calls, ["Can continue"])

    async def test_successful_navigation_advances_projection_watermark(self):
        before = self.service.state()
        created = await self.service.dispatch("new_session")
        self.assertGreater(created["seq"], before["seq"])
        resumed = await self.service.dispatch("resume_session", {"session_id": self.session})
        self.assertGreater(resumed["seq"], created["seq"])
        self.assertEqual(resumed["session_id"], self.session)
        self.assertEqual(self.events[-1]["state"]["seq"], resumed["seq"])

    async def test_edit_remove_capacity_and_session_target_validation(self):
        await self.service.dispatch("pause_queue")
        await self.submit("edited", "Original")
        await self.service.dispatch("update_input", {"command_id": "edited", "action": "edit", "prompt": "Edited"})
        receipt = (await self.submit("edited", "Original"))["receipt"]
        self.assertEqual(receipt["prompt"], "Edited")
        with self.assertRaises(ValueError):
            await self.submit("edited", "Edited")
        for index in range(19):
            await self.submit(f"pending-{index}", f"Pending {index}")
        with self.assertRaisesRegex(ValueError, "20"):
            await self.submit("overflow", "Overflow")
        await self.service.dispatch("update_input", {"command_id": "pending-0", "action": "remove"})
        self.assertEqual((await self.receipt("pending-0"))["status"], "cancelled")
        await self.submit("replacement", "Replacement")
        with self.assertRaises(ValueError):
            await self.service.dispatch("submit_input", {"command_id": "wrong", "prompt": "Wrong", "session_id": "f" * 32})
        with self.assertRaises(ValueError):
            await self.submit("invalid", "x" * 200001)
        self.assertEqual(self.calls, [])

    async def test_projection_sequence_ids_and_receipts_do_not_leak_secrets(self):
        await self.service.dispatch("configure", {"api_key": "FIXTURE_PRIVATE_KEY"})
        async def delta(agent, prompt):
            agent.sink({"type": "text_delta", "text": "one"})
            agent.sink({"type": "text_delta", "text": "two"})
        self.runner = delta
        accepted = await self.submit("projection", "FIXTURE_PRIVATE_KEY")
        self.assertNotIn("FIXTURE_PRIVATE_KEY", json.dumps(accepted))
        await self.finish()
        sequences = [event["seq"] for event in self.events]
        self.assertEqual(sequences, list(range(1, len(sequences) + 1)))
        state = self.service.state()
        self.assertEqual(self.service.state()["seq"], state["seq"])
        for event in self.events:
            self.assertEqual(event["stream_id"], self.service.stream_id)
            self.assertIn(event["session_id"], ("", self.session))
            if event["type"] == "state":
                self.assertEqual(event["state"]["seq"], event["seq"])
        deltas = [event for event in self.events if event["type"] == "text_delta"]
        self.assertEqual(deltas[0]["message_id"], deltas[1]["message_id"])
        self.assertEqual(deltas[0]["message_id"], state["messages"][1]["id"])
        self.assertEqual(state["messages"][1]["content"], "onetwo")
        self.assertNotIn("FIXTURE_PRIVATE_KEY", json.dumps(await self.receipt("projection")))

    async def test_attachment_is_frozen_hash_bound_and_not_expanded_in_transcript(self):
        target = self.workspace / "source.txt"
        target.write_text("Original attached contents", encoding="utf8")
        revision = hashlib.sha256(target.read_bytes()).hexdigest()
        await self.service.dispatch("pause_queue")
        accepted = await self.submit("attachment", "Review source", attachments=[{"path": "source.txt", "revision": revision}])
        self.assertNotIn("Original attached contents", json.dumps(accepted))
        target.write_text("Changed externally", encoding="utf8")
        repeat = await self.submit("attachment", "Review source", attachments=[{"path": "source.txt", "revision": revision}])
        self.assertEqual(repeat["receipt"]["content_hash"], accepted["receipt"]["content_hash"])
        changed_revision = hashlib.sha256(target.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, "不同内容"):
            await self.submit("attachment", "Review source", attachments=[{"path": "source.txt", "revision": changed_revision}])
        with self.assertRaises(ValueError):
            await self.submit("stale", "Review source", attachments=[{"path": "source.txt", "revision": revision}])
        await self.service.dispatch("resume_queue")
        await self.finish()
        self.assertIn("Original attached contents", self.calls[0])
        self.assertNotIn("Changed externally", self.calls[0])
        user_message = self.service.state()["messages"][0]
        self.assertEqual(user_message["content"], "Review source")
        self.assertEqual(user_message["attachments"][0]["revision"], revision)

    async def test_real_process_kill_restores_unknown_without_second_model_request(self):
        entered, release = threading.Event(), threading.Event()
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                requests.append(self.rfile.read(int(self.headers["Content-Length"])))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(b'data: {"choices":[{"delta":{"content":"in progress"},"finish_reason":null}]}\n\n')
                self.wfile.flush()
                entered.set()
                release.wait(10)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        processes = []

        def launch():
            env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
            env.pop("OPENAI_API_KEY", None)
            env.pop("ANTHROPIC_API_KEY", None)
            process = subprocess.Popen([sys.executable, "-B", "-u", "-m", "src.desktop_service"],
                                       cwd=Path(__file__).resolve().parents[1], env=env,
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                       text=True, encoding="utf8")
            processes.append(process)
            responses = queue.Queue()
            threading.Thread(target=lambda: [responses.put(json.loads(line)) for line in process.stdout], daemon=True).start()
            counter = 0

            def rpc(method, params=None):
                nonlocal counter
                counter += 1
                process.stdin.write(json.dumps({"id": counter, "method": method, "params": params or {}}) + "\n")
                process.stdin.flush()
                while True:
                    response = responses.get(timeout=10)
                    if response.get("id") == counter:
                        if "error" in response:
                            raise AssertionError(response["error"])
                        return response["result"]

            return process, rpc

        try:
            process, rpc = launch()
            config = {"base_url": f"http://127.0.0.1:{server.server_port}", "model": "local-kill-fixture", "security_profile": "open"}
            rpc("initialize", {"data_dir": str(self.root / "process-data"), "config": config})
            session = rpc("open_workspace", {"cwd": str(self.workspace)})["session_id"]
            rpc("submit_input", {"session_id": session, "command_id": "kill-window", "prompt": "Only one model request"})
            self.assertTrue(entered.wait(5))
            rpc("submit_input", {"session_id": session, "command_id": "waiting", "prompt": "Remain paused"})
            process.kill()
            process.wait(timeout=5)
            restarted, rpc2 = launch()
            rpc2("initialize", {"data_dir": str(self.root / "process-data"), "config": config})
            state = rpc2("open_workspace", {"cwd": str(self.workspace), "session_id": session})
            self.assertTrue(state["queue_paused"])
            self.assertEqual(state["status"], "idle")
            self.assertEqual([item["id"] for item in state["input_queue"]], ["waiting"])
            receipt = rpc2("get_input_receipt", {"session_id": session, "command_id": "kill-window"})["receipt"]
            self.assertEqual(receipt["status"], "interrupted")
            retry = rpc2("submit_input", {"session_id": session, "command_id": "kill-window", "prompt": "Only one model request"})
            self.assertEqual(retry["receipt"]["status"], "interrupted")
            self.assertEqual(len(requests), 1)
            rpc2("shutdown")
            restarted.wait(timeout=5)
        finally:
            release.set()
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                process.stdin.close()
                process.stdout.close()
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
