"""Process ownership shared by command execution and desktop terminals."""
from __future__ import annotations

import asyncio
import ctypes
from ctypes import wintypes
import os
import signal


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


class WindowsJob:
    """Own a Windows process tree independently of its parent process lifetime."""

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

    async def terminate(self):
        kernel = self.kernel
        kernel.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
        kernel.QueryInformationJobObject.argtypes = (
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p)
        if not kernel.TerminateJobObject(self.handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())
        accounting = _JobAccounting()
        deadline = asyncio.get_running_loop().time() + 5
        while True:
            if not kernel.QueryInformationJobObject(self.handle, 1, ctypes.byref(accounting),
                                                    ctypes.sizeof(accounting), None):
                raise ctypes.WinError(ctypes.get_last_error())
            if accounting.active_processes == 0:
                return
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError("Owned process job did not terminate")
            await asyncio.sleep(.005)


class _JobAccounting(ctypes.Structure):
    _fields_ = [("times", ctypes.c_longlong * 4), ("page_faults", wintypes.DWORD),
                ("total_processes", wintypes.DWORD), ("active_processes", wintypes.DWORD),
                ("terminated_processes", wintypes.DWORD)]


class _ThreadEntry(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("usage", wintypes.DWORD),
                ("thread_id", wintypes.DWORD), ("process_id", wintypes.DWORD),
                ("base_priority", wintypes.LONG), ("delta_priority", wintypes.LONG),
                ("flags", wintypes.DWORD)]


def _resume_initial_thread(kernel, pid):
    # asyncio/Popen closes CreateProcess's thread handle. The process is still
    # suspended, so its sole initial thread can be recovered without a spawn race.
    kernel.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Thread32First.argtypes = (wintypes.HANDLE, ctypes.POINTER(_ThreadEntry))
    kernel.Thread32Next.argtypes = (wintypes.HANDLE, ctypes.POINTER(_ThreadEntry))
    kernel.OpenThread.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenThread.restype = wintypes.HANDLE
    kernel.ResumeThread.argtypes = (wintypes.HANDLE,)
    kernel.ResumeThread.restype = wintypes.DWORD
    snapshot = kernel.CreateToolhelp32Snapshot(4, 0)  # TH32CS_SNAPTHREAD
    if snapshot == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        entry = _ThreadEntry(size=ctypes.sizeof(_ThreadEntry))
        more = kernel.Thread32First(snapshot, ctypes.byref(entry))
        while more:
            if entry.process_id == pid:
                thread = kernel.OpenThread(2, False, entry.thread_id)  # THREAD_SUSPEND_RESUME
                if not thread:
                    raise ctypes.WinError(ctypes.get_last_error())
                try:
                    if kernel.ResumeThread(thread) == 0xffffffff:
                        raise ctypes.WinError(ctypes.get_last_error())
                    return
                finally:
                    kernel.CloseHandle(thread)
            more = kernel.Thread32Next(snapshot, ctypes.byref(entry))
        raise OSError("Suspended process initial thread not found")
    finally:
        kernel.CloseHandle(snapshot)


async def spawn_owned(command, *, shell, **options):
    """Attach ownership before command code can launch descendants."""
    job = WindowsJob() if os.name == "nt" else None
    if job:
        options["creationflags"] = 4  # CREATE_SUSPENDED
    else:
        options["start_new_session"] = True
    proc = None
    try:
        proc = (await asyncio.create_subprocess_shell(command, **options) if shell
                else await asyncio.create_subprocess_exec(*command, **options))
        proc._thinkflow_job = job
        if job:
            # CPython's Windows subprocess transport owns this live process handle.
            job.attach(proc._transport.get_extra_info("subprocess")._handle)
            _resume_initial_thread(job.kernel, proc.pid)
        return proc
    except BaseException:
        if job:
            job.close()
        if proc is not None:
            if proc.returncode is None:
                proc.kill()
            await proc.wait()
        raise


async def stop_owned(proc):
    """Stop the owned group/job even after its root process has exited."""
    if os.name == "nt":
        job = proc._thinkflow_job
        if job is not None:
            try:
                await job.terminate()
            finally:
                job.close()
                proc._thinkflow_job = None
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    await proc.wait()
