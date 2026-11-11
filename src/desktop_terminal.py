"""Workspace-scoped Windows ConPTY sessions for the desktop transport."""
from __future__ import annotations

import asyncio
import contextlib
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import os
from pathlib import Path
from typing import TYPE_CHECKING
import uuid

from .configuration import ENV_DEFAULTS

if TYPE_CHECKING:
    from winpty import PTY


SCROLLBACK_CHARS = 64 * 1024
MAX_TERMINALS = 8
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
WAIT_OBJECT_0 = 0


class _JobBasicLimits(ctypes.Structure):
    _fields_ = [("per_process_time", ctypes.c_longlong), ("per_job_time", ctypes.c_longlong),
                ("limit_flags", wintypes.DWORD), ("minimum_working_set", ctypes.c_size_t),
                ("maximum_working_set", ctypes.c_size_t), ("active_process_limit", wintypes.DWORD),
                ("affinity", ctypes.c_size_t), ("priority_class", wintypes.DWORD),
                ("scheduling_class", wintypes.DWORD)]


class _JobLimits(ctypes.Structure):
    _fields_ = [("basic", _JobBasicLimits), ("io_counters", ctypes.c_ulonglong * 6),
                ("process_memory_limit", ctypes.c_size_t), ("job_memory_limit", ctypes.c_size_t),
                ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t)]


