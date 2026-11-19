"""Optional package builds and runnable artifact checks for a single agent run."""

from __future__ import annotations

import os
import re
import shlex
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from .executor import ExecutionResult


_SCRIPT_RUNNERS = {
    ".py": {"python", "python3", "py"},
    ".js": {"node"}, ".mjs": {"node"}, ".cjs": {"node"},
    ".ts": {"node", "tsx", "ts-node"}, ".tsx": {"tsx", "ts-node"},
    ".sh": {"sh", "bash"}, ".ps1": {"pwsh", "powershell"},
    ".cmd": {"cmd"}, ".bat": {"cmd"},
}

# Accept literal words and quoted paths. Shell expansion and control operators
# outside quotes would make an exit status ambiguous. On Windows, percent and
# delayed-exclamation expansion can also occur inside double quotes.
_LITERAL_WORD = (r'(?:"[^"%!\r\n]*"|[\w@:/.,+\\=\-]+)' if os.name == "nt" else
                 r"""(?:'[^'\r\n]*'|"[^"$`\\\r\n]*"|[\w@:/.,+=\-]+)""")
_DIRECT_INVOCATION = re.compile(rf"[ \t]*{_LITERAL_WORD}(?:[ \t]+{_LITERAL_WORD})*[ \t]*")


def executed_script(cmd: str, cwd: str) -> Path | None:
    """Recognize a successful direct invocation. Compound shell commands need a separate run."""
    if not _DIRECT_INVOCATION.fullmatch(cmd):
        return None
    args = shlex.split(cmd, posix=os.name != "nt")
    if os.name == "nt":
        args = [arg.removeprefix('"').removesuffix('"') for arg in args]
    executable = Path(args[0])
    runner = os.path.normcase(executable.name).removesuffix(".exe")
    if executable.suffix.lower() in _SCRIPT_RUNNERS:
        # POSIX shells search PATH for bare names. cmd directly invokes batch files.
        if os.name == "nt" and executable.suffix.lower() not in {".bat", ".cmd"}:
            return None
        if os.name != "nt" and "/" not in args[0]:
            return None
        return (Path(cwd) / executable).resolve()

    index = 1
    if runner in {"powershell", "pwsh"}:
        while index < len(args) and args[index].lower() in {"-noprofile", "-noninteractive", "-nologo"}:
            index += 1
        if index == len(args) or args[index].lower() != "-file":
            return None
        index += 1
    elif runner == "cmd":
        if index == len(args) or args[index].lower() != "/c":
            return None
        index += 1
    elif runner in _SCRIPT_RUNNERS[".py"]:
        while index < len(args) and (args[index] in {"-B", "-u", "-I", "-O"}
                                     or runner == "py" and re.fullmatch(r"-3(?:\.\d+)?", args[index])):
            index += 1
    if index >= len(args) or args[index].startswith("-"):
        return None
    target = Path(args[index])
    if runner in _SCRIPT_RUNNERS.get(target.suffix.lower(), ()):
        return (Path(cwd) / target).resolve()
    return None


def package_projects(cwd: str, changed_paths: set[Path]) -> list[Path]:
    root = Path(cwd).resolve()
    projects = set()
    for path in changed_paths:
        if not path.is_relative_to(root):
            continue
        directory = path if path.is_dir() else path.parent
        while directory.is_relative_to(root):
            manifest = directory / "package.json"
            if manifest.is_file() and manifest.resolve().is_relative_to(root):
                projects.add(directory)
                break
            if directory == root:
                break
            directory = directory.parent
    return sorted(projects)


@dataclass
class VerificationResult:
    project_dir: Path
    steps: list[tuple[str, ExecutionResult]] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.steps[-1][1].success

    def to_feedback(self, max_chars: int = 12000) -> str:
        lines = [
            "[THINKFLOW DELIVERY CHECK FAILED]",
            "Pre-delivery verification found errors. Fix the files, then finish only after verification can pass.",
            f"project_dir: {self.project_dir}",
        ]
        for command, result in self.steps:
            lines.extend(("", f"$ {command}", f"exit_code: {result.exit_code}"))
            if result.error and result.error != result.stderr:
                lines.append(result.error)
            if result.stdout:
                lines.extend(("--- stdout ---", result.stdout))
            if result.stderr:
                lines.extend(("--- stderr ---", result.stderr))
        text = "\n".join(lines)
        if len(text) <= max_chars:
            return text
        head = max_chars // 2
        tail = max_chars - head - 80
        return text[:head] + f"\n...[THINKFLOW DELIVERY FEEDBACK CLIPPED {len(text) - max_chars} CHARS]...\n" + text[-tail:]


async def verify_delivery(project_dir: Path, run: Callable[[str, Path], Awaitable[ExecutionResult]]) -> VerificationResult:
    result = VerificationResult(project_dir)
    commands = ["npm run build"]
    if not (project_dir / "node_modules").is_dir():
        commands.insert(0, "npm install")
    for command in commands:
        step = await run(command, project_dir)
        result.steps.append((command, step))
        if not step.success:
            break
    return result


class DeliveryChecks:
    def __init__(self):
        self.revisions: dict[Path, str] = {}
        self.changed_paths: set[Path] = set()
        self.failures: dict[Path, VerificationResult] = {}
        self.scripts: dict[Path, str] = {}
        self.prompted_scripts: dict[Path, str] = {}

    def observe(self, name: str, arguments: dict, result: ExecutionResult, *, cwd: str, builds: bool, scripts: bool):
        if not result.success:
            return
        if name in {"write", "append", "edit", "copy", "touch", "mkdir"}:
            path = Path(result.path).resolve()  # Executor returns the actual destination, including for copy.
            if self.revisions.get(path) != result.revision:
                self.revisions[path] = result.revision
                if builds:
                    self.changed_paths.add(path)
            if scripts and name != "mkdir" and path.suffix.lower() in _SCRIPT_RUNNERS:
                if path not in self.scripts:
                    self.prompted_scripts.pop(path, None)
                self.scripts[path] = result.revision
        elif scripts and self.scripts and name == "bash" and result.exit_code == 0:
            path = executed_script(arguments["cmd"], cwd)
            if path is not None:
                self.scripts.pop(path, None)

    def script_feedback(self) -> str:
        paths = "\n".join(f"- {path}" for path in self.scripts)
        return ("[THINKFLOW DELIVERY VERIFY REQUIRED — 可运行脚本等待验证]\n"
                f"{paths}\n请用 bash 工具逐个直接运行以上文件，例如 python \"脚本路径\" 或 node \"脚本路径\"。"
                "每次调用只运行一个脚本；失败后根据回执修复并重新运行。")
