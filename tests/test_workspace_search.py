"""Workspace file searches use local ignore rules and keep explicit paths usable."""

import asyncio
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.executor import Executor
from src.workspace_search import iter_entries


class WorkspaceSearchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = TemporaryDirectory(prefix="thinkflow-search-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.executor = Executor(str(self.root))
        self.addCleanup(self.executor.close)

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    async def test_ignore_scopes_and_explicit_targets(self):
        self.write(".gitignore", "node_modules/\n*.log\n!/root.log\n")
        self.write(".git/metadata.txt", "needle")
        self.write("node_modules/library.txt", "needle")
        self.write("node_modules/.gitignore", "private.txt\n")
        self.write("node_modules/private.txt", "needle")
        self.write("root.log", "needle")
        self.write("src/.gitignore", "!/保留.log\n/cache/\n")
        self.write("src/cache/hidden.txt", "needle")
        self.write("src/保留.log", "needle")
        self.write("src/ignored.log", "needle")
        self.write("src/实际.py", "first\nneedle\n")

        grep = await self.executor.grep("needle", max_results=3)
        self.assertEqual(grep.content.splitlines(), [
            "root.log:1: needle", "src/保留.log:1: needle", "src/实际.py:2: needle",
        ])
        self.assertFalse(grep.truncated)
        grep = await self.executor.grep("needle", path="src", max_results=1)
        self.assertEqual(grep.content, "src/保留.log:1: needle")
        self.assertTrue(grep.truncated)

        top = await self.executor.list_files()
        self.assertEqual(top.content.splitlines(), [".gitignore", "root.log", "src/"])
        glob = await self.executor.glob("*.log", path="src")
        self.assertEqual(glob.content, "src/保留.log")
        recursive = await self.executor.list_files(recursive=True)
        self.assertNotIn("src/cache", recursive.content)
        self.assertNotIn("src/ignored.log", recursive.content)
        self.assertNotIn("node_modules", recursive.content)

        explicit = await self.executor.grep("needle", "node_modules")
        self.assertEqual(explicit.content, "node_modules/library.txt:1: needle")
        explicit = await self.executor.grep("needle", "src/ignored.log", "*.py")
        self.assertEqual(explicit.content, "src/ignored.log:1: needle")
        self.assertTrue((await self.executor.read("src/ignored.log")).success)

    async def test_regex_contract_and_explicit_errors(self):
        self.write("source.txt", "Code: code\n")
        self.write("config.json", "Code: code\n")
        result = await self.executor.grep(r"(?<=^)(\w+): \1$")
        self.assertEqual(result.content, "source.txt:1: Code: code")
        sensitive = await self.executor.grep(r"(\w+): \1$", case_sensitive=True)
        self.assertEqual(sensitive.content, "")
        self.assertFalse((await self.executor.grep("Code", "config.json")).success)
        self.assertIn("正则错误", (await self.executor.grep("[")).error)
        for operation in (self.executor.list_files, self.executor.glob, self.executor.grep):
            with self.subTest(tool=operation.__name__):
                args = () if operation == self.executor.list_files else ("Code",)
                self.assertFalse((await operation(*args, path="missing")).success)

    async def test_search_cancellation_signals_and_settles_the_worker(self):
        entered = asyncio.Event()
        released = Event()
        loop = asyncio.get_running_loop()

        def hold(root, workspace, recursive, stopped, readable):
            loop.call_soon_threadsafe(entered.set)
            if stopped.wait(2):
                released.set()
            yield from iter_entries(root, workspace, recursive, stopped, readable)

        with patch("src.executor.iter_entries", side_effect=hold):
            task = asyncio.create_task(self.executor.grep("needle"))
            await asyncio.wait_for(entered.wait(), 2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(released.is_set())


if __name__ == "__main__":
    unittest.main()
