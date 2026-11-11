"""Desktop Git and terminal contracts exercised with real local processes."""
import asyncio
import ctypes
from ctypes import wintypes
import importlib.util
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.desktop_service import DesktopService


class WorkspaceServicesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temp = self.enterContext(tempfile.TemporaryDirectory(prefix="thinkflow-desktop-services-"))
        self.root = Path(temp)
        self.enterContext(patch.dict(os.environ, {"GIT_CEILING_DIRECTORIES": str(self.root)}))
        self.workspace = self.root / "工作区"
        self.workspace.mkdir()
        self.events = []
        self.service = DesktopService(self.events.append)
        self.addAsyncCleanup(self.service.close)
        await self.service.dispatch("initialize", {"data_dir": str(self.root / "data")})
        await self.service.dispatch("open_workspace", {"cwd": str(self.workspace)})

    def git(self, *args):
        subprocess.run(["git", "-c", "user.name=ThinkFlow Test", "-c", "user.email=test@example.invalid",
                        "-c", "core.autocrlf=false", "-c", "commit.gpgsign=false", *args],
                       cwd=self.workspace, capture_output=True, check=True)

    @unittest.skipUnless(shutil.which("git"), "Git executable required")
    async def test_git_status_and_diffs_stay_in_selected_workspace(self):
        status = await self.service.dispatch("git_status")
        self.assertFalse(status["is_repo"])
        self.assertFalse((self.workspace / ".git").exists())

        nested = self.workspace / "子 目录"
        nested.mkdir()
        original = nested / "原 文件.txt"
        original.write_text("original\n", encoding="utf-8")
        changed = nested / "双状态.txt"
        changed.write_text("initial\n", encoding="utf-8")
        outside = self.workspace / "outside.txt"
        outside.write_text("original outside\n", encoding="utf-8")
        self.git("init", "-b", "feature")
        self.git("add", ".")
        self.git("commit", "-m", "baseline")
        self.git("mv", "子 目录/原 文件.txt", "子 目录/-新 文件.txt")
        self.git("mv", "outside.txt", "子 目录/移入.txt")
        changed.write_text("staged\n", encoding="utf-8")
        self.git("add", "子 目录/双状态.txt")
        changed.write_text("working\n", encoding="utf-8")
        (nested / "-未跟踪.txt").write_text("untracked\n", encoding="utf-8")
        (self.workspace / "outside-untracked.txt").write_text("outside\n", encoding="utf-8")
        await self.service.dispatch("open_workspace", {"cwd": str(nested)})

        index = self.workspace / ".git" / "index"
        before = index.read_bytes(), index.stat().st_mtime_ns
        status = await self.service.dispatch("git_status")
        self.assertEqual(status["branch"], "feature")
        self.assertTrue(Path(status["root"]).samefile(self.workspace))
        self.assertCountEqual(status["staged"], [
            {"path": "-新 文件.txt", "old_path": "原 文件.txt", "status": "R"},
            {"path": "移入.txt", "status": "A"},
            {"path": "双状态.txt", "status": "M"},
        ])
        self.assertEqual(status["unstaged"], [{"path": "双状态.txt", "status": "M"}])
        self.assertEqual(status["untracked"], [{"path": "-未跟踪.txt", "status": "?"}])

        async def diff(path, staged=False):
            result = await self.service.dispatch("git_diff", {"path": path, "staged": staged})
            return result["diff"]

        rename = await diff("-新 文件.txt", True)
        self.assertIn("rename from 原 文件.txt", rename)
        self.assertIn("rename to -新 文件.txt", rename)
        self.assertNotIn("rename from outside.txt", await diff("移入.txt", True))
        self.assertIn("+staged", await diff("双状态.txt", True))
        self.assertIn("+working", await diff("双状态.txt"))
        self.assertIn("+untracked", await diff("-未跟踪.txt"))
        self.assertEqual(before, (index.read_bytes(), index.stat().st_mtime_ns))
        for path in ("../outside.txt", str(outside), "../子 目录/双状态.txt"):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "当前工作区"):
                await diff(path, True)

    @unittest.skipUnless(os.name == "nt" and importlib.util.find_spec("winpty"), "Windows ConPTY required")
    async def test_terminal_input_resize_replay_exit_and_workspace_cleanup(self):
        self.enterContext(patch.dict(os.environ, {"OPENAI_API_KEY": "unused-test-env-secret"}))
        await self.service.dispatch("configure", {"api_key": "mock-private-key"})

        async def create():
            return (await self.service.dispatch("terminal_create", {"cols": 120, "rows": 28}))["id"]

        async def terminals():
            result = await self.service.dispatch("terminal_list")
            return {item["id"]: item for item in result["terminals"]}

        def output(identifier):
            return "".join(e["data"] for e in self.events if e["type"] == "terminal_output" and e["id"] == identifier)

        first, second = await create(), await create()
        first_handle, second_handle = self.terminal_handle(first), self.terminal_handle(second)
        for identifier in (first, second):
            await self.service.dispatch("terminal_input", {"id": identifier, "data":
                "[Console]::WriteLine('TF_READY_' + (6 * 7))\r"})
        async with asyncio.timeout(30):
            while not all("TF_READY_42" in output(identifier) for identifier in (first, second)):
                await asyncio.sleep(.02)
        async with asyncio.timeout(12):
            await self.service.dispatch("new_session")
            self.assertEqual(set(await terminals()), {first, second})
            await self.service.dispatch("terminal_resize", {"id": first, "cols": 96, "rows": 19})
            await self.service.dispatch("terminal_input", {"id": first, "data":
                '[Console]::WriteLine("尺寸="+[Console]::WindowWidth+"x"+[Console]::WindowHeight); '
                '[Console]::WriteLine("ENCODING="+[Console]::InputEncoding.CodePage+"/"+'
                '[Console]::OutputEncoding.CodePage+"/"+$OutputEncoding.CodePage); '
                '[Console]::WriteLine("ENV="+[bool]$env:OPENAI_API_KEY); '
                '[Console]::Write("mock-"); Start-Sleep -Milliseconds 450; '
                '[Console]::WriteLine("private-key"); exit 7\r'})
            while not any(e["type"] == "terminal_exit" and e["id"] == first for e in self.events):
                await asyncio.sleep(.02)

        snapshot = (await terminals())[first]
        self.assertIn("\x1b[", snapshot["scrollback"])
        self.assertIn("尺寸=96x19", snapshot["scrollback"])
        self.assertIn("ENCODING=65001/65001/65001", snapshot["scrollback"])
        self.assertIn("ENV=False", snapshot["scrollback"])
        self.assertIn("[REDACTED]", snapshot["scrollback"])
        self.assertNotIn("mock-private-key", snapshot["scrollback"])
        self.assertEqual(snapshot["scrollback"], output(first))
        offset = 0
        for event in self.events:
            if event["type"] == "terminal_output" and event["id"] == first:
                offset += len(event["data"].encode("utf-16-le")) // 2
                self.assertEqual(event["offset"], offset)
        self.assertEqual(snapshot["offset"], offset)
        self.assertTrue(snapshot["exited"])
        self.assertEqual(snapshot["exit_code"], 7)
        self.assert_process_exited(first_handle)
        self.assertFalse((await terminals())[second]["exited"])

        await self.service.dispatch("terminal_input", {"id": second, "data":
            "$child = Start-Process (Join-Path $PSHOME 'powershell.exe') "
            "-ArgumentList '-NoLogo','-NoProfile','-Command','Start-Sleep -Seconds 60' "
            "-WindowStyle Hidden -PassThru; [Console]::WriteLine('CHILD_PID=' + $child.Id)\r"})
        async with asyncio.timeout(10):
            while not (match := re.search(r"CHILD_PID=(\d+)", output(second))):
                await asyncio.sleep(.02)
        child_handle = self.process_handle(int(match[1]))
        await self.service.dispatch("terminal_close", {"id": second})
        self.assert_process_exited(second_handle)
        self.assert_process_exited(child_handle)
        self.assertEqual(set(await terminals()), {first})

        third = await create()
        third_handle = self.terminal_handle(third)
        new_workspace = self.root / "另一工作区"
        new_workspace.mkdir()
        await self.service.dispatch("open_workspace", {"cwd": str(new_workspace)})
        self.assertFalse(await terminals())
        self.assert_process_exited(third_handle)

        fourth = await create()
        fourth_handle = self.terminal_handle(fourth)
        await self.service.dispatch("shutdown")
        self.assertFalse(await terminals())
        self.assert_process_exited(fourth_handle)

    def terminal_handle(self, identifier):
        return self.process_handle(self.service.terminals.sessions[identifier].pty.pid)

    def process_handle(self, pid):
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        self.assertTrue(handle)
        self.addCleanup(kernel.CloseHandle, handle)
        return handle

    def assert_process_exited(self, handle):
        wait = ctypes.WinDLL("kernel32", use_last_error=True).WaitForSingleObject
        wait.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        self.assertEqual(wait(handle, 3000), 0)
