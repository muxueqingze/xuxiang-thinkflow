"""Second-round independent core checks. Local files and mocked model streams only."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.agent_loop import AgentConfig, AgentLoop
from src.changes import ChangeStore, content_revision
from src.executor import Executor
from src.parser import Command
from src.security import SecurityPolicy
from src.streaming import EventType, StreamEvent


class RoundTwoCoreTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="thinkflow-round2-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.cwd = self.root / "workspace"
        self.cwd.mkdir()
        self.store = ChangeStore(self.root / "private" / "changes", self.cwd)
        self.executor = Executor(str(self.cwd), change_store=self.store)

    async def test_sha_conflict_is_recoverable_after_fresh_read_without_receipt_or_overwrite(self):
        target = self.cwd / "document.txt"
        target.write_bytes(b"AAAA")
        info = target.stat()
        observed = await self.executor.read("document.txt")
        target.write_bytes(b"BBBB")
        os.utime(target, ns=(info.st_atime_ns, info.st_mtime_ns))
        result = await self.executor.execute(Command("1", "write", path="document.txt", content="CCCC"))
        self.assertEqual(result.status_str, "conflict")
        self.assertNotEqual(observed.revision, content_revision(target.read_bytes()))
        self.assertEqual(target.read_bytes(), b"BBBB")
        self.assertEqual(self.store.list_changes(), [])
        await self.executor.read("document.txt")
        result = await self.executor.execute(Command("2", "write", path="document.txt", content="CCCC"))
        self.assertTrue(result.success)
        self.assertEqual(target.read_bytes(), b"CCCC")
        self.assertEqual(self.store.list_changes()[0]["status"], "success")

    async def test_receipt_failure_and_external_race_remain_unknown_without_revert(self):
        with patch.object(self.store, "complete", side_effect=OSError("injected final receipt fault")):
            outcome = await self.executor.execute(Command("1", "write", path="first.txt", content="committed bytes"))
        self.assertEqual(outcome.status_str, "unknown")
        self.assertEqual((self.cwd / "first.txt").read_text(), "committed bytes")
        reopened = ChangeStore(self.store.root, self.cwd)
        self.assertEqual(reopened.list_changes()[0]["status"], "unknown")
        with self.assertRaisesRegex(ValueError, "unconfirmed"):
            reopened.revert(outcome.change_id, outcome.revision)
        begin = self.store.begin

        def racing(*args, **kwargs):
            identifier = begin(*args, **kwargs)
            (self.cwd / "second.txt").write_bytes(b"external bytes")
            return identifier

        with patch.object(self.store, "begin", side_effect=racing):
            outcome = await self.executor.execute(Command("2", "write", path="second.txt", content="model bytes"))
        self.assertEqual(outcome.status_str, "unknown")
        self.assertEqual((self.cwd / "second.txt").read_bytes(), b"external bytes")

    async def test_intent_failure_makes_no_parent_and_binary_copy_has_no_snapshots(self):
        with patch.object(self.store, "begin", side_effect=OSError("intent cannot commit")):
            result = await self.executor.execute(Command("1", "write", path="new/sub/file.txt", content="no"))
        self.assertEqual(result.status_str, "failed")
        self.assertFalse((self.cwd / "new").exists())
        (self.cwd / "binary.dat").write_bytes(b"\x00\xff\x10\x00")
        result = await self.executor.execute(Command("2", "copy", path="binary.dat", dest="copy.dat"))
        self.assertTrue(result.success)
        self.assertEqual((self.cwd / "copy.dat").read_bytes(), b"\x00\xff\x10\x00")
        meta = self.store.list_changes()[0]
        self.assertFalse(meta["can_diff"])
        self.assertFalse(meta["can_revert"])
        raw = json.loads((self.store.root / f"{result.change_id}.json").read_text())
        self.assertNotIn("snapshots", raw)

    async def test_sensitive_read_only_and_traversal_block_before_intent(self):
        (self.cwd / ".env").write_text("fixture=secret", encoding="utf8")
        blocked = await self.executor.execute(Command("1", "copy", path=".env", dest="copied.txt"))
        self.assertFalse(blocked.success)
        self.assertFalse((self.cwd / "copied.txt").exists())
        readonly = Executor(str(self.cwd), security=SecurityPolicy(allowed_roots=[str(self.cwd)], read_only=True), change_store=self.store)
        for tool, args in [("write", {"content": "x"}), ("mkdir", {}), ("copy", {"dest": "copy.txt"}), ("touch", {})]:
            result = await readonly.execute(Command("2", tool, path="readonly.txt", **args))
            self.assertFalse(result.success)
        result = await self.executor.execute(Command("3", "write", path="../escaped.txt", content="x"))
        self.assertFalse(result.success)
        self.assertFalse((self.root / "escaped.txt").exists())
        self.assertEqual(self.store.list_changes(), [])

    async def test_revert_revert_requires_current_revision_and_preserves_later_edit(self):
        target = self.cwd / "text.txt"
        target.write_text("old", encoding="utf8")
        await self.executor.read("text.txt")
        written = await self.executor.execute(Command("1", "write", path="text.txt", content="new"))
        store = ChangeStore(self.store.root, self.cwd)
        restored = store.revert(written.change_id, written.revision)
        self.assertEqual(target.read_text(), "old")
        target.write_text("USER", encoding="utf8")
        with self.assertRaisesRegex(ValueError, "revision_conflict"):
            store.revert(restored["id"], restored["expected_revision"])
        self.assertEqual(target.read_text(), "USER")
        self.assertEqual(len(store.list_changes()), 2)

    async def test_unknown_side_effect_stops_agent_before_second_model_turn(self):
        events = []
        agent = AgentLoop(AgentConfig(cwd=str(self.cwd), security=SecurityPolicy(allowed_roots=[str(self.cwd)])), event_sink=events.append)
        agent.executor.set_change_store(self.store)
        response = type("Response", (), {"aclose": AsyncMock()})()
        agent._send_stream_request = AsyncMock(return_value=response)
        turns = []

        async def stream(_response):
            turns.append(len(turns) + 1)
            command = ('<tf-write id="101" path="result.txt">A</tf-write>' if len(turns) == 1
                       else '<tf-append id="102" path="result.txt">B</tf-append>')
            yield StreamEvent(type=EventType.THINKING_DELTA, thinking_text=command)
            yield StreamEvent(type=EventType.MESSAGE_STOP, finish_reason="stop")

        agent._process_stream = stream
        complete = self.store.complete
        failures = 0

        def fail_first(identifier):
            nonlocal failures
            failures += 1
            if failures == 1:
                raise OSError("final receipt unavailable after write")
            return complete(identifier)

        try:
            with patch.object(self.store, "complete", side_effect=fail_first):
                await agent.run("write one artifact")
            self.assertEqual(turns, [1], f"unknown effect was followed by another model turn: {turns}; bytes={(self.cwd / 'result.txt').read_bytes()!r}")
            self.assertEqual((self.cwd / "result.txt").read_bytes(), b"A")
            self.assertTrue(agent.last_error)
        finally:
            await agent.close()

    async def test_native_unknown_stops_batch_and_next_model_turn(self):
        events = []
        agent = AgentLoop(AgentConfig(cwd=str(self.cwd), security=SecurityPolicy(allowed_roots=[str(self.cwd)])), event_sink=events.append)
        agent.executor.set_change_store(self.store)
        response = type("Response", (), {"aclose": AsyncMock()})()
        agent._send_stream_request = AsyncMock(return_value=response)
        turns = []

        async def stream(_response):
            turns.append(len(turns) + 1)
            for index, identifier, path in [(0, "native201", "first.txt"), (1, "native202", "second.txt")]:
                yield StreamEvent(type=EventType.TOOL_USE_START, tool_id=identifier, tool_name="write", tool_index=index)
                yield StreamEvent(type=EventType.TOOL_USE_DELTA, tool_index=index, tool_input=json.dumps({"path": path, "content": "native content"}))
            yield StreamEvent(type=EventType.MESSAGE_STOP, finish_reason="tool_calls")

        agent._process_stream = stream
        try:
            with patch.object(self.store, "complete", side_effect=OSError("final native receipt unavailable")):
                await agent.run("write two files")
            self.assertEqual(turns, [1])
            self.assertEqual((self.cwd / "first.txt").read_text(), "native content")
            self.assertFalse((self.cwd / "second.txt").exists())
            self.assertEqual([record.status for record in agent.context.records], ["unknown", "skipped"])
            self.assertIn("核对", agent.last_error)
            self.assertEqual(agent.stopped_reason, "unconfirmed_side_effect")
        finally:
            await agent.close()

    async def test_large_preview_utf8_and_mutation_limits_hold_under_independent_spy(self):
        target = self.cwd / "large.txt"
        with target.open("wb") as stream:
            stream.write(b"large fixture\n")
            stream.truncate(9 * 1024 * 1024)
        with patch.object(Path, "read_bytes", side_effect=AssertionError("unbounded file read forbidden")):
            preview = await self.executor.read("large.txt")
            self.assertTrue(preview.success, preview.error)
            self.assertTrue(preview.truncated)
            self.assertFalse(preview.revision)
            self.assertLessEqual(len(preview.content), 200000)
            result = await self.executor.execute(Command("1", "copy", path="large.txt", dest="too-large.txt"))
            self.assertFalse(result.success)
            self.assertIn("file_too_large", result.error)
            self.assertFalse((self.cwd / "too-large.txt").exists())
            result = await self.executor.execute(Command("2", "write", path="large.txt", content="overwrite"))
            self.assertFalse(result.success)
            self.assertIn("file_too_large", result.error)
        self.assertEqual(target.stat().st_size, 9 * 1024 * 1024)
        self.assertFalse(self.store.list_changes())
        (self.cwd / "utf8.txt").write_text("甲" * 20, encoding="utf8")
        self.executor.max_read_chars = 3
        preview = await self.executor.read("utf8.txt")
        self.assertTrue(preview.success, preview.error)
        self.assertEqual(preview.content, "甲" * 3)
        self.assertTrue(preview.truncated)
        self.assertFalse(preview.revision)

    async def test_open_profile_without_store_and_desktop_receipts_have_different_path_scope(self):
        security = SecurityPolicy.from_config({"security": {"profile": "open"}}, str(self.cwd))
        direct = Executor(str(self.cwd), security=security)
        target = self.root / "outside-direct.txt"
        result = await direct.execute(Command("1", "write", path=str(target), content="direct caller allowed"))
        self.assertTrue(result.success, result.error)
        guarded = Executor(str(self.cwd), security=security, change_store=self.store)
        other = self.root / "outside-desktop.txt"
        result = await guarded.execute(Command("2", "write", path=str(other), content="blocked by receipt scope"))
        self.assertFalse(result.success)
        self.assertFalse(other.exists())
        self.assertTrue((await guarded.read(str(target))).success)


if __name__ == "__main__":
    unittest.main(verbosity=2)