class _TerminalJob:
    """Close the PowerShell process tree even while ConPTY is still starting."""

    def __init__(self):
        self.kernel = kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
        kernel.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        kernel.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
        kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)

        self.handle = kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = _JobLimits()
        limits.basic.limit_flags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(self.handle, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                                              ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def attach(self, process_handle):
        if not self.kernel.AssignProcessToJobObject(self.handle, process_handle):
            error = ctypes.WinError(ctypes.get_last_error())
            self.kernel.TerminateProcess(process_handle, 1)
            raise error

    def close(self, process_handle=None):
        self.kernel.CloseHandle(self.handle)
        if process_handle is not None and self.kernel.WaitForSingleObject(process_handle, 5000) != WAIT_OBJECT_0:
            raise OSError("终端进程未在关闭时退出。")


@dataclass
class TerminalSession:
    id: str
    title: str
    cols: int
    rows: int
    pty: PTY | None
    job: _TerminalJob
    scrollback: str = ""
    pending: str = ""
    offset: int = 0
    exit_code: int | None = None
    reader: asyncio.Task | None = None

    def snapshot(self):
        return {"id": self.id, "title": self.title, "cols": self.cols, "rows": self.rows,
                "exited": self.pty is None, "exit_code": self.exit_code,
                "scrollback": self.scrollback, "offset": self.offset}


def _size(cols, rows):
    if any(type(value) is not int or not 1 <= value <= 32767 for value in (cols, rows)):
        raise ValueError("终端的行列数须为 1–32767 的整数。")
    return cols, rows


class DesktopTerminals:
    def __init__(self, emit, clean, secrets):
        self.emit = emit
        self.clean = clean
        self.secrets = secrets
        self.sessions: dict[str, TerminalSession] = {}
        self.serial = 0

    def list(self):
        return {"terminals": [terminal.snapshot() for terminal in self.sessions.values()]}

    async def create(self, cwd, cols=80, rows=24):
        if os.name != "nt":
            raise ValueError("当前桌面终端需要 Windows ConPTY。")
        if len(self.sessions) >= MAX_TERMINALS:
            raise ValueError(f"最多可打开 {MAX_TERMINALS} 个终端。")
        cols, rows = _size(cols, rows)
        pty, job = await asyncio.to_thread(self._spawn, cwd, cols, rows)
        self.serial += 1
        terminal = TerminalSession(uuid.uuid4().hex, f"PowerShell {self.serial}", cols, rows, pty, job)
        self.sessions[terminal.id] = terminal
        terminal.reader = asyncio.create_task(self._read(terminal))
        return terminal.snapshot()

    def _spawn(self, cwd, cols, rows):
        from winpty import Backend, PTY

        shell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        excluded = set(ENV_DEFAULTS["api_key"])
        secrets = self.secrets()
        env = "\0".join(f"{key}={value}" for key, value in os.environ.items()
                        if key not in excluded and value not in secrets) + "\0"
        # PtyProcess opens a loopback TCP socket. Use the native handle directly.
        pty = PTY(cols, rows, backend=Backend.ConPTY)
        job = _TerminalJob()
        startup = "[Console]::InputEncoding = [Console]::OutputEncoding = $OutputEncoding = [Text.UTF8Encoding]::new()"
        try:
            if not pty.spawn(str(shell), cmdline=f' -NoLogo -NoProfile -NoExit -Command "{startup}"', cwd=cwd, env=env):
                raise OSError("无法启动 PowerShell 终端。")
            job.attach(pty.fd)
        except Exception:
            job.close()
            raise
        return pty, job

    def _session(self, identifier):
        terminal = self.sessions.get(identifier) if isinstance(identifier, str) else None
        if terminal is None:
            raise ValueError("终端已关闭。")
        return terminal

    async def input(self, identifier, data):
        if not isinstance(data, str):
            raise ValueError("终端输入须为文本。")
        terminal = self._session(identifier)
        if terminal.pty is None:
            raise ValueError("终端进程已退出。")
        await asyncio.to_thread(terminal.pty.write, data)
        return {"ok": True}

    async def resize(self, identifier, cols, rows):
        terminal = self._session(identifier)
        cols, rows = _size(cols, rows)
        if terminal.pty is not None and (terminal.cols, terminal.rows) != (cols, rows):
            await asyncio.to_thread(terminal.pty.set_size, cols, rows)
            terminal.cols, terminal.rows = cols, rows
        return {"ok": True}

    async def close(self, identifier):
        terminal = self._session(identifier)
        self.sessions.pop(identifier)
        await self._close_session(terminal)
        return {"ok": True}

    async def close_all(self):
        terminals = tuple(self.sessions.values())
        self.sessions.clear()
        self.serial = 0
        await asyncio.gather(*(self._close_session(terminal) for terminal in terminals))

    async def _close_session(self, terminal):
        terminal.reader.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await terminal.reader
        await asyncio.to_thread(self._release, terminal)

    @staticmethod
    def _release(terminal):
        pty, terminal.pty = terminal.pty, None
        if pty is not None:
            terminal.job.close(pty.fd)

    def _output(self, terminal, data, *, final=False):
        data = terminal.pending + data
        stop = len(data)
        if data and not final:
            keys = self.secrets()
            # A key may straddle two PTY reads. Keep only a possible partial key at the end.
            last_match_end = max((data.rfind(key) + len(key) for key in keys if key in data), default=0)
            for key in keys:
                for size in range(min(len(key) - 1, len(data) - last_match_end), 0, -1):
                    if key.startswith(data[-size:]):
                        stop = min(stop, len(data) - size)
                        break
        terminal.pending = data[stop:]
        data = self.clean(data[:stop])
        if data:
            terminal.scrollback = (terminal.scrollback + data)[-SCROLLBACK_CHARS:]
            terminal.offset += len(data.encode("utf-16-le", errors="surrogatepass")) // 2
            self.emit({"type": "terminal_output", "id": terminal.id, "data": data, "offset": terminal.offset})

    async def _read(self, terminal):
        from winpty import WinptyError

        try:
            while True:
                chunks = []
                for _ in range(128):
                    data = terminal.pty.read(blocking=False)
                    if not data:
                        break
                    chunks.append(data)
                self._output(terminal, "".join(chunks))
                if not data and terminal.pty.iseof():
                    terminal.exit_code = terminal.pty.get_exitstatus()
                    break
                await asyncio.sleep(0.016)
        except WinptyError as error:
            self._output(terminal, f"\r\n终端读取失败：{error}\r\n")
        self._output(terminal, "", final=True)
        await asyncio.to_thread(self._release, terminal)
        self.emit({"type": "terminal_exit", "id": terminal.id, "exit_code": terminal.exit_code})
