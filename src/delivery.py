"""Pre-delivery verification helpers.

This module runs deterministic local checks after the model has written files.
It is intentionally small and conservative: only known project manifests trigger
checks, and only fixed commands are executed.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field


@dataclass
class VerificationStep:
    command: str
    exit_code: int | None
    stdout: str = ""
    stderr: str = ""


@dataclass
class VerificationResult:
    attempted: bool
    success: bool
    project_dir: str = ""
    reason: str = ""
    steps: list[VerificationStep] = field(default_factory=list)

    def to_feedback(self, max_chars: int = 12000) -> str:
        lines = [
            "[THINKFLOW DELIVERY CHECK FAILED]",
            "Pre-delivery verification found errors. Fix the files, then finish only after verification can pass.",
            f"project_dir: {self.project_dir}",
            f"reason: {self.reason}",
        ]
        for step in self.steps:
            lines.append("")
            lines.append(f"$ {step.command}")
            lines.append(f"exit_code: {step.exit_code}")
            if step.stdout:
                lines.append("--- stdout ---")
                lines.append(step.stdout)
            if step.stderr:
                lines.append("--- stderr ---")
                lines.append(step.stderr)
        text = "\n".join(lines)
        if len(text) <= max_chars:
            return text
        head = max_chars // 2
        tail = max_chars - head - 80
        return text[:head] + f"\n...[THINKFLOW DELIVERY FEEDBACK CLIPPED {len(text) - max_chars} CHARS]...\n" + text[-tail:]


async def verify_delivery(cwd: str, changed_paths: list[str]) -> VerificationResult:
    """Run local checks for projects touched by this turn."""
    project_dir = _find_package_project(cwd, changed_paths)
    if not project_dir:
        return VerificationResult(attempted=False, success=True, reason="no supported project manifest")

    steps: list[VerificationStep] = []
    if not os.path.isdir(os.path.join(project_dir, "node_modules")):
        install = await _run("npm install", project_dir)
        steps.append(install)
        if install.exit_code != 0:
            return VerificationResult(
                attempted=True,
                success=False,
                project_dir=project_dir,
                reason="npm install failed",
                steps=steps,
            )

    build = await _run("npm run build", project_dir)
    steps.append(build)
    return VerificationResult(
        attempted=True,
        success=build.exit_code == 0,
        project_dir=project_dir,
        reason="ok" if build.exit_code == 0 else "npm run build failed",
        steps=steps,
    )


def _find_package_project(cwd: str, changed_paths: list[str]) -> str:
    candidates: list[str] = []
    for path in changed_paths:
        if not path:
            continue
        abs_path = path if os.path.isabs(path) else os.path.join(cwd, path)
        cur = abs_path if os.path.isdir(abs_path) else os.path.dirname(abs_path)
        while cur:
            if os.path.isfile(os.path.join(cur, "package.json")):
                candidates.append(os.path.abspath(cur))
                break
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent
    if not candidates:
        return ""
    # Prefer the shallowest common project touched by the turn.
    return sorted(set(candidates), key=lambda item: (len(item), item))[0]


async def _run(command: str, cwd: str) -> VerificationStep:
    proc = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    stdout_bytes, stderr_bytes = await proc.communicate()
    return VerificationStep(
        command=command,
        exit_code=proc.returncode,
        stdout=stdout_bytes.decode("utf-8", errors="replace"),
        stderr=stderr_bytes.decode("utf-8", errors="replace"),
    )
