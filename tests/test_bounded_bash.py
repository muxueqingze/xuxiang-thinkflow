"""Bounded shell streams and owned-process cleanup with small local fixtures."""
import asyncio
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import signal
import sys
import tempfile
import tracemalloc
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.executor import Executor
from src.parser import Command
from src.security import SecurityPolicy


class BoundedBashTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="thinkflow-bounded-shell-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def command(self, source):
        script = self.root / "fixture.py"
        script.write_text(source, encoding="utf8")
        return Command("fixture", "bash", cmd=subprocess.list2cmdline([sys.executable, "-B", str(script)]))

    def executor(self, **kwargs):
        return Executor(str(self.root), security=SecurityPolicy(allowed_roots=[str(self.root)], **kwargs))

    async def test_two_mib_dual_streams_are_drained_without_communicate_or_full_capture(self):
        command = self.command('import sys\nsys.stdout.buffer.write(b"x" * (2 * 1024 * 1024))\nsys.stderr.buffer.write(b"y" * (2 * 1024 * 1024))\n')
        executor = self.executor(max_bash_output_chars=80000)
        tracemalloc.start()
        try:
            with patch.object(asyncio.subprocess.Process, "communicate", side_effect=AssertionError("unbounded communicate forbidden")):
                result = await asyncio.wait_for(executor.execute(command), 5)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertTrue(result.success, result.error)
        self.assertEqual(result.stdout, "x" * 80000)
        self.assertEqual(result.stderr, "y" * 80000)
        self.assertTrue(result.truncated)
        # Total child output is 4 MiB; retained Python buffers must not grow to it.
        self.assertLess(peak, 2 * 1024 * 1024, f"Python retained peak={peak}")

    async def test_utf8_character_limit_spans_chunks_and_flushes_partial_tail(self):
        class Pipe:
            def __init__(self):
                self.parts = [b"\xe7", b"\x94", b"\xb2", "乙".encode("utf8"), b"\xf0\x9f", b""]
                self.read_sizes = []

            async def read(self, size):
                self.read_sizes.append(size)
                return self.parts.pop(0)

        pipe = Pipe()
        text, truncated = await Executor._read_bounded_output(pipe, 2)
        self.assertEqual(text, "甲乙")
        self.assertTrue(truncated)
        self.assertEqual(set(pipe.read_sizes), {65536})
        pipe = Pipe()
        text, truncated = await Executor._read_bounded_output(pipe, 3)
        self.assertEqual(text, "甲乙\ufffd")
        self.assertFalse(truncated)

    async def test_real_utf8_output_counts_characters_not_encoded_bytes(self):
        command = self.command('import sys\nsys.stdout.buffer.write(("甲乙丙" * 10000).encode("utf8"))\nsys.stderr.buffer.write(b"\\xff")\n')
        result = await self.executor(max_bash_output_chars=5).execute(command)
        self.assertTrue(result.success, result.error)
        self.assertEqual(result.stdout, "甲乙丙甲乙")
        self.assertEqual(result.stderr, "\ufffd")
        self.assertTrue(result.truncated)

    async def test_nonpositive_legacy_limit_uses_bounded_default(self):
        command = self.command('import sys\nsys.stdout.buffer.write(b"z" * 100000)\n')
        result = await self.executor(max_bash_output_chars=0).execute(command)
        self.assertEqual(len(result.stdout), 80000)
        self.assertTrue(result.truncated)

    async def test_large_configured_limit_cannot_disable_hard_character_cap(self):
        command = self.command('import sys\nsys.stdout.buffer.write(b"q" * (2 * 1024 * 1024))\n')
        result = await self.executor(max_bash_output_chars=100_000_000_000).execute(command)
        self.assertTrue(result.success, result.error)
        self.assertEqual(len(result.stdout), 1_000_000)
        self.assertTrue(result.truncated)

    @staticmethod
    def alive(pid):
        if os.name != "nt":
            try:
                os.kill(pid, 0)
                return True
            except ProcessLookupError:
                return False
        kernel = ctypes.windll.kernel32
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x100000, False, pid)
        if not handle:
            return False
        try:
            return kernel.WaitForSingleObject(handle, 0) == 258
        finally:
            kernel.CloseHandle(handle)

    def hanging_command(self):
        return self.command('import os, pathlib, sys, time\npathlib.Path("pid.txt").write_text(str(os.getpid()))\nsys.stdout.buffer.write(b"x" * (2 * 1024 * 1024))\nsys.stdout.flush()\nsys.stderr.buffer.write(b"y" * (2 * 1024 * 1024))\nsys.stderr.flush()\ntime.sleep(60)\n')

    async def test_timeout_kills_owned_child_and_settles_bounded_readers(self):
        result = await asyncio.wait_for(self.executor(bash_timeout_seconds=0.8, max_bash_output_chars=80).execute(self.hanging_command()), 4)
        self.assertFalse(result.success)
        self.assertTrue(result.timed_out)
        self.assertEqual(result.error, "命令超时")
        self.assertLessEqual(len(result.stdout), 80)
        self.assertLessEqual(len(result.stderr), 80)
        pid = int((self.root / "pid.txt").read_text())
        self.assertFalse(self.alive(pid))

    async def test_cancel_kills_owned_child_and_joins_readers(self):
        task = asyncio.create_task(self.executor(max_bash_output_chars=80).execute(self.hanging_command()))
        deadline = asyncio.get_running_loop().time() + 3
        while not (self.root / "pid.txt").exists():
            self.assertLess(asyncio.get_running_loop().time(), deadline)
            await asyncio.sleep(0.01)
        pid = int((self.root / "pid.txt").read_text())
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, 4)
        self.assertFalse(self.alive(pid))

    async def test_exited_shell_with_inherited_child_pipes_cannot_hang_timeout_cleanup(self):
        command = self.command('import pathlib, subprocess, sys\nchild = subprocess.Popen([sys.executable, "-B", "-c", "import time; time.sleep(60)"])\npathlib.Path("child-pid.txt").write_text(str(child.pid))\n')
        started = asyncio.get_running_loop().time()
        try:
            result = await asyncio.wait_for(self.executor(bash_timeout_seconds=0.3).execute(command), 3)
            elapsed = asyncio.get_running_loop().time() - started
            self.assertFalse(result.success)
            self.assertTrue(result.timed_out)
            self.assertIn("管道未关闭", result.error)
            self.assertEqual(result.status_str, "unknown")
            self.assertLess(elapsed, 2.5)
        finally:
            marker = self.root / "child-pid.txt"
            if marker.exists():
                pid = int(marker.read_text())
                if self.alive(pid):
                    os.kill(pid, signal.SIGTERM)
                    deadline = asyncio.get_running_loop().time() + 2
                    while self.alive(pid) and asyncio.get_running_loop().time() < deadline:
                        await asyncio.sleep(0.01)
                    self.assertFalse(self.alive(pid), "owned fixture child did not exit")


if __name__ == "__main__":
    unittest.main(verbosity=2)
