"""
ThinkFlow Executor — 命令执行器

接收 Command 对象，执行实际操作（write/mkdir/bash/edit）。
返回 ExecutionResult。
"""

import asyncio
import codecs
import fnmatch
import os
import re
import stat
import sys
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Iterator, Optional

from .parser import Command
from .security import SecurityPolicy
from .output_capture import OutputCapture
from .output_store import OutputStore
from .process_owner import spawn_owned, stop_owned
from .file_observations import ReadObservation, read_file_range
from .workspace_search import iter_entries
from .changes import (
    ChangeStore, MAX_GUARDED_FILE_BYTES, atomic_write_bytes, content_revision,
    read_optional_bytes, reject_symbolic_path,
)


@dataclass
class ExecutionResult:
    """命令执行结果"""
    success: bool
    tool: str = ""
    path: str = ""
    bytes_written: int = 0
    stdout: str = ""
    stderr: str = ""
    exit_code: Optional[int] = None
    error: str = ""
    content: str = ""
    truncated: bool = False
    timed_out: bool = False
    status: str = ""
    revision: str = ""
    change_id: str = ""
    output_refs: dict[str, str] = field(default_factory=dict)
    output_sizes: dict[str, int] = field(default_factory=dict)
    read_note: str = ""

    @property
    def status_str(self) -> str:
        if self.status:
            return self.status
        return "success" if self.success else "failed"


class FileRevisionConflict(ValueError):
    """The requested mutation has no valid model-observed baseline."""


class ExecutionCancelled(asyncio.CancelledError):
    def __init__(self, result: ExecutionResult):
        super().__init__(result.error)
        self.result = result


