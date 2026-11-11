"""Real command ownership, complete output, and settled file cancellation facts."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.changes import ChangeStore
from src.executor import Executor, ExecutionCancelled, ExecutionResult
from src.interfaces import ExternalInterfaces, InterfaceConfig, CustomToolConfig, ImageGenerationConfig
from src.parser import Command
from src.security import SecurityPolicy


def alive(pid):
    if os.name != "nt":
        state = Path(f"/proc/{pid}/stat")
        return state.exists() and state.read_text().split(") ", 1)[1][0] != "Z"
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel.OpenProcess(0x100000, False, pid)
    if not handle:
        return False
    try:
        return kernel.WaitForSingleObject(handle, 0) == 258
    finally:
        kernel.CloseHandle(handle)


class ProcessContracts(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="thinkflow-process-contracts-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.executor = Executor(str(self.root), security=SecurityPolicy(
            allowed_roots=[str(self.root)], max_bash_output_chars=80000))
        self.addCleanup(self.executor.close)
        self.interfaces = ExternalInterfaces(InterfaceConfig(), str(self.root), executor=self.executor)

    async def wait_for(self, condition):
        deadline = asyncio.get_running_loop().time() + 4
        while not condition():
            self.assertLess(asyncio.get_running_loop().time(), deadline)
            await asyncio.sleep(.005)

    async def run_orphan(self, mode):
        child = "import time,pathlib;time.sleep(1.2);pathlib.Path('late.txt').write_text('late')"
        redirect = ", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL" if mode == "closed" else ""
        source = ("import os,pathlib,subprocess,sys\n"
                  f"child=subprocess.Popen([sys.executable,'-c',{child!r}]{redirect})\n"
                  "pathlib.Path('pids.txt').write_text(str(os.getpid())+' '+str(child.pid))\n")
        task = asyncio.create_task(self.executor._run_process(
            [sys.executable, "-c", source], shell=False, tool="fixture",
            timeout=.4 if mode == "timeout" else 10))
        try:
            await self.wait_for(lambda: (self.root / "pids.txt").exists())
            parent, child_pid = map(int, (self.root / "pids.txt").read_text().split())
            await self.wait_for(lambda: not alive(parent))
            if mode == "cancel":
                task.cancel()
                with self.assertRaises(ExecutionCancelled) as raised:
                    await asyncio.wait_for(task, 4)
                self.assertEqual(raised.exception.result.status_str, "cancelled")
            else:
                result = await asyncio.wait_for(task, 4)
                self.assertEqual(result.timed_out, mode == "timeout")
                self.assertEqual(result.success, mode == "closed")
            await self.wait_for(lambda: not alive(child_pid))
            await asyncio.sleep(1.3)
            self.assertFalse((self.root / "late.txt").exists())
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_exited_parent_timeout_stops_inherited_pipe_child(self):
        await self.run_orphan("timeout")

    async def test_exited_parent_cancel_stops_inherited_pipe_child(self):
        await self.run_orphan("cancel")

    async def test_successful_parent_closes_owned_child_with_redirected_pipes(self):
        await self.run_orphan("closed")

    async def test_all_command_entries_share_complete_streams_and_exit_status(self):
        stdout = "x" * 90000 + "\nSTDOUT_TAIL\n"
        stderr = "y" * 90000 + "\nSTDERR_TAIL\n"
        for exit_code in (0, 7):
            source = ("import sys\n"
                      "sys.stdout.buffer.write(b'x'*90000+b'\\nSTDOUT_TAIL\\n')\n"
                      "sys.stderr.buffer.write(b'y'*90000+b'\\nSTDERR_TAIL\\n')\n"
                      f"sys.exit({exit_code})\n")
            script = self.root / "streams.py"
            script.write_text(source)
            command = [sys.executable, str(script)]
            import shlex
            import subprocess
            shell_command = subprocess.list2cmdline(command) if os.name == "nt" else shlex.join(command)
            custom = CustomToolConfig("custom", "fixture", {}, command=command)
            image = ImageGenerationConfig(enabled=True, provider="command", command=command)
            calls = [lambda: self.executor._run_process(shell_command, shell=True, tool="bash"),
                     lambda: self.executor._run_process(command, shell=False, tool="python"),
                     lambda: self.interfaces.run_custom_tool(custom, {}),
                     lambda: self.interfaces._run_image_command(image, {"output_path": str(self.root / "image.png")})]
            for call in calls:
                result = await call()
                self.assertIsInstance(result, ExecutionResult)
                self.assertEqual(result.exit_code, exit_code)
                self.assertEqual(result.success, exit_code == 0)
                self.assertTrue(result.truncated)
                for stream, original in (("stdout", stdout), ("stderr", stderr)):
                    self.assertEqual(getattr(result, stream), original[-80000:])
                    self.assertEqual(result.output_sizes[stream], len(original))
                    self.assertEqual(self.executor.outputs.path_for(result.output_refs[stream]).read_text(), original)

    async def test_custom_short_output_and_configured_environment_are_preserved(self):
        source = "import json,os,sys; p=json.load(sys.stdin);sys.stdout.buffer.write(('  '+p['text']+'  \\n').encode());sys.stderr.write(os.getcwd())"
        tool = CustomToolConfig("custom", "fixture", {}, command=[sys.executable, "-c", source])
        result = await self.interfaces.run_custom_tool(tool, {"text": "short"})
        self.assertEqual(result.stdout, "  short  \n")
        self.assertEqual(Path(result.stderr), self.root)
        self.assertFalse(result.truncated)

    async def test_custom_timeout_and_cancel_keep_captured_output(self):
        for cancel in (False, True):
            ready = self.root / f"ready-{cancel}"
            source = ("import pathlib,sys,time\n"
                      "sys.stdout.write('x'*90000+'CUSTOM_TAIL');sys.stdout.flush()\n"
                      f"pathlib.Path({str(ready)!r}).write_text('ready')\n"
                      "time.sleep(60)\n")
            tool = CustomToolConfig("custom", "fixture", {}, command=[sys.executable, "-c", source],
                                    timeout_seconds=10 if cancel else .4)
            task = asyncio.create_task(self.interfaces.run_custom_tool(tool, {}))
            if cancel:
                await self.wait_for(ready.exists)
                task.cancel()
                with self.assertRaises(ExecutionCancelled) as raised:
                    await task
                result = raised.exception.result
                self.assertEqual(result.status_str, "cancelled")
            else:
                result = await task
                self.assertTrue(result.timed_out)
            self.assertFalse(result.success)
            self.assertTrue(result.stdout.endswith("CUSTOM_TAIL"))
            self.assertEqual(result.output_sizes["stdout"], 90011)
            self.assertIn("CUSTOM_TAIL", self.executor.outputs.recall(result.output_refs["stdout"], query="CUSTOM_TAIL"))

    async def test_file_cancel_preserves_committed_result_before_and_after_receipt(self):
        store = ChangeStore(self.root / "private", self.root)
        self.executor.set_change_store(store)
        for boundary in ("before", "after"):
            with self.subTest(boundary=boundary):
                started, release = threading.Event(), threading.Event()
                original = self.executor._mutate_file

                def operation(*args):
                    result = original(*args) if boundary == "after" else None
                    started.set()
                    release.wait(4)
                    return result if boundary == "after" else original(*args)

                with patch.object(self.executor, "_mutate_file", operation):
                    task = asyncio.create_task(self.executor.execute(Command(
                        boundary, "append", path=f"{boundary}.txt", content="once")))
                    await self.wait_for(started.is_set)
                    task.cancel()
                    await asyncio.sleep(.01)
                    self.assertFalse(task.done())
                    task.cancel()  # Stop/close callers may cancel the same run again.
                    release.set()
                    with self.assertRaises(ExecutionCancelled) as raised:
                        await task
                result = raised.exception.result
                self.assertTrue(result.success)
                self.assertEqual(result.status_str, "success")
                self.assertEqual(result.bytes_written, 4)
                self.assertEqual((self.root / f"{boundary}.txt").read_text(), "once")
                receipt = next(item for item in store.list_changes() if item["id"] == result.change_id)
                self.assertEqual(receipt["status"], "success")

    async def test_repeated_image_cancel_settles_output_and_returns_receipt(self):
        ready = self.root / "image-ready"
        source = ("import os,pathlib,sys,time\n"
                  "sys.stdout.write('x'*90000+'IMAGE_TAIL');sys.stdout.flush()\n"
                  f"pathlib.Path({str(ready)!r}).write_text(str(os.getpid()))\n"
                  "time.sleep(60)\n")
        image = ImageGenerationConfig(enabled=True, provider="command", command=[sys.executable, "-c", source])
        task = asyncio.create_task(self.interfaces._run_image_command(image, {"output_path": str(self.root / "image.png")}))
        await self.wait_for(ready.exists)
        child_pid = int(ready.read_text())
        task.cancel()
        asyncio.get_running_loop().call_soon(task.cancel)
        with self.assertRaises(ExecutionCancelled) as raised:
            await task
        result = raised.exception.result
        self.assertEqual(result.status_str, "cancelled")
        self.assertEqual(result.output_sizes["stdout"], 90010)
        self.assertEqual(self.executor.outputs.path_for(result.output_refs["stdout"]).read_text(), "x" * 90000 + "IMAGE_TAIL")
        self.assertFalse(alive(child_pid))
        self.assertFalse([pending for pending in asyncio.all_tasks() if pending is not asyncio.current_task()])

    async def test_cancelled_queued_file_thread_still_returns_its_actual_result(self):
        loop = asyncio.get_running_loop()
        pool = ThreadPoolExecutor(max_workers=1)
        loop.set_default_executor(pool)
        started, release = threading.Event(), threading.Event()

        def occupy_worker():
            started.set()
            release.wait(4)

        occupied = loop.run_in_executor(None, occupy_worker)
        await self.wait_for(started.is_set)
        task = asyncio.create_task(self.executor.execute(Command("queued", "append", path="queued.txt", content="once")))
        await asyncio.sleep(0)  # Execute reaches run_in_executor while its sole worker is occupied.
        task.cancel()
        release.set()
        await occupied
        with self.assertRaises(ExecutionCancelled) as raised:
            await task
        self.assertEqual(raised.exception.result.bytes_written, 4)
        self.assertTrue(raised.exception.result.success)
        self.assertEqual((self.root / "queued.txt").read_text(), "once")

    async def test_cancel_at_failed_receipt_commit_keeps_unknown_fact(self):
        store = ChangeStore(self.root / "private", self.root)
        self.executor.set_change_store(store)
        started, release = threading.Event(), threading.Event()

        def fail_receipt(_):
            started.set()
            release.wait(4)
            raise OSError("fixture receipt disk failure")

        with patch.object(store, "complete", fail_receipt):
            task = asyncio.create_task(self.executor.execute(Command("receipt", "append", path="receipt.txt", content="once")))
            await self.wait_for(started.is_set)
            task.cancel()
            release.set()
            with self.assertRaises(ExecutionCancelled) as raised:
                await task
        result = raised.exception.result
        self.assertEqual(result.status_str, "unknown")
        self.assertEqual(result.error, "fixture receipt disk failure")
        self.assertFalse(result.success)
        self.assertEqual((self.root / "receipt.txt").read_text(), "once")
        self.assertEqual(store.list_changes()[0]["status"], "unknown")


if __name__ == "__main__":
    unittest.main(verbosity=2)
