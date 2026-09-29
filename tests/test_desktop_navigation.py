"""Session metadata and workspace restoration use only private test directories."""
import asyncio
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.desktop_service import DesktopService
from src import desktop_service
from test_desktop_service import FakeAgent


class DesktopNavigationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="thinkflow-navigation-")
        # Windows CI can expose TEMP through an 8.3 alias (RUNNER~1). Match the
        # service's canonical paths so fault injection targets the intended save.
        self.root = Path(self.temp.name).resolve()
        self.workspace = self.root / "workspace"
        self.other = self.root / "other"
        self.workspace.mkdir()
        self.other.mkdir()
        self.runner = None
        self.agents = []
        self.services = []
        self.prompt_patch = patch.object(desktop_service, "resolve_system_prompt", return_value="Test only")
        self.prompt_patch.start()

        def create(config, system, cwd, *, event_sink, approval_handler):
            agent = FakeAgent(event_sink, approval_handler, self.runner)
            self.agents.append(agent)
            return agent

        self.agent_patch = patch.object(desktop_service, "create_agent", side_effect=create)
        self.agent_patch.start()
        self.service = await self.start_service()
        self.first = (await self.open(self.workspace))["session_id"]

    async def asyncTearDown(self):
        try:
            for service in self.services:
                await service.close()
        finally:
            self.agent_patch.stop()
            self.prompt_patch.stop()
            self.temp.cleanup()

    async def start_service(self):
        service = DesktopService()
        self.services.append(service)
        await service.dispatch("initialize", {"data_dir": str(self.root / "data"),
                                             "config": {"base_url": "http://127.0.0.1:1", "model": "local"}})
        return service

    async def open(self, workspace, session_id=None):
        return await self.service.dispatch("open_workspace", {"cwd": str(workspace), "session_id": session_id})

    def metadata(self, state, identifier):
        return next(item for item in state["sessions"] if item["id"] == identifier)

    async def test_metadata_persists_without_switching_or_replacing_history(self):
        await self.service.dispatch("run", {"prompt": "Original transcript"})
        await self.service.task
        before = copy.deepcopy(self.service.store.load())
        second = (await self.service.dispatch("new_session"))["session_id"]
        state = await self.service.dispatch("update_session", {"session_id": self.first, "title": "  项目计划  ", "pinned": True, "archived": True})
        self.assertEqual(state["session_id"], second)
        self.assertEqual(state["messages"], [])
        meta = self.metadata(state, self.first)
        self.assertEqual((meta["title"], meta["pinned"], meta["archived"]), ("项目计划", True, True))
        saved = json.loads(self.service._session_path(self.first).read_text(encoding="utf8"))
        self.assertEqual(saved["messages"], before["messages"])
        self.assertEqual(saved["desktop"]["transcript"], before["desktop"]["transcript"])
        state = await self.service.dispatch("resume_session", {"session_id": self.first})
        self.assertTrue(self.service.archived)
        with self.assertRaisesRegex(ValueError, "已归档"):
            await self.service.dispatch("run", {"prompt": "Must not run"})
        state = await self.service.dispatch("update_session", {"session_id": self.first, "archived": False})
        await self.service.dispatch("run", {"prompt": "Continue after unarchive"})
        await self.service.task
        await self.service.close()
        restarted = await self.start_service()
        state = await restarted.dispatch("open_workspace", {"cwd": str(self.workspace), "session_id": self.first})
        self.assertEqual(state["session_id"], self.first)
        self.assertTrue(self.metadata(state, self.first)["pinned"])
        self.assertEqual(self.metadata(state, self.first)["title"], "项目计划")
        self.assertEqual(state["messages"][-2]["content"], "Continue after unarchive")

    async def test_current_archiving_keeps_view_and_fork_is_unarchived(self):
        state = await self.service.dispatch("update_session", {"session_id": self.first, "archived": True, "pinned": True})
        self.assertEqual(state["session_id"], self.first)
        fork = await self.service.dispatch("fork_session")
        self.assertFalse(self.metadata(fork, fork["session_id"])["archived"])
        self.assertFalse(self.metadata(fork, fork["session_id"])["pinned"])
        self.assertTrue(self.metadata(fork, self.first)["archived"])

    async def test_legacy_defaults_and_workspace_selected_session_restore(self):
        original = self.service.store.load()
        original["desktop"].pop("pinned")
        original["desktop"].pop("archived")
        self.service.store.save(original, history=False)
        meta = self.metadata(self.service.state(), self.first)
        self.assertIs(meta["pinned"], False)
        self.assertIs(meta["archived"], False)
        second = (await self.service.dispatch("new_session"))["session_id"]
        await self.open(self.other)
        state = await self.open(self.workspace, self.first)
        self.assertEqual(state["session_id"], self.first)
        self.assertIn(second, [item["id"] for item in state["sessions"]])
        await self.open(self.other)
        with self.assertRaises(FileNotFoundError):
            await self.service.dispatch("update_session", {"session_id": self.first, "pinned": True})
        self.assertNotIn(self.first, [item["id"] for item in self.service.state()["sessions"]])

    async def test_archived_restore_falls_back_and_all_archived_creates_new(self):
        second = (await self.service.dispatch("new_session"))["session_id"]
        await self.service.dispatch("update_session", {"session_id": second, "archived": True})
        state = await self.open(self.workspace, second)
        self.assertEqual(state["session_id"], self.first)
        self.assertIn("已归档", state["navigation_warning"])
        await self.service.dispatch("update_session", {"session_id": self.first, "archived": True})
        state = await self.open(self.workspace, self.first)
        self.assertNotIn(state["session_id"], [self.first, second])
        self.assertEqual(len(state["sessions"]), 3)
        self.assertTrue(self.service._session_path(self.first).exists())

    async def test_corrupt_preferred_session_is_preserved_and_missing_path_does_not_switch(self):
        second = (await self.service.dispatch("new_session"))["session_id"]
        await self.open(self.other)
        await self.open(self.workspace, self.first)
        bad = self.service._session_path(second)
        bad.write_text('{"broken":', encoding="utf8")
        state = await self.open(self.workspace, second)
        self.assertEqual(state["session_id"], self.first)
        self.assertTrue(state["navigation_warning"])
        self.assertEqual(bad.read_text(encoding="utf8"), '{"broken":')
        before = self.service.state()
        with self.assertRaises(ValueError):
            await self.open(self.root / "missing")
        self.assertEqual(self.service.state(), before)

    async def test_restart_recovery_restores_without_automatic_execution(self):
        self.service._on_agent_event({"type": "tool_started", "channel": "text", "id": "pending", "tool": "write", "path": "unknown.txt"})
        await self.service.close()
        restarted = await self.start_service()
        state = await restarted.dispatch("open_workspace", {"cwd": str(self.workspace), "session_id": self.first})
        self.assertEqual(state["session_id"], self.first)
        self.assertTrue(state["recovery_required"])
        self.assertIsNone(restarted.task)
        self.assertFalse((self.workspace / "unknown.txt").exists())
        with self.assertRaisesRegex(ValueError, "核对"):
            await restarted.dispatch("run", {"prompt": "Continue"})

    async def test_schema_invalid_snapshot_cannot_replace_current_agent_and_is_skipped(self):
        second = (await self.service.dispatch("new_session"))["session_id"]
        bad = self.service.store.path
        await self.service.dispatch("resume_session", {"session_id": self.first})
        current_agent = self.service.agent
        current_store = self.service.store
        snapshot = json.loads(bad.read_text(encoding="utf8"))
        snapshot["desktop"]["transcript"] = None
        bad.write_text(json.dumps(snapshot), encoding="utf8")
        with self.assertRaisesRegex(ValueError, "损坏"):
            await self.service.dispatch("resume_session", {"session_id": second})
        self.assertIs(self.service.agent, current_agent)
        self.assertIs(self.service.store, current_store)
        self.assertFalse(current_agent.closed)
        snapshot["desktop"]["transcript"] = []
        snapshot["messages"] = ["invalid runtime message"]
        bad.write_text(json.dumps(snapshot), encoding="utf8")
        with self.assertRaises(AttributeError):
            await self.service.dispatch("resume_session", {"session_id": second})
        self.assertIs(self.service.agent, current_agent)
        self.assertFalse(current_agent.closed)
        state = await self.open(self.workspace, second)
        self.assertEqual(state["session_id"], self.first)
        self.assertIn("跳过", state["navigation_warning"])
        self.assertEqual(json.loads(bad.read_text(encoding="utf8"))["messages"], ["invalid runtime message"])

    async def test_explicit_new_session_title_is_not_automatically_replaced(self):
        await self.service.dispatch("update_session", {"session_id": self.first, "title": "新会话"})
        await self.service.dispatch("run", {"prompt": "A different title"})
        await self.service.task
        self.assertEqual(self.service.title, "新会话")
        await self.service.dispatch("new_session")
        await self.service.dispatch("resume_session", {"session_id": self.first})
        await self.service.dispatch("run", {"prompt": "Still should not replace"})
        await self.service.task
        self.assertEqual(self.service.title, "新会话")

    async def test_failed_workspace_snapshot_save_restores_original_runtime(self):
        from src.session import SessionStore
        original_agent, original_store = self.service.agent, self.service.store
        original_state = self.service.state()
        original_save = SessionStore.save

        def fail_other(store, snapshot, **kwargs):
            if snapshot.get("desktop", {}).get("cwd") == str(self.other):
                raise OSError("Local simulated disk full")
            return original_save(store, snapshot, **kwargs)

        with patch.object(SessionStore, "save", new=fail_other):
            with self.assertRaisesRegex(OSError, "disk full"):
                await self.open(self.other)
        self.assertIs(self.service.agent, original_agent)
        self.assertIs(self.service.store, original_store)
        self.assertFalse(original_agent.closed)
        self.assertEqual(self.service.cwd, str(self.workspace))
        self.assertEqual(self.service.session_id, original_state["session_id"])
        await self.service.dispatch("run", {"prompt": "Still usable in original workspace"})
        await self.service.task
        self.assertEqual(self.service.store.load()["desktop"]["cwd"], str(self.workspace))

    async def test_invalid_metadata_and_busy_changes_leave_snapshot_unchanged(self):
        before = self.service.store.path.read_bytes()
        for params in ({"title": " "}, {"title": "x" * 121}, {"pinned": 1}, {"archived": "yes"}, {"cwd": str(self.other)}, {}):
            with self.subTest(params=params), self.assertRaises(ValueError):
                await self.service.dispatch("update_session", {"session_id": self.first, **params})
        with self.assertRaises(ValueError):
            await self.service.dispatch("update_session", {"session_id": "../outside", "pinned": True})
        self.assertEqual(self.service.store.path.read_bytes(), before)
        entered = asyncio.Event()

        async def block(agent, prompt):
            entered.set()
            await asyncio.Event().wait()

        self.service.agent.runner = block
        await self.service.dispatch("run", {"prompt": "Hold"})
        await asyncio.wait_for(entered.wait(), 2)
        during = self.service.store.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "停止"):
            await self.service.dispatch("update_session", {"session_id": self.first, "archived": True})
        self.assertEqual(self.service.store.path.read_bytes(), during)
        await self.service.dispatch("cancel")


if __name__ == "__main__":
    unittest.main(verbosity=2)
