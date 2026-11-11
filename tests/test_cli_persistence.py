"""Real local HTTP/CLI process contracts, plus session ownership on SDK switches."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.cli import handle_slash_command, select_session
from src.parser import Command
from src.runtime import create_agent
from src.session import SessionStore


ROOT = Path(__file__).resolve().parents[1]


class CLIProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="thinkflow-cli-")
        self.root = Path(self.temp.name)
        self.store = SessionStore(str(self.root / "session.json"), cwd=str(self.root))
        self.release = threading.Event()
        self.requests = []
        self.status = 200
        self.text = "complete"
        self.finish = "stop"
        self.hold = False
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                fixture.requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                self.send_response(fixture.status)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                if fixture.status != 200:
                    self.wfile.write(b"fixture HTTP failure")
                    return
                try:
                    payload = {"choices": [{"delta": {"content": fixture.text + "\n"}, "finish_reason": None}]}
                    self.wfile.write(("data: " + json.dumps(payload) + "\n\n").encode())
                    self.wfile.flush()
                    if fixture.hold:
                        deadline = time.monotonic() + 20
                        while not fixture.release.wait(.1) and time.monotonic() < deadline:
                            self.wfile.write(b": fixture heartbeat\n\n")
                            self.wfile.flush()
                    payload = {"choices": [{"delta": {}, "finish_reason": fixture.finish}]}
                    self.wfile.write(("data: " + json.dumps(payload) + "\n\ndata: [DONE]\n\n").encode())
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.processes = []

    def tearDown(self):
        for process in self.processes:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=10)
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.temp.cleanup()

    def start(self, *extra, interrupt=False):
        env = dict(os.environ, THINKFLOW_HOME=str(self.root / "home"), PYTHONIOENCODING="utf-8")
        env["PYTHONPATH"] = str(ROOT)
        entry = ["-m", "src.cli"]
        if interrupt:
            entry = ["-c", "import _thread, threading; from src.cli import main; "
                     "threading.Timer(2, _thread.interrupt_main).start(); main()"]
        command = [sys.executable, *entry, "--prompt", "fixture task",
                   "--cwd", str(self.root), "--session", str(self.store.path),
                   "--api-key", "local-fixture", "--model", "fixture", "--provider", "openai",
                   "--base-url", f"http://127.0.0.1:{self.server.server_port}",
                   "--no-context", "--no-system-prompt", "--sandbox", "open",
                   "--set", "max_retries=1", "--set", "retry_backoff_seconds=0",
                   "--set", "max_run_turns=2", *extra]
        process = subprocess.Popen(command, cwd=self.root, env=env, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, encoding="utf-8")
        self.processes.append(process)
        return process

    def wait_for(self, predicate):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.02)
        self.fail("CLI did not reach the requested persistence boundary")

    def journal_events(self):
        if not self.store.journal_path.exists():
            return []
        return [json.loads(line) for line in self.store.journal_path.read_text(encoding="utf-8").splitlines()]

    def test_success_and_http_failure_exit_codes(self):
        for status, expected, requests in ((200, 0, 1), (401, 1, 1), (503, 1, 2)):
            with self.subTest(status=status):
                self.status = status
                self.requests.clear()
                process = self.start()
                stdout, stderr = process.communicate(timeout=15)
                self.assertEqual(process.returncode, expected, (stdout, stderr))
                self.assertEqual(len(self.requests), requests)
                if status != 200:
                    self.assertIn(f"HTTP {status}", stderr)

    def test_budget_stop_is_nonzero(self):
        self.finish = "length"
        process = self.start("--set", "max_run_turns=1")
        _, stderr = process.communicate(timeout=15)
        self.assertEqual(process.returncode, 1)
        self.assertIn("max_run_turns", stderr)

    def test_keyboard_interrupt_exits_130(self):
        self.hold = True
        process = self.start(interrupt=True)
        out, err = process.communicate(timeout=15)
        self.assertEqual(process.returncode, 130, (out, err))
        self.assertEqual(len(self.requests), 1)

    def test_completed_receipt_survives_kill_during_open_stream(self):
        self.text = '<tf-write id="1" path="effect.txt">persisted effect</tf-write>'
        self.hold = True
        process = self.start()
        self.wait_for(lambda: any(e["type"] == "tool_completed" for e in self.journal_events()))
        self.assertIsNone(process.poll())
        process.kill()
        stdout, _ = process.communicate(timeout=10)
        self.assertEqual((self.root / "effect.txt").read_text(), "persisted effect")
        self.assertIn("write", stdout)  # Persistence did not disable the terminal renderer.
        snapshot = self.store.load()
        self.assertEqual([(r["id"], r["status"]) for r in snapshot["context"]["records"]],
                         [("1", "success")])
        self.assertIn("1", snapshot["executed_ids"])
        self.hold = False
        self.text = "recovered"
        resumed = self.start("--resume")
        out, err = resumed.communicate(timeout=15)
        self.assertEqual(resumed.returncode, 0, (out, err))
        self.assertIn("persisted effect", json.dumps(self.requests[-1]))
        self.assertEqual((self.root / "effect.txt").read_text(), "persisted effect")

    def test_unresolved_intent_survives_process_kill(self):
        # A real long-running local command creates an intent without completing.
        self.text = '<tf-python id="1">import time\ntime.sleep(30)</tf-python>'
        self.hold = True
        process = self.start()
        self.wait_for(lambda: any(e["type"] == "tool_started" for e in self.journal_events()))
        process.kill()
        process.communicate(timeout=10)
        snapshot = self.store.load()
        record = next(r for r in snapshot["context"]["records"] if r["id"] == "1")
        self.assertEqual(record["status"], "unknown")
        self.assertIn("1", snapshot["executed_ids"])
        self.assertEqual(snapshot["command_fingerprints"]["1"], record["content_hash"])
        self.assertEqual(snapshot["context"]["next_stamp"], 2)
        self.hold = False
        resumed = self.start("--resume")
        out, err = resumed.communicate(timeout=15)
        self.assertEqual(resumed.returncode, 1, (out, err))
        self.assertIn("已停止自动继续", err)
        self.assertEqual(len([e for e in self.journal_events() if e["type"] == "tool_started"]), 1)

    def test_no_autosave_does_not_create_session_or_journal(self):
        self.text = '<tf-write id="1" path="effect.txt">effect</tf-write>'
        process = self.start("--no-autosave")
        stdout, stderr = process.communicate(timeout=15)
        self.assertEqual(process.returncode, 0, (stdout, stderr))
        self.assertTrue((self.root / "effect.txt").exists())
        self.assertFalse(self.store.path.exists())
        self.assertFalse(self.store.journal_path.exists())


class SDKPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_binding_switch_new_and_unknown_replay(self):
        with tempfile.TemporaryDirectory(prefix="thinkflow-sdk-session-") as temp:
            root = Path(temp)
            config = {"context": {"enabled": False}, "security": {"profile": "open"}}
            first = SessionStore(str(root / "first.json"), cwd=temp)
            second = SessionStore(str(root / "second.json"), cwd=temp)
            events = []
            agent = create_agent(config, "", cwd=temp, event_sink=events.append, session_store=first)
            try:
                await agent._execute_command(Command("one", "write", path="first.txt", content="first"))
                original = first.path.read_bytes()
                second.save(agent.to_snapshot())  # A fork keeps source receipts and independent outputs.
                select_session(agent, second.path, first)
                await agent._execute_command(Command("two", "write", path="second.txt", content="second"))
                self.assertEqual((root / "first.json").read_bytes(), original)
                self.assertEqual(first.path, second.path)
                self.assertTrue(any(e["type"] == "tool_completed" for e in events))
                self.assertEqual(len(second.load()["context"]["records"]), 2)
                command = Command("pending", "append", path="second.txt", content="duplicate")
                from src.agent_loop import _command_hash
                agent._remember_command(command.id, _command_hash(command))
                agent._tool_event("tool_started", command, "text")
                agent.load_snapshot(first.load())
                agent.executor.execute = AsyncMock()
                result = await agent._execute_command(command)
                self.assertEqual(result.status_str, "unknown")
                agent.executor.execute.assert_not_awaited()
                self.assertEqual((root / "second.txt").read_text(), "second")
                handle_slash_command("/new", agent, first)
                self.assertEqual(first.load()["context"]["records"], [])
                self.assertEqual(len(agent.events.observers), 1)
            finally:
                await agent.close()


if __name__ == "__main__":
    unittest.main()
