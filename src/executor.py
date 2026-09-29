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
import signal
import threading
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

from .parser import Command
from .security import SecurityPolicy
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

    @property
    def status_str(self) -> str:
        if self.status:
            return self.status
        return "success" if self.success else "failed"


class FileRevisionConflict(ValueError):
    """The requested mutation has no valid model-observed baseline."""


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
        # Explicit legacy opt-out for trusted direct callers only. Agent defaults
        # stay strict; no read/list/grep fallback silently authorizes overwriting.
        self.require_read_revision = require_read_revision
        self.change_store = None
        self.set_change_store(change_store)

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
    async def _file_io(function, *args):
        """A thread cannot be cancelled: settle it before releasing path locks."""
        work = asyncio.get_running_loop().run_in_executor(None, function, *args)
        try:
            return await asyncio.shield(work)
        except asyncio.CancelledError:
            try:
                await work
            finally:
                raise

    async def read(self, path: str) -> ExecutionResult:
        """读取文件内容，供传统 read tool 使用。"""
        try:
            resolved = self._resolve_path(path, "read")
            data, complete = await self._file_io(self._read_preview, resolved, self.max_read_chars)
            raw_content = codecs.getincrementaldecoder("utf-8")().decode(data, final=complete)
            content = raw_content
            if not self.security.allow_sensitive_paths:
                content = self.security.redact_text(content)
            truncated = not complete
            if self.max_read_chars > 0 and len(content) > self.max_read_chars:
                content = content[:self.max_read_chars]
                truncated = True
            revision = ""
            if not truncated and complete and content == raw_content and "\x00" not in content:
                revision = content_revision(data)
                self.grant_read_revision(resolved, revision)
            else:
                # A later partial/redacted view cannot refresh an old full view.
                with self._io_lock:
                    self._read_revisions.pop(self._revision_key(resolved), None)
            return ExecutionResult(
                success=True,
                tool="read",
                path=resolved,
                content=content,
                bytes_written=len(content.encode("utf-8")),
                truncated=truncated,
                revision=revision,
            )
        except Exception as e:
            return ExecutionResult(
                success=False,
                tool="read",
                path=path,
                error=f"读取失败: {e}",
            )

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
        return await self.read(command.path)

    async def list_files(
        self,
        path: str = ".",
        recursive: bool = False,
        max_entries: int = 200,
    ) -> ExecutionResult:
        """List files under a directory."""
        try:
            resolved = self._resolve_path(path or ".", "read")
            if not os.path.isdir(resolved):
                return ExecutionResult(
                    success=False,
                    tool="list_files",
                    path=resolved,
                    error=f"不是目录: {resolved}",
                )

            entries = []
            truncated = False
            if recursive:
                for root, dirs, files in os.walk(resolved):
                    dirs.sort()
                    files.sort()
                    for name in dirs + files:
                        full = os.path.join(root, name)
                        rel = os.path.relpath(full, self.cwd)
                        suffix = "/" if os.path.isdir(full) else ""
                        entries.append(rel.replace("\\", "/") + suffix)
                        if len(entries) >= max_entries:
                            truncated = True
                            break
                    if truncated:
                        break
            else:
                for name in sorted(os.listdir(resolved)):
                    full = os.path.join(resolved, name)
                    rel = os.path.relpath(full, self.cwd)
                    suffix = "/" if os.path.isdir(full) else ""
                    entries.append(rel.replace("\\", "/") + suffix)
                    if len(entries) >= max_entries:
                        truncated = True
                        break

            content = "\n".join(entries)
            return ExecutionResult(
                success=True,
                tool="list_files",
                path=resolved,
                content=content,
                truncated=truncated,
            )
        except Exception as e:
            return ExecutionResult(success=False, tool="list_files", path=path, error=f"列目录失败: {e}")

    async def glob(self, pattern: str, path: str = ".", max_results: int = 200) -> ExecutionResult:
        """Find paths by glob pattern under path."""
        try:
            root = self._resolve_path(path or ".", "read")
            matches = []
            truncated = False
            for current_root, dirs, files in os.walk(root):
                dirs.sort()
                files.sort()
                for name in dirs + files:
                    full = os.path.join(current_root, name)
                    rel_from_root = os.path.relpath(full, root).replace("\\", "/")
                    rel_from_cwd = os.path.relpath(full, self.cwd).replace("\\", "/")
                    if fnmatch.fnmatch(rel_from_root, pattern) or fnmatch.fnmatch(rel_from_cwd, pattern):
                        matches.append(rel_from_cwd + ("/" if os.path.isdir(full) else ""))
                        if len(matches) >= max_results:
                            truncated = True
                            break
                if truncated:
                    break
            return ExecutionResult(
                success=True,
                tool="glob",
                path=root,
                content="\n".join(matches),
                truncated=truncated,
            )
        except Exception as e:
            return ExecutionResult(success=False, tool="glob", path=path, error=f"glob 失败: {e}")

    async def grep(
        self,
        pattern: str,
        path: str = ".",
        file_glob: str = "*",
        case_sensitive: bool = False,
        max_results: int = 100,
    ) -> ExecutionResult:
        """Search text files with a regex pattern."""
        try:
            root = self._resolve_path(path or ".", "read")
            flags = 0 if case_sensitive else re.IGNORECASE
            regex = re.compile(pattern, flags)
            results = []
            truncated = False

            paths = [root] if os.path.isfile(root) else []
            if os.path.isdir(root):
                for current_root, dirs, files in os.walk(root):
                    dirs.sort()
                    files.sort()
                    for name in files:
                        rel_from_root = os.path.relpath(os.path.join(current_root, name), root).replace("\\", "/")
                        if fnmatch.fnmatch(rel_from_root, file_glob):
                            paths.append(os.path.join(current_root, name))

            for file_path in paths:
                try:
                    resolved_file = self._resolve_path(file_path, "read")
                    with open(resolved_file, "r", encoding="utf-8", errors="replace") as f:
                        for line_no, line in enumerate(f, start=1):
                            if regex.search(line):
                                rel = os.path.relpath(resolved_file, self.cwd).replace("\\", "/")
                                safe_line = line.rstrip()
                                if not self.security.allow_sensitive_paths:
                                    safe_line = self.security.redact_text(safe_line)
                                results.append(f"{rel}:{line_no}: {safe_line}")
                                if len(results) >= max_results:
                                    truncated = True
                                    break
                    if truncated:
                        break
                except Exception:
                    continue

            return ExecutionResult(
                success=True,
                tool="grep",
                path=root,
                content="\n".join(results),
                truncated=truncated,
            )
        except re.error as e:
            return ExecutionResult(success=False, tool="grep", path=path, error=f"正则错误: {e}")
        except Exception as e:
            return ExecutionResult(success=False, tool="grep", path=path, error=f"搜索失败: {e}")

    @staticmethod
    def _read_file(path: str) -> str:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    def _check_revision(self, path: str, before: bytes | None) -> None:
        current = content_revision(before)
        known = self._read_revisions.get(self._revision_key(path))
        if not self.require_read_revision:
            return
        if known is not None and known != current:
            raise FileRevisionConflict("revision_conflict: file changed since the model's full read; read again")
        if before is not None and known is None:
            raise FileRevisionConflict("read_required: existing file needs a complete unredacted read before modification")

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
                    self._check_revision(path, before)
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
                        count = content.count(command.old_text)
                        if not command.old_text or count != 1:
                            raise ValueError(f"oldText 必须唯一且非空，当前出现 {count} 次")
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
                    self._read_revisions[self._revision_key(path)] = content_revision(after)
                if receipt:
                    self.change_store.complete(receipt)
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

        # 异步执行子进程
        proc = await asyncio.create_subprocess_shell(
            command.cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.cwd,
            env=self.security.command_env(),
            **({"start_new_session": True} if os.name != "nt" else {}),
        )

        # A non-positive legacy limit must not silently restore unbounded capture.
        max_chars = min(1_000_000, self.security.max_bash_output_chars if self.security.max_bash_output_chars > 0 else 80000)
        readers = [asyncio.create_task(self._read_bounded_output(proc.stdout, max_chars)),
                   asyncio.create_task(self._read_bounded_output(proc.stderr, max_chars))]
        waiter = asyncio.create_task(proc.wait())

        async def exchange():
            stdout, stderr, _ = await asyncio.gather(*readers, waiter)
            return stdout, stderr

        transfer = asyncio.create_task(exchange())
        timed_out = False
        cleanup_incomplete = False
        try:
            outputs = await asyncio.wait_for(asyncio.shield(transfer), timeout=self.security.bash_timeout_seconds)
        except asyncio.TimeoutError:
            # Keep both readers alive while killing: blocked pipes must not prevent
            # the owned process tree from terminating or its readers from settling.
            await self._stop_process_tree(proc)
            outputs = await self._settle_output_capture(proc, transfer, readers, waiter)
            cleanup_incomplete = outputs is None
            timed_out = True
        except asyncio.CancelledError:
            await self._stop_process_tree(proc)
            await self._settle_output_capture(proc, transfer, readers, waiter)
            raise
        except Exception:
            await self._stop_process_tree(proc)
            await self._settle_output_capture(proc, transfer, readers, waiter)
            raise
        if outputs is None:
            outputs = (("", True), ("", True))
        (stdout, stdout_truncated), (stderr, stderr_truncated) = outputs
        truncated = stdout_truncated or stderr_truncated

        return ExecutionResult(
            success=(proc.returncode == 0 and not timed_out),
            tool="bash",
            stdout=stdout,
            stderr=stderr,
            exit_code=proc.returncode,
            error=("命令超时；后台输出管道未关闭，已停止读取。请核对仍在后台的进程。" if cleanup_incomplete
                   else "命令超时" if timed_out else (stderr if proc.returncode != 0 else "")),
            truncated=truncated,
            timed_out=timed_out,
            status="unknown" if cleanup_incomplete else "",
        )

    @staticmethod
    async def _read_bounded_output(pipe, max_chars: int) -> tuple[str, bool]:
        """Drain a pipe fully while retaining a bounded UTF-8 character prefix."""
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        retained = []
        length = 0
        truncated = False
        while chunk := await pipe.read(65536):
            text = decoder.decode(chunk, final=False)
            remaining = max(0, max_chars - length)
            if remaining:
                retained.append(text[:remaining])
                length += min(len(text), remaining)
            truncated = truncated or len(text) > remaining
        tail = decoder.decode(b"", final=True)
        remaining = max(0, max_chars - length)
        if remaining:
            retained.append(tail[:remaining])
        truncated = truncated or len(tail) > remaining
        return "".join(retained), truncated

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
        if proc.returncode is not None:
            return
        if os.name == "nt":
            killer = await asyncio.create_subprocess_exec(
                "taskkill", "/PID", str(proc.pid), "/T", "/F",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            await killer.wait()
        else:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        await proc.wait()

    async def _edit(self, command: Command) -> ExecutionResult:
        return await self._file_command(command)
