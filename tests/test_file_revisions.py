"""File freshness + private change receipts; no provider/network dependency."""

import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.changes import ChangeStore, content_revision
from src.executor import Executor
from src.parser import Command


class FileRevisionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="thinkflow-revisions-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.cwd = self.root / "workspace"
        self.cwd.mkdir()
        self.store = ChangeStore(self.root / "session" / "changes", self.cwd)
        self.executor = Executor(cwd=str(self.cwd), change_store=self.store)
        self.counter = 0

    async def command(self, tool, path="a.txt", **kwargs):
        self.counter += 1
        return await self.executor.execute(Command(str(self.counter), tool, path=path, **kwargs))

    def seed(self, name="a.txt", data=b"old\n"):
        path = self.cwd / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    async def test_existing_file_requires_full_read_for_every_mutator(self):
        self.seed()
        self.seed("source.txt", b"source\n")
        cases = [("write", {"content": "new"}), ("append", {"content": "tail"}),
                 ("touch", {}), ("edit", {"old_text": "old", "new_text": "new"})]
        for tool, args in cases:
            with self.subTest(tool=tool):
                result = await self.command(tool, **args)
                self.assertFalse(result.success)
                self.assertIn("read_required", result.error)
        copied = await self.command("copy", "source.txt", dest="a.txt")
        self.assertIn("read_required", copied.error)
        self.assertEqual((self.cwd / "a.txt").read_bytes(), b"old\n")
        self.assertEqual(self.store.list_changes(), [])

    async def test_same_size_and_mtime_external_edit_is_conflict(self):
        path = self.seed(data=b"AAAA")
        old_stat = path.stat()
        self.assertTrue((await self.executor.read("a.txt")).success)
        path.write_bytes(b"BBBB")
        os.utime(path, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
        result = await self.command("write", content="CCCC")
        self.assertEqual(result.status, "conflict")
        self.assertIn("revision_conflict", result.error)
        self.assertEqual(path.read_bytes(), b"BBBB")

    async def test_new_file_and_own_writes_keep_streaming(self):
        results = [await self.command("write", content="first"),
                   await self.command("append", content=" next"),
                   await self.command("edit", old_text="first", new_text="final"),
                   await self.command("touch"),
                   await self.command("copy", dest="copy.txt"),
                   await self.command("mkdir", "nested/dir")]
        for result in results:
            self.assertTrue(result.success, result.error)
        self.assertEqual((self.cwd / "copy.txt").read_text(), "final next")
        self.assertEqual(len(self.store.list_changes()), 6)

    async def test_edit_and_copy_destination_reject_later_changes(self):
        path = self.seed()
        self.seed("source.txt", b"source\n")
        await self.executor.read("a.txt")
        path.write_bytes(b"user\n")
        edit = await self.command("edit", old_text="old", new_text="new")
        copy = await self.command("copy", "source.txt", dest="a.txt")
        self.assertIn("revision_conflict", edit.error)
        self.assertIn("revision_conflict", copy.error)
        await self.executor.read("a.txt")
        self.assertTrue((await self.command("copy", "source.txt", dest="a.txt")).success)
        self.assertEqual(path.read_bytes(), b"source\n")

    async def test_partial_redacted_and_directory_reads_do_not_grant(self):
        self.seed(data=b"long file")
        self.executor.max_read_chars = 3
        self.assertTrue((await self.executor.read("a.txt")).truncated)
        self.assertIn("read_required", (await self.command("write", content="new")).error)
        self.executor.max_read_chars = 200000
        self.seed("notes.txt", b"password=never-record-this")
        self.assertIn("[REDACTED]", (await self.executor.read("notes.txt")).content)
        self.assertIn("read_required", (await self.command("write", "notes.txt", content="new")).error)
        await self.executor.list_files(".")
        await self.executor.grep("long", ".")
        self.assertIn("read_required", (await self.command("append", content="tail")).error)

    async def test_attachment_grant_uses_original_hash_not_current_file(self):
        path = self.seed()
        frozen = content_revision(path.read_bytes())
        path.write_bytes(b"user change")
        self.executor.grant_read_revision("a.txt", frozen)
        self.assertIn("revision_conflict", (await self.command("write", content="new")).error)
        with self.assertRaises(ValueError):
            self.executor.grant_read_revision("a.txt", "not-a-sha")
        with self.assertRaises(PermissionError):
            self.executor.grant_read_revision("../outside.txt", frozen)

    async def test_external_delete_does_not_silently_recreate(self):
        path = self.seed()
        await self.executor.read("a.txt")
        path.unlink()
        self.assertIn("revision_conflict", (await self.command("write", content="new")).error)
        self.assertFalse(path.exists())

    async def test_cli_versions_work_without_store_and_legacy_is_explicit(self):
        self.seed()
        plain = Executor(cwd=str(self.cwd))
        command = Command("1", "write", path="a.txt", content="new")
        self.assertFalse((await plain.execute(command)).success)
        await plain.read("a.txt")
        self.assertTrue((await plain.execute(command)).success)
        trusted = Executor(cwd=str(self.cwd), require_read_revision=False)
        self.assertTrue((await trusted.execute(Command("2", "write", path="a.txt", content="legacy"))).success)

    async def test_intent_failure_prevents_file_and_parent_creation(self):
        with patch.object(self.store, "_save", side_effect=OSError("disk fault")):
            result = await self.command("write", "not-created/a.txt", content="new")
        self.assertFalse(result.success)
        self.assertFalse((self.cwd / "not-created").exists())

    async def test_finish_failure_stays_unknown_across_restart(self):
        with patch.object(self.store, "complete", side_effect=OSError("receipt fault")):
            result = await self.command("write", content="actual new bytes")
        self.assertFalse(result.success)
        self.assertEqual(result.status, "unknown")
        reopened = ChangeStore(self.store.root, self.cwd)
        entry = reopened.list_changes()[0]
        self.assertEqual(entry["status"], "unknown")
        self.assertFalse(entry["can_revert"])
        self.assertEqual((self.cwd / "a.txt").read_text(), "actual new bytes")
        with self.assertRaisesRegex(ValueError, "unconfirmed_change"):
            reopened.diff(entry["id"])

    async def test_intent_exists_before_atomic_file_mutation(self):
        from src import executor as executor_module
        original = executor_module.atomic_write_bytes
        observed = []
        def checked(path, data):
            entries = self.store.list_changes()
            observed.append(entries[0]["status"])
            self.assertFalse(Path(path).exists())
            original(path, data)
        with patch.object(executor_module, "atomic_write_bytes", side_effect=checked):
            self.assertTrue((await self.command("write", content="new")).success)
        self.assertEqual(observed, ["unknown"])

    async def test_external_write_after_intent_is_not_overwritten(self):
        path = self.seed()
        await self.executor.read("a.txt")
        begin = self.store.begin
        def racing_begin(*args, **kwargs):
            receipt = begin(*args, **kwargs)
            path.write_bytes(b"external after intent")
            return receipt
        with patch.object(self.store, "begin", side_effect=racing_begin):
            result = await self.command("write", content="model change")
        self.assertFalse(result.success)
        self.assertIn("revision_conflict", result.error)
        self.assertEqual(path.read_bytes(), b"external after intent")
        self.assertEqual(self.store.list_changes()[0]["status"], "unknown")

    async def test_private_snapshots_restart_diff_and_guarded_revert(self):
        path = self.seed(data=b"old without newline")
        await self.executor.read("a.txt")
        result = await self.command("write", content="new without newline")
        reopened = ChangeStore(self.store.root, self.cwd)
        entry = reopened.list_changes()[0]
        self.assertNotIn("snapshots", entry)
        self.assertNotIn("new without newline", json.dumps(entry))
        patch_text = reopened.diff(result.change_id)["diff"]
        self.assertIn("-old without newline\n", patch_text)
        self.assertIn("+new without newline\n", patch_text)
        reverted = reopened.revert(result.change_id, entry["after_hash"])
        self.assertEqual(reverted["operation"], "revert")
        self.assertEqual(reverted["undo_of"], result.change_id)
        self.assertEqual(path.read_bytes(), b"old without newline")
        # The model still knows its previous write, so it must read after a UI undo.
        self.assertIn("revision_conflict", (await self.command("append", content="tail")).error)

    async def test_revert_refuses_external_edit_and_wrong_expected_revision(self):
        path = self.seed()
        await self.executor.read("a.txt")
        result = await self.command("write", content="new")
        with self.assertRaisesRegex(ValueError, "revision_conflict"):
            self.store.revert(result.change_id, "0" * 64)
        path.write_bytes(b"user later edit")
        with self.assertRaisesRegex(ValueError, "revision_conflict"):
            self.store.revert(result.change_id, content_revision(b"new"))
        self.assertEqual(path.read_bytes(), b"user later edit")

    async def test_revert_of_revert_refuses_later_external_changes(self):
        path = self.seed()
        await self.executor.read("a.txt")
        changed = await self.command("write", content="new")
        restored = self.store.revert(changed.change_id, content_revision(b"new"))
        path.write_bytes(b"external")
        with self.assertRaisesRegex(ValueError, "revision_conflict"):
            self.store.revert(restored["id"], restored["after_hash"])

    async def test_created_files_never_deleted_by_revert(self):
        result = await self.command("write", content="new")
        entry = self.store.list_changes()[0]
        self.assertTrue(entry["can_diff"])
        self.assertFalse(entry["can_revert"])
        with self.assertRaisesRegex(ValueError, "new_file_requires_manual_recovery"):
            self.store.revert(result.change_id, entry["after_hash"])
        self.assertTrue((self.cwd / "a.txt").exists())

    async def test_sensitive_binary_and_large_changes_have_no_snapshots(self):
        cases = [("config.json", b"{}", "sensitive_path"),
                 ("notes.txt", b"password=dummysecret", "sensitive_content"),
                 ("binary.bin", b"\x00\xff", "binary_file"),
                 ("large.txt", b"x" * (1024 * 1024 + 1), "snapshot_too_large")]
        for name, data, reason in cases:
            with self.subTest(name=name):
                receipt = self.store.begin(name, "write", None, data)
                self.seed(name, data)
                self.store.complete(receipt)
                raw = json.loads((self.store.root / f"{receipt}.json").read_text())
                self.assertNotIn("snapshots", raw)
                self.assertEqual(raw["unavailable_reason"], reason)
                with self.assertRaises(ValueError):
                    self.store.diff(receipt)
                with self.assertRaises(ValueError):
                    self.store.revert(receipt, content_revision(data))

    async def test_traversal_and_store_tampering_are_refused(self):
        outside = await self.command("write", "../outside.txt", content="no")
        self.assertFalse(outside.success)
        with self.assertRaises(PermissionError):
            self.store.begin("../outside.txt", "write", None, b"no")
        with self.assertRaises(ValueError):
            self.store.diff("../../session")
        result = await self.command("write", content="new")
        receipt = self.store.root / f"{result.change_id}.json"
        entry = json.loads(receipt.read_text())
        entry["path"] = "../outside.txt"
        receipt.write_text(json.dumps(entry))
        with self.assertRaises(PermissionError):
            self.store.diff(result.change_id)

    async def test_symbolic_alias_and_parent_path_are_refused(self):
        target = self.seed()
        link = self.cwd / "alias.txt"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"symlinks unavailable: {error}")
        self.assertFalse((await self.executor.read("alias.txt")).success)
        self.assertFalse((await self.command("write", "alias.txt", content="no")).success)
        directory_link = self.cwd / "alias-dir"
        directory_link.symlink_to(self.cwd, target_is_directory=True)
        self.assertFalse((await self.command("write", "alias-dir/new.txt", content="no")).success)
        self.assertFalse((self.cwd / "new.txt").exists())

    async def test_restarted_executor_does_not_infer_model_read_from_receipts(self):
        await self.command("write", content="new")
        restored = Executor(cwd=str(self.cwd), change_store=ChangeStore(self.store.root, self.cwd))
        result = await restored.execute(Command("later", "append", path="a.txt", content="tail"))
        self.assertIn("read_required", result.error)

    async def test_private_store_inside_workspace_cannot_be_read_by_model(self):
        store = ChangeStore(self.cwd / ".private-changes", self.cwd)
        self.executor.set_change_store(store)
        changed = await self.command("write", content="new")
        read = await self.executor.read(f".private-changes/{changed.change_id}.json")
        self.assertFalse(read.success)
        self.assertIn("change_store_path", read.error)

    async def test_large_file_preview_is_bounded_and_mutations_reject(self):
        path = self.cwd / "large.txt"
        with path.open("wb") as stream:
            stream.write(b"header\n")
            stream.truncate(9 * 1024 * 1024)
        with patch.object(Path, "read_bytes", side_effect=AssertionError("unbounded read forbidden")):
            preview = await self.executor.read("large.txt")
            self.assertTrue(preview.success, preview.error)
            self.assertTrue(preview.truncated)
            self.assertEqual(preview.revision, "")
            self.assertLessEqual(len(preview.content), self.executor.max_read_chars)
            result = await self.command("write", "large.txt", content="new")
            self.assertFalse(result.success)
            self.assertIn("file_too_large", result.error)
            copied = await self.command("copy", "large.txt", dest="copy.txt")
            self.assertFalse(copied.success)
            self.assertIn("file_too_large", copied.error)
        self.assertEqual(path.stat().st_size, 9 * 1024 * 1024)
        self.assertFalse((self.cwd / "copy.txt").exists())
        self.assertEqual(self.store.list_changes(), [])

    async def test_utf8_preview_cut_does_not_fail_mid_character(self):
        self.seed(data=("甲" * 20).encode("utf8"))
        self.executor.max_read_chars = 3
        result = await self.executor.read("a.txt")
        self.assertTrue(result.success, result.error)
        self.assertEqual(result.content, "甲" * 3)
        self.assertTrue(result.truncated)
        self.assertEqual(result.revision, "")


if __name__ == "__main__":
    unittest.main()
