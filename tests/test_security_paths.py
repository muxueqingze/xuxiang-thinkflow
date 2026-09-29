"""File-tool roots apply to real targets, including Windows junctions."""
import asyncio
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.executor import Executor
from src.parser import Command


class LinkedPathTests(unittest.IsolatedAsyncioTestCase):
    async def test_link_cannot_escape_file_tool_root(self):
        with tempfile.TemporaryDirectory(prefix="thinkflow-paths-") as temp:
            root = Path(temp)
            workspace, outside = root / "workspace", root / "outside"
            workspace.mkdir()
            outside.mkdir()
            (outside / "original.txt").write_text("private", encoding="utf-8")
            (workspace / "inside.txt").write_text("inside", encoding="utf-8")
            link = workspace / "link"
            if os.name == "nt":
                result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            else:
                link.symlink_to(outside, target_is_directory=True)
            try:
                executor = Executor(cwd=str(workspace))
                commands = [
                    Command(id="1", tool="write", path="link/new.txt", content="forbidden"),
                    Command(id="2", tool="read", path="link/original.txt"),
                    Command(id="3", tool="copy", path="link/original.txt", dest="copied.txt"),
                    Command(id="4", tool="copy", path="inside.txt", dest="link/copied.txt"),
                ]
                for command in commands:
                    result = await executor.execute(command)
                    self.assertFalse(result.success, command)
                    self.assertIn("允许范围", result.error)
                self.assertFalse((outside / "new.txt").exists())
                self.assertFalse((outside / "copied.txt").exists())
                self.assertFalse((workspace / "copied.txt").exists())
                self.assertEqual((outside / "original.txt").read_text(encoding="utf-8"), "private")
            finally:
                # Remove this test-owned link only; never recurse into its target.
                os.rmdir(link) if os.name == "nt" else link.unlink()


if __name__ == "__main__":
    unittest.main()
