"""
ThinkFlow Executor — 命令执行器

接收 Command 对象，执行实际操作（write/mkdir/bash/edit）。
返回 ExecutionResult。
"""

import asyncio
import fnmatch
import os
import re
import shutil
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

from .parser import Command
from .security import SecurityPolicy


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

    @property
    def status_str(self) -> str:
        if self.status:
            return self.status
        return "success" if self.success else "failed"


class Executor:
    """命令执行器"""

    def __init__(
        self,
        cwd: str = ".",
        allowed_paths: Optional[list[str]] = None,
        max_read_chars: int = 200_000,
        security: Optional[SecurityPolicy] = None,
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
        return resolved

    async def read(self, path: str) -> ExecutionResult:
        """读取文件内容，供传统 read tool 使用。"""
        try:
            resolved = self._resolve_path(path, "read")
            loop = asyncio.get_event_loop()
            content = await loop.run_in_executor(None, self._read_file, resolved)
            if not self.security.allow_sensitive_paths:
                content = self.security.redact_text(content)
            truncated = False
            if self.max_read_chars > 0 and len(content) > self.max_read_chars:
                content = content[:self.max_read_chars]
                truncated = True
            return ExecutionResult(
                success=True,
                tool="read",
                path=resolved,
                content=content,
                bytes_written=len(content.encode("utf-8")),
                truncated=truncated,
            )
        except Exception as e:
            return ExecutionResult(
                success=False,
                tool="read",
                path=path,
                error=f"读取失败: {e}",
            )

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

    async def _write(self, command: Command) -> ExecutionResult:
        """写入文件（覆盖）。"""
        if command.path is None or command.content is None:
            return ExecutionResult(
                success=False, tool="write", path=command.path or "",
                error="缺少 path 或 content",
            )

        if not command.content.strip():
            return ExecutionResult(
                success=False,
                tool="write",
                path=command.path,
                error="write 内容为空；如需创建空文件请使用 touch",
            )

        path = self._resolve_path(command.path, "write")

        # 确保目录存在
        dir_path = os.path.dirname(path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)

        # 写入（异步包装同步 IO）
        data = command.content.encode("utf-8")
        loop = asyncio.get_event_loop()
        async with self._path_locks[path]:
            await loop.run_in_executor(None, self._write_file, path, data)

        return ExecutionResult(
            success=True,
            tool="write",
            path=path,
            bytes_written=len(data),
        )

    @staticmethod
    def _write_file(path: str, data: bytes):
        temp_path = f"{path}.tmp-{os.getpid()}"
        with open(temp_path, "wb") as f:
            f.write(data)
        os.replace(temp_path, path)

    async def _mkdir(self, command: Command) -> ExecutionResult:
        """创建目录。"""
        if command.path is None:
            return ExecutionResult(
                success=False, tool="mkdir", error="缺少 path",
            )

        path = self._resolve_path(command.path, "mkdir")
        os.makedirs(path, exist_ok=True)

        return ExecutionResult(
            success=True, tool="mkdir", path=path,
        )

    async def _append(self, command: Command) -> ExecutionResult:
        """追加写入文件。"""
        if command.path is None or command.content is None:
            return ExecutionResult(
                success=False, tool="append", path=command.path or "",
                error="缺少 path 或 content",
            )

        if not command.content.strip():
            return ExecutionResult(
                success=False,
                tool="append",
                path=command.path,
                error="append 内容为空；空追加没有可审计副作用",
            )

        path = self._resolve_path(command.path, "write")
        dir_path = os.path.dirname(path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)

        data = command.content.encode("utf-8")
        loop = asyncio.get_event_loop()
        async with self._path_locks[path]:
            await loop.run_in_executor(None, self._append_file, path, data)

        return ExecutionResult(
            success=True,
            tool="append",
            path=path,
            bytes_written=len(data),
        )

    @staticmethod
    def _append_file(path: str, data: bytes):
        with open(path, "ab") as f:
            f.write(data)

    async def _touch(self, command: Command) -> ExecutionResult:
        """创建空文件或更新 mtime。"""
        if command.path is None:
            return ExecutionResult(success=False, tool="touch", error="缺少 path")


        path = self._resolve_path(command.path, "write")
        dir_path = os.path.dirname(path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)

        loop = asyncio.get_event_loop()
        async with self._path_locks[path]:
            await loop.run_in_executor(None, self._touch_file, path)

        return ExecutionResult(success=True, tool="touch", path=path)

    @staticmethod
    def _touch_file(path: str):
        with open(path, "ab"):
            pass
        os.utime(path, None)

    async def _copy(self, command: Command) -> ExecutionResult:
        """复制文件。"""
        if command.path is None or command.dest is None:
            return ExecutionResult(
                success=False,
                tool="copy",
                path=command.path or "",
                error="缺少 path 或 dest",
            )

        source = self._resolve_path(command.path, "read")
        dest = self._resolve_path(command.dest, "write")
        if not os.path.isfile(source):
            return ExecutionResult(
                success=False,
                tool="copy",
                path=source,
                error=f"源文件不存在: {source}",
            )
        dir_path = os.path.dirname(dest)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)

        loop = asyncio.get_event_loop()
        async with self._path_locks[dest]:
            await loop.run_in_executor(None, shutil.copyfile, source, dest)
        bytes_written = os.path.getsize(dest)

        return ExecutionResult(
            success=True,
            tool="copy",
            path=dest,
            bytes_written=bytes_written,
        )

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
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=self.security.bash_timeout_seconds,
            )
            timed_out = False
        except asyncio.TimeoutError:
            proc.kill()
            stdout_bytes, stderr_bytes = await proc.communicate()
            timed_out = True

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        truncated = False
        max_chars = self.security.max_bash_output_chars
        if max_chars > 0:
            if len(stdout) > max_chars:
                stdout = stdout[:max_chars]
                truncated = True
            if len(stderr) > max_chars:
                stderr = stderr[:max_chars]
                truncated = True

        return ExecutionResult(
            success=(proc.returncode == 0 and not timed_out),
            tool="bash",
            stdout=stdout,
            stderr=stderr,
            exit_code=proc.returncode,
            error="命令超时" if timed_out else (stderr if proc.returncode != 0 else ""),
            truncated=truncated,
            timed_out=timed_out,
        )

    async def _edit(self, command: Command) -> ExecutionResult:
        """编辑文件（精确替换）。"""
        if command.path is None or command.old_text is None or command.new_text is None:
            return ExecutionResult(
                success=False, tool="edit", path=command.path or "",
                error="缺少 path / old_text / new_text",
            )

        path = self._resolve_path(command.path, "edit")

        if not os.path.exists(path):
            return ExecutionResult(
                success=False, tool="edit", path=path,
                error=f"文件不存在: {path}",
            )

        loop = asyncio.get_event_loop()
        async with self._path_locks[path]:
            content, error = await loop.run_in_executor(
                None, self._do_edit, path, command.old_text, command.new_text
            )

        if error:
            return ExecutionResult(
                success=False, tool="edit", path=path, error=error,
            )

        return ExecutionResult(
            success=True, tool="edit", path=path,
            bytes_written=len(content.encode("utf-8")),
        )

    @staticmethod
    def _do_edit(path: str, old: str, new: str) -> tuple[str, Optional[str]]:
        """执行编辑，返回 (新内容, 错误)。"""
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        count = content.count(old)
        if count == 0:
            return content, f"oldText 在文件中未找到"
        if count > 1:
            return content, f"oldText 在文件中出现 {count} 次，必须唯一"

        new_content = content.replace(old, new, 1)

        with open(path, "w", encoding="utf-8") as f:
            f.write(new_content)

        return new_content, None

