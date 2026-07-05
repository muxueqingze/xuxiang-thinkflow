from __future__ import annotations

import hashlib
import os
from pathlib import Path


DEFAULT_CONTEXT_FILENAMES = ("AGENTS.md", "context.md")
WORKSPACE_CONTEXT_FILENAMES = ("AGENTS.md", "agents.md")


def thinkflow_home() -> Path:
    raw = os.environ.get("THINKFLOW_HOME")
    if raw:
        return Path(raw).expanduser().resolve()
    return (Path.home() / ".thinkflow").resolve()


def cwd_session_path(cwd: str) -> Path:
    resolved = Path(cwd).expanduser().resolve()
    digest = hashlib.sha1(str(resolved).encode("utf-8")).hexdigest()[:12]
    safe_name = resolved.name or "root"
    return thinkflow_home() / "sessions" / f"{safe_name}-{digest}" / "session.json"


def default_skill_roots() -> list[str]:
    return [str(thinkflow_home() / "skills")]


def collect_context_files(cwd: str, extra_paths: list[str] | None = None) -> list[Path]:
    files: list[Path] = []
    seen: set[str] = set()

    def add(path: Path):
        try:
            resolved = path.expanduser().resolve()
        except OSError:
            return
        key = str(resolved).lower()
        if key in seen or not resolved.is_file():
            return
        seen.add(key)
        files.append(resolved)

    home = thinkflow_home()
    for name in DEFAULT_CONTEXT_FILENAMES:
        add(home / name)

    current = Path(cwd).expanduser().resolve()
    for parent in [current, *current.parents]:
        for name in WORKSPACE_CONTEXT_FILENAMES:
            add(parent / name)
        if (parent / ".git").exists():
            break

    for raw in extra_paths or []:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = current / path
        add(path)

    return files


def build_context_prompt(cwd: str, extra_paths: list[str] | None = None, max_chars: int = 80_000) -> str:
    parts: list[str] = []
    remaining = max_chars
    for path in collect_context_files(cwd, extra_paths=extra_paths):
        if remaining <= 0:
            break
        try:
            body = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        header = f"\n\n# Context file: {path}\n"
        chunk = header + body.strip() + "\n"
        if len(chunk) > remaining:
            chunk = chunk[:remaining] + "\n[THINKFLOW CONTEXT TRUNCATED]\n"
        parts.append(chunk)
        remaining -= len(chunk)
    if not parts:
        return ""
    return "# ThinkFlow injected context\n" + "".join(parts).strip() + "\n"
