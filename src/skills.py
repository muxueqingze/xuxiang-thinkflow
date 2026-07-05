"""Skill discovery compatible with Codex and Claude-style folders."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .runtime_context import default_skill_roots


@dataclass
class SkillConfig:
    enabled: bool = True
    roots: list[str] = field(default_factory=list)
    max_list_chars: int = 8000
    max_body_chars: int = 24000

    @classmethod
    def from_config(cls, data: dict) -> "SkillConfig":
        skills = ((data.get("interfaces", {}) or {}).get("skills", {}) or {})
        return cls(
            enabled=bool(skills.get("enabled", True)),
            roots=_as_list(skills.get("roots"), []),
            max_list_chars=int(skills.get("max_list_chars", 8000)),
            max_body_chars=int(skills.get("max_body_chars", 24000)),
        )


@dataclass(frozen=True)
class SkillInfo:
    name: str
    description: str
    path: str
    source: str
    kind: str = "skill"


class SkillManager:
    """Discover and read local SKILL.md files with a small context budget."""

    def __init__(self, cwd: str, config: SkillConfig):
        self.cwd = os.path.abspath(os.path.expanduser(cwd))
        self.config = config
        self._cache: list[SkillInfo] | None = None

    def list_skills(self, query: str = "", max_results: int = 80) -> list[SkillInfo]:
        if not self.config.enabled:
            return []
        skills = self._discover()
        q = query.strip().lower()
        if q:
            skills = [
                skill for skill in skills
                if q in skill.name.lower() or q in skill.description.lower() or q in skill.source.lower()
            ]
        return skills[:max_results]

    def render_list(self, query: str = "", max_results: int = 80) -> str:
        if not self.config.enabled:
            return "skills 未启用。请在 config.interfaces.skills.enabled 打开。"
        skills = self.list_skills(query=query, max_results=max_results)
        if not skills:
            return "没有找到匹配 skill。"

        lines = ["available skills:"]
        for skill in skills:
            lines.append(f"- {skill.name} [{skill.source}] {skill.description}\n  {skill.path}")
        text = "\n".join(lines)
        if len(text) > self.config.max_list_chars:
            text = text[: self.config.max_list_chars] + "\n\n[THINKFLOW TRUNCATED] skill 列表已截断。"
        return text

    def read_skill(self, name: str) -> str:
        if not self.config.enabled:
            return "skills 未启用。请在 config.interfaces.skills.enabled 打开。"
        needle = name.strip().lower()
        if not needle:
            return "缺少 name"
        matches = [
            skill for skill in self._discover()
            if skill.name.lower() == needle or os.path.abspath(skill.path).lower() == needle
        ]
        if not matches:
            partial = [skill for skill in self._discover() if needle in skill.name.lower()]
            if partial:
                names = ", ".join(skill.name for skill in partial[:10])
                return f"未找到精确匹配。相近 skill: {names}"
            return f"未找到 skill: {name}"

        skill = matches[0]
        try:
            with open(skill.path, "r", encoding="utf-8", errors="replace") as f:
                body = f.read()
        except OSError as exc:
            return f"读取 skill 失败: {exc}"

        truncated = False
        if len(body) > self.config.max_body_chars:
            body = body[: self.config.max_body_chars]
            truncated = True

        header = f"# {skill.name}\nsource: {skill.source}\npath: {skill.path}\n\n"
        if truncated:
            body += "\n\n[THINKFLOW TRUNCATED] SKILL.md 已截断。"
        return header + body

    def _discover(self) -> list[SkillInfo]:
        if self._cache is not None:
            return self._cache
        seen: set[str] = set()
        skills: list[SkillInfo] = []
        for root, source in self._candidate_roots():
            root_path = Path(root)
            if not root_path.exists():
                continue
            for info in self._discover_skill_root(root_path, source):
                real = os.path.realpath(info.path)
                if real in seen:
                    continue
                seen.add(real)
                skills.append(info)
        skills.sort(key=lambda item: (item.source, item.name.lower(), item.path.lower()))
        self._cache = skills
        return skills

    def _candidate_roots(self) -> list[tuple[str, str]]:
        roots: list[tuple[str, str]] = []
        for parent in _walk_to_repo_root(self.cwd):
            roots.append((os.path.join(parent, ".agents", "skills"), "codex-repo"))
            roots.append((os.path.join(parent, ".claude", "skills"), "claude-repo"))
            roots.append((os.path.join(parent, ".claude", "commands"), "claude-command"))
        home = os.path.expanduser("~")
        roots.extend([
            (os.path.join(home, ".agents", "skills"), "codex-user"),
            (os.path.join(home, ".claude", "skills"), "claude-user"),
            (os.path.join(home, ".claude", "commands"), "claude-command"),
            (os.path.join(home, ".codex", "skills"), "codex-user"),
        ])
        roots.extend((path, "thinkflow-user") for path in default_skill_roots())
        for raw in self.config.roots:
            path = os.path.expanduser(raw)
            if not os.path.isabs(path):
                path = os.path.join(self.cwd, path)
            roots.append((path, "config"))
        return roots

    def _discover_skill_root(self, root: Path, source: str) -> list[SkillInfo]:
        if source == "claude-command":
            return self._discover_claude_commands(root, source)
        skills = []
        for skill_file in root.rglob("SKILL.md"):
            info = _parse_skill_file(skill_file, source)
            if info:
                skills.append(info)
        return skills

    def _discover_claude_commands(self, root: Path, source: str) -> list[SkillInfo]:
        skills = []
        for command_file in root.rglob("*.md"):
            if command_file.name.upper() == "SKILL.MD":
                continue
            try:
                text = command_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            name = command_file.stem
            title = _first_heading(text)
            description = title or _first_plain_line(text) or "Claude Code slash command"
            skills.append(SkillInfo(
                name=f"claude-command:{name}",
                description=description,
                path=str(command_file),
                source=source,
                kind="claude-command",
            ))
        return skills


def _walk_to_repo_root(cwd: str) -> list[str]:
    path = os.path.abspath(cwd)
    parents = []
    while True:
        parents.append(path)
        if os.path.isdir(os.path.join(path, ".git")):
            break
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return parents


def _as_list(value, default: list[str]) -> list[str]:
    if value is None:
        return list(default)
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value]
    return list(default)


def _parse_skill_file(path: Path, source: str) -> SkillInfo | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    frontmatter = _frontmatter(text)
    name = frontmatter.get("name") or path.parent.name
    description = frontmatter.get("description") or _first_plain_line(_strip_frontmatter(text))
    return SkillInfo(
        name=name.strip(),
        description=(description or "No description").strip(),
        path=str(path),
        source=source,
    )


def _frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    block = text[3:end]
    data: dict[str, str] = {}
    for line in block.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip().strip('"').strip("'")
    return data


def _strip_frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end == -1:
        return text
    return text[end + 4 :]


def _first_heading(text: str) -> str:
    match = re.search(r"^#\s+(.+)$", text, flags=re.MULTILINE)
    return match.group(1).strip() if match else ""


def _first_plain_line(text: str) -> str:
    text = _strip_frontmatter(text)
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        return stripped[:240]
    return ""