class Executor:
    """命令执行器"""

    def __init__(
        self,
        cwd: str = ".",
        allowed_paths: Optional[list[str]] = None,
        max_read_chars: int = 200_000,
        security: Optional[SecurityPolicy] = None,
        change_store: Optional[ChangeStore] = None,
        require_read_revision: bool = True,
        output_store: Optional[OutputStore] = None,
    ):
        """
        Args:
            cwd: 工作目录
            allowed_paths: 兼容旧参数；传入时覆盖 security.allowed_roots
            max_read_chars: read 工具单次返回的最大字符数，避免意外把巨型文件塞回上下文。
            security: 安全策略，默认限制在 cwd 内并拦截常见密钥读取。
        """
        self.cwd = os.path.abspath(os.path.expanduser(cwd))
        self.security = security or SecurityPolicy(allowed_roots=[self.cwd])
        if allowed_paths is not None:
            self.security.allowed_roots = allowed_paths
        self.allowed_paths = self.security.normalized_roots(self.cwd)
        self.max_read_chars = max_read_chars
        self._path_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._io_lock = threading.RLock()
        self._read_revisions: dict[str, str] = {}
        self._read_observations: dict[str, ReadObservation] = {}
        # Explicit legacy opt-out for trusted direct callers only. Agent defaults
        # stay strict; no read/list/grep fallback silently authorizes overwriting.
        self.require_read_revision = require_read_revision
        self.outputs = output_store if output_store is not None else OutputStore()
        self.output_secrets: set[str] = set()
        self.change_store = None
        self.set_change_store(change_store)

    def redact_output(self, text: str) -> str:
        for key in sorted(self.output_secrets, key=len, reverse=True):
            text = text.replace(key, "[REDACTED]")
        return text

    def close(self):
        self.outputs.close()

    def set_change_store(self, store: Optional[ChangeStore]) -> None:
        if store is not None and os.path.normcase(str(store.cwd)) != os.path.normcase(self.cwd):
            raise ValueError("change_store_workspace_mismatch")
        self.change_store = store

    def grant_read_revision(self, path: str, revision: str) -> None:
        """Grant exactly the UTF-8 attachment version delivered to the model.

        Caller must only use this after admitting that immutable full content.
        We never read the current file and silently replace the supplied hash.
        """
        resolved = self._resolve_path(path, "read")
        reject_symbolic_path(resolved)
        if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{64}", revision):
            raise ValueError("invalid_read_revision: expected SHA256")
        with self._io_lock:
            self._read_revisions[self._revision_key(resolved)] = revision

    @staticmethod
    def _revision_key(path: str) -> str:
        return os.path.normcase(os.path.abspath(path))

    async def execute(self, command: Command) -> ExecutionResult:
        """执行命令，返回结果。"""
        dispatch = {
            "read": self._read_command,
            "write": self._write,
            "append": self._append,
            "mkdir": self._mkdir,
            "touch": self._touch,
            "copy": self._copy,
            "bash": self._bash,
            "python": self._python_command,
            "edit": self._edit,
        }

        handler = dispatch.get(command.tool)
        if not handler:
            return ExecutionResult(
                success=False,
                tool=command.tool,
                error=f"未知工具类型: {command.tool}",
            )

        try:
            return await handler(command)
        except Exception as e:
            return ExecutionResult(
                success=False,
                tool=command.tool,
                path=command.path or "",
                error=f"执行异常: {e}",
            )

    def _normalize_path(self, path: str) -> str:
        """展开用户路径，并把相对路径固定到 agent cwd 下。"""
        expanded = os.path.expanduser(path)
        if not os.path.isabs(expanded):
            expanded = os.path.join(self.cwd, expanded)
        return os.path.abspath(expanded)

    def _resolve_path(self, path: str, operation: str) -> str:
        resolved = self._normalize_path(path)
        self.security.check_path(resolved, self.allowed_paths, operation)
        if self.change_store is not None:
            canonical = os.path.normcase(os.path.realpath(resolved))
            private_root = os.path.normcase(os.path.realpath(self.change_store.root))
            try:
                private = os.path.commonpath([canonical, private_root]) == private_root
            except ValueError:
                private = False
            if private:
                raise PermissionError("change_store_path: private receipts are not model inputs or tool targets")
        return resolved

    @staticmethod
    async def _file_io(function, *args, stop: threading.Event | None = None):
        """Settle file I/O before unwinding, signalling cooperative searches first."""
        work = asyncio.get_running_loop().run_in_executor(None, function, *args)
        try:
            return await asyncio.shield(work)
        except asyncio.CancelledError:
            if stop is not None:
                stop.set()
            while not work.done():
                try:
                    await asyncio.shield(work)
                except asyncio.CancelledError:
                    continue
            result = work.result()
            if stop is None and isinstance(result, ExecutionResult):
                raise ExecutionCancelled(result) from None
            raise

    async def read(self, path: str, offset: int | None = None, limit: int | None = None) -> ExecutionResult:
        """读取文件内容，供传统 read tool 使用。"""
        try:
            for name, value in (("offset", offset), ("limit", limit)):
                if value is not None and (type(value) is not int or value < 1):
                    raise ValueError(f"{name} must be a positive integer (lines, starting at 1)")
            resolved = self._resolve_path(path, "read")
            ranged = offset is not None or limit is not None
            if ranged:
                reject_symbolic_path(resolved)
                if not os.path.isfile(resolved):
                    raise FileNotFoundError("file not found or not a regular file")
                view = await self._file_io(read_file_range, resolved, offset or 1, limit, self.max_read_chars)
                raw_content = view.content
                observed_revision = view.revision
                start = view.start
                full = view.start == 0 and view.end == view.total_chars
                truncated = view.truncated
                read_note = view.read_note
            else:
                data, complete = await self._file_io(self._read_preview, resolved, self.max_read_chars)
                raw_content = codecs.getincrementaldecoder("utf-8")().decode(data, final=complete)
                observed_revision = content_revision(data) if complete else ""
                start = 0
                full = complete
                truncated = not complete
                read_note = ""
            content = raw_content
            if not self.security.allow_sensitive_paths:
                content = self.security.redact_text(content)
            content = self.redact_output(content)
            unredacted = content == raw_content and "\x00" not in content
            if self.max_read_chars > 0 and len(content) > self.max_read_chars:
                content = content[:self.max_read_chars]
                truncated = True
                full = False
            revision = ""
            if observed_revision and unredacted:
                self._observe_read(resolved, observed_revision, start, start + len(content), full)
                if full:
                    revision = observed_revision
            else:
                # A redacted or unhashed view cannot refresh an older baseline.
                with self._io_lock:
                    key = self._revision_key(resolved)
                    self._read_revisions.pop(key, None)
                    self._read_observations.pop(key, None)
            return ExecutionResult(
                success=True,
                tool="read",
                path=resolved,
                content=content,
                bytes_written=len(content.encode("utf-8")),
                truncated=truncated,
                revision=revision,
                read_note=read_note,
            )
        except Exception as e:
            return ExecutionResult(
                success=False,
                tool="read",
                path=path,
                error=f"读取失败: {e}",
            )

    def _observe_read(self, path: str, revision: str, start: int, end: int, full: bool) -> None:
        with self._io_lock:
            key = self._revision_key(path)
            if self._read_revisions.get(key) != revision:
                self._read_revisions.pop(key, None)
            if full:
                self._read_revisions[key] = revision
                self._read_observations.pop(key, None)
            elif key not in self._read_revisions:
                observed = self._read_observations.get(key)
                if observed is None or observed.revision != revision:
                    observed = self._read_observations[key] = ReadObservation(revision)
                observed.include(start, end)

    @staticmethod
    def _read_preview(path: str, max_chars: int) -> tuple[bytes, bool]:
        reject_symbolic_path(path)
        if not os.path.isfile(path):
            raise FileNotFoundError("file not found or not a regular file")
        # UTF-8 needs at most four bytes per character. Read one byte beyond the
        # bounded window to distinguish EOF without hashing or loading the tail.
        limit = min(MAX_GUARDED_FILE_BYTES, max_chars * 4 + 4) if max_chars > 0 else MAX_GUARDED_FILE_BYTES
        with open(path, "rb") as stream:
            data = stream.read(limit + 1)
        return data[:limit], len(data) <= limit

    async def _read_command(self, command: Command) -> ExecutionResult:
        if command.path is None:
            return ExecutionResult(success=False, tool="read", error="缺少 path")
        return await self.read(command.path, command.offset, command.limit)

    async def _search(
        self,
        tool: str,
        path: str,
        limit: int,
        collect: Callable[[str, threading.Event], Iterator[str]],
    ) -> ExecutionResult:
        stopped = threading.Event()

        def run():
            try:
                if limit < 0:
                    raise ValueError("结果数量上限不能为负数")
                root = self._resolve_path(path or ".", "read")
                lines = []
                for line in collect(root, stopped):
                    if len(lines) == limit:
                        return ExecutionResult(True, tool, root, content="\n".join(lines), truncated=True)
                    lines.append(line)
                return ExecutionResult(True, tool, root, content="\n".join(lines))
            except re.error as e:
                return ExecutionResult(False, tool, path, error=f"正则错误: {e}")
            except (OSError, ValueError) as e:
                return ExecutionResult(False, tool, path, error=f"搜索失败: {e}")

        return await self._file_io(run, stop=stopped)

    def _search_readable(self, path: str) -> bool:
        try:
            self._resolve_path(path, "read")
        except PermissionError:
            return False
        return True

    def _search_entries(self, root: str, recursive: bool, stopped: threading.Event):
        return iter_entries(root, self.cwd, recursive, stopped, self._search_readable)

    def _search_prefix(self, root: str) -> str:
        relative = os.path.relpath(root, self.cwd).replace(os.sep, "/")
        return "" if relative == "." else relative + "/"

    async def list_files(
        self,
        path: str = ".",
        recursive: bool = False,
        max_entries: int = 200,
    ) -> ExecutionResult:
        """List files under a directory."""

        def collect(root, stopped):
            prefix = self._search_prefix(root)
            for entry in self._search_entries(root, recursive, stopped):
                yield prefix + entry.relative + ("/" if entry.is_dir else "")

        return await self._search("list_files", path, max_entries, collect)

    async def glob(self, pattern: str, path: str = ".", max_results: int = 200) -> ExecutionResult:
        """Find paths by glob pattern under path."""

        def collect(root, stopped):
            prefix = self._search_prefix(root)
            for entry in self._search_entries(root, True, stopped):
                path = prefix + entry.relative
                if fnmatch.fnmatch(entry.relative, pattern) or fnmatch.fnmatch(path, pattern):
                    yield path + ("/" if entry.is_dir else "")

        return await self._search("glob", path, max_results, collect)

    async def grep(
        self,
        pattern: str,
        path: str = ".",
        file_glob: str = "*",
        case_sensitive: bool = False,
        max_results: int = 100,
    ) -> ExecutionResult:
        """Search text files with a Python regex pattern."""

        def collect(root, stopped):
            flags = 0 if case_sensitive else re.IGNORECASE
            regex = re.compile(pattern, flags)
            mode = os.stat(root).st_mode
            if stat.S_ISREG(mode):
                relative = os.path.relpath(root, self.cwd).replace(os.sep, "/")
                paths = ((root, relative),)
            elif stat.S_ISDIR(mode):
                prefix = self._search_prefix(root)
                paths = (
                    (entry.path, prefix + entry.relative)
                    for entry in self._search_entries(root, True, stopped)
                    if not entry.is_dir and fnmatch.fnmatch(entry.relative, file_glob)
                    and self._search_readable(entry.path)
                )
            else:
                raise ValueError(f"不是普通文件或目录: {root}")

            for file_path, relative in paths:
                with open(file_path, encoding="utf-8", errors="replace") as source:
                    for line_no, line in enumerate(source, start=1):
                        if stopped.is_set():
                            return
                        if regex.search(line):
                            safe_line = line.rstrip()
                            if not self.security.allow_sensitive_paths:
                                safe_line = self.security.redact_text(safe_line)
                            yield f"{relative}:{line_no}: {safe_line}"

        return await self._search("grep", path, max_results, collect)

    @staticmethod
    def _read_file(path: str) -> str:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    def _check_revision(self, path: str, before: bytes | None, *, exact_edit: bool = False) -> ReadObservation | None:
        current = content_revision(before)
        key = self._revision_key(path)
        known = self._read_revisions.get(key)
        if not self.require_read_revision:
            return
        if known is not None and known != current:
            raise FileRevisionConflict("revision_conflict: file changed since the model's full read; read again")
        if known is not None:
            return
        observed = self._read_observations.get(key)
        if observed is not None:
            if observed.revision != current:
                raise FileRevisionConflict("revision_conflict: file changed since the model's range read; read again")
            if exact_edit:
                return observed
        if before is not None and known is None:
            requirement = "the target range" if exact_edit else "the complete file"
            raise FileRevisionConflict(f"read_required: read {requirement} without redaction before modification")

    async def _write(self, command: Command) -> ExecutionResult:
        return await self._file_command(command)

    async def _append(self, command: Command) -> ExecutionResult:
        return await self._file_command(command)

    async def _touch(self, command: Command) -> ExecutionResult:
        return await self._file_command(command)

    async def _copy(self, command: Command) -> ExecutionResult:
        return await self._file_command(command)

    async def _mkdir(self, command: Command) -> ExecutionResult:
        return await self._file_command(command)

    async def _file_command(self, command: Command) -> ExecutionResult:
        if command.path is None:
            return ExecutionResult(False, tool=command.tool, error="缺少 path")
        if command.tool in {"write", "append"}:
            if command.content is None or not command.content.strip():
                return ExecutionResult(False, tool=command.tool, path=command.path,
                                       error="内容为空；创建空文件请使用 touch")
        if command.tool == "copy" and command.dest is None:
            return ExecutionResult(False, tool="copy", error="缺少 dest")
        if command.tool == "edit" and (command.old_text is None or command.new_text is None):
            return ExecutionResult(False, tool="edit", error="缺少 old_text / new_text")
        target = command.dest if command.tool == "copy" else command.path
        path = self._resolve_path(target, "write")
        reject_symbolic_path(path)
        async with self._path_locks[self._revision_key(path)]:
            return await self._file_io(self._mutate_file, command, path)

    def _mutate_file(self, command: Command, path: str) -> ExecutionResult:
        # Store's lock also excludes a simultaneous UI revert. These locks do
        # not claim to coordinate independent processes or external editors.
        with self._io_lock, (self.change_store.lock if self.change_store else self._io_lock):
            receipt = ""
            try:
                path = self._resolve_path(path, "write")
                reject_symbolic_path(path)
                if command.tool == "mkdir":
                    if os.path.isdir(path):
                        return ExecutionResult(True, tool="mkdir", path=path)
                    if os.path.lexists(path):
                        raise ValueError("not_directory: target already exists")
                    before = after = None
                else:
                    before = read_optional_bytes(path)
                    observed = self._check_revision(path, before, exact_edit=command.tool == "edit")
                    if command.tool == "write":
                        after = command.content.encode("utf-8")
                    elif command.tool == "append":
                        after = (before or b"") + command.content.encode("utf-8")
                    elif command.tool == "touch":
                        after = before if before is not None else b""
                    elif command.tool == "copy":
                        source = self._resolve_path(command.path, "read")
                        after = read_optional_bytes(source)
                        if after is None:
                            raise FileNotFoundError("源文件不存在")
                    elif command.tool == "edit":
                        if before is None:
                            raise FileNotFoundError("文件不存在")
                        content = before.decode("utf-8")
                        edit_start = content.find(command.old_text)
                        if (not command.old_text or edit_start < 0
                                or content.find(command.old_text, edit_start + 1) >= 0):
                            raise ValueError("oldText 必须唯一且非空（包括重叠匹配）")
                        edit_end = edit_start + len(command.old_text)
                        if observed is not None and not observed.covers(edit_start, edit_end):
                            raise FileRevisionConflict("read_required: old_text extends beyond the observed range; read that range first")
                        after = content.replace(command.old_text, command.new_text, 1).encode("utf-8")
                    else:
                        raise ValueError("unsupported file mutation")
                    if len(after) > MAX_GUARDED_FILE_BYTES:
                        raise ValueError("file_too_large: guarded file operations are limited to 8 MiB")
                if self.change_store:
                    receipt = self.change_store.begin(
                        path, command.tool, before, after, command_id=command.id,
                        kind="directory" if command.tool == "mkdir" else "file",
                    )
                # No workspace mkdir or write is allowed before intent commits.
                self._resolve_path(path, "write")
                reject_symbolic_path(path)
                if command.tool == "mkdir":
                    os.makedirs(path, exist_ok=True)
                else:
                    if content_revision(read_optional_bytes(path)) != content_revision(before):
                        raise FileRevisionConflict("revision_conflict: file changed before write")
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    reject_symbolic_path(path)
                    if content_revision(read_optional_bytes(path)) != content_revision(before):
                        raise FileRevisionConflict("revision_conflict: file changed before replacement")
                    if command.tool == "touch" and before is not None:
                        os.utime(path, None)
                    else:
                        atomic_write_bytes(path, after)
                    if content_revision(read_optional_bytes(path)) != content_revision(after):
                        raise FileRevisionConflict("revision_conflict: file changed during write")
                if receipt:
                    self.change_store.complete(receipt)
                if command.tool != "mkdir":
                    revision = content_revision(after)
                    if observed is not None:
                        observed.after_edit(edit_start, edit_end, len(command.new_text), revision)
                    else:
                        key = self._revision_key(path)
                        self._read_revisions[key] = revision
                        self._read_observations.pop(key, None)
                size = len(command.content.encode("utf-8")) if command.tool == "append" else len(after or b"")
                return ExecutionResult(True, tool=command.tool, path=path, bytes_written=size,
                                       revision=content_revision(after) if command.tool != "mkdir" else "",
                                       change_id=receipt)
            except Exception as error:
                # A durable intent with no success receipt is deliberately left
                # unknown, including final receipt storage failures after a write.
                status = "unknown" if receipt else ("conflict" if isinstance(error, FileRevisionConflict) else "failed")
                return ExecutionResult(False, tool=command.tool, path=path,
                                       error=str(error), status=status, change_id=receipt)

    @staticmethod
    def _write_file(path: str, data: bytes):
        atomic_write_bytes(path, data)

    async def _bash(self, command: Command) -> ExecutionResult:
        """执行 shell 命令。"""
        if command.cmd is None:
            return ExecutionResult(
                success=False, tool="bash", error="缺少 cmd",
            )
        try:
            self.security.check_bash(command.cmd)
        except PermissionError as e:
            return ExecutionResult(
                success=False,
                tool="bash",
                error=str(e),
                exit_code=126,
            )

        return await self._run_process(command.cmd, shell=True, tool="bash")

    async def _python_command(self, command: Command) -> ExecutionResult:
        return await self.python(command.cmd)

    async def python(self, code: str) -> ExecutionResult:
        """Run a Python code block directly, without shell quoting or a script file."""
        try:
            self.security.check_bash(code)
        except PermissionError as error:
            return ExecutionResult(success=False, tool="python", error=str(error), exit_code=126)
        executable = os.environ.get("THINKFLOW_PYTHON")
        if executable:
            command = [executable, "-X", "utf8", "-"]
        elif getattr(sys, "frozen", False):
            runtime = os.path.join(os.path.dirname(sys.executable), "python")
            command = [os.path.join(runtime, "python.exe"), "-X", "utf8",
                       os.path.join(runtime, "thinkflow_stdin.py")]
        else:
            command = ["python", "-X", "utf8", "-"]
        return await self._run_process(command, shell=False, tool="python", stdin=code)

    async def run_command(self, command: list[str], *, tool: str, stdin: str,
                          timeout: float, env: dict[str, str]) -> ExecutionResult:
        """Execute a configured command with the same capture and ownership as bash."""
        return await self._run_process(command, shell=False, tool=tool, stdin=stdin,
                                       timeout=timeout, env=env)

    async def _run_process(self, command, *, shell: bool, tool: str, stdin: Optional[str] = None,
                           timeout: Optional[float] = None, env: Optional[dict[str, str]] = None) -> ExecutionResult:
        options = {"stdout": asyncio.subprocess.PIPE, "stderr": asyncio.subprocess.PIPE,
                   "stdin": asyncio.subprocess.PIPE if stdin is not None else None,
                   "cwd": self.cwd, "env": self.security.command_env() if env is None else env}
        if tool == "python":
            # Match the UTF-8 code channel and result decoder, regardless of shell locale.
            options["env"].update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        proc = await spawn_owned(command, shell=shell, **options)

        # A non-positive legacy limit must not silently restore unbounded capture.
        max_chars = min(1_000_000, self.security.max_bash_output_chars if self.security.max_bash_output_chars > 0 else 80000)
        captures = {name: OutputCapture(self.outputs, max_chars, self.output_secrets)
                    for name in ("stdout", "stderr")}
        readers = [asyncio.create_task(captures[name].read(getattr(proc, name)))
                   for name in ("stdout", "stderr")]
        waiter = asyncio.create_task(proc.wait())

        async def exchange():
            if stdin is not None:
                try:
                    proc.stdin.write(stdin.encode("utf-8"))
                    await proc.stdin.drain()
                except (BrokenPipeError, ConnectionResetError):
                    pass  # Commands may finish without reading all their input.
                finally:
                    proc.stdin.close()
            stdout, stderr, _ = await asyncio.gather(*readers, waiter)
            return stdout, stderr

        transfer = asyncio.create_task(exchange())
        timed_out = False
        cleanup_incomplete = False
        try:
            outputs = await asyncio.wait_for(asyncio.shield(transfer), timeout=(
                self.security.bash_timeout_seconds if timeout is None else timeout))
        except asyncio.TimeoutError:
            # Keep both readers alive while killing: blocked pipes must not prevent
            # the owned process tree from terminating or its readers from settling.
            await self._stop_process_tree(proc)
            outputs = await self._settle_output_capture(proc, transfer, readers, waiter)
            cleanup_incomplete = outputs is None
            timed_out = True
        except asyncio.CancelledError:
            async def finish_cancelled_process():
                await self._stop_process_tree(proc)
                await self._settle_output_capture(proc, transfer, readers, waiter)

            cleanup = asyncio.create_task(finish_cancelled_process())
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    continue
            cleanup.result()
            raise ExecutionCancelled(self._captured_result(
                captures, tool=tool, exit_code=proc.returncode, status="cancelled",
                error="Execution cancelled; captured output is available for inspection.",
            )) from None
        except Exception:
            await self._stop_process_tree(proc)
            await self._settle_output_capture(proc, transfer, readers, waiter)
            raise
        else:
            # Background descendants with closed output pipes are still owned.
            await self._stop_process_tree(proc)
        return self._captured_result(
            captures, tool=tool, exit_code=proc.returncode,
            error=("命令超时；后台输出管道未关闭，已停止读取。请核对仍在后台的进程。" if cleanup_incomplete
                   else "命令超时" if timed_out else (captures["stderr"].text if proc.returncode != 0 else "")),
            timed_out=timed_out,
            status="unknown" if cleanup_incomplete else "",
        )

    @staticmethod
    def _captured_result(captures, *, tool, exit_code, error="", timed_out=False, status=""):
        return ExecutionResult(
            success=exit_code == 0 and not timed_out and not status,
            tool=tool, stdout=captures["stdout"].text, stderr=captures["stderr"].text,
            exit_code=exit_code, error=error, timed_out=timed_out, status=status,
            truncated=any(capture.truncated or not capture.complete for capture in captures.values()),
            output_refs={name: capture.ref for name, capture in captures.items() if capture.ref},
            output_sizes={name: capture.chars for name, capture in captures.items() if capture.ref},
        )

    @staticmethod
    async def _settle_output_capture(proc, transfer, readers, waiter, timeout=1.0):
        """Bound cleanup even when an exited shell left inherited pipes open."""
        try:
            return await asyncio.wait_for(asyncio.shield(transfer), timeout=timeout)
        except asyncio.TimeoutError:
            # StreamReader has no public close API. These are this process's
            # local pipe transports; closing them does not kill unrelated PIDs.
            transport = getattr(proc, "_transport", None)
            if transport:
                for descriptor in (1, 2):
                    try:
                        pipe = transport.get_pipe_transport(descriptor)
                        if pipe:
                            pipe.close()
                    except (AttributeError, OSError):
                        pass
        except Exception:
            pass
        for task in (transfer, *readers, waiter):
            if not task.done():
                task.cancel()
        await asyncio.gather(transfer, *readers, waiter, return_exceptions=True)
        return None

    @staticmethod
    async def _stop_process_tree(proc):
        """Only terminate the process tree created by this executor."""
        await stop_owned(proc)

    async def _edit(self, command: Command) -> ExecutionResult:
        return await self._file_command(command)
