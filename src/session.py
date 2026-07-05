"""
ThinkFlow Session Store — 会话快照持久化

只保存 agent 运行所需的结构化状态，不保存 API key。
"""

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from .runtime_context import cwd_session_path


DEFAULT_SESSION_DIR = ".thinkflow"


class SessionStore:
    """基于 JSON 的轻量会话存储。"""

    def __init__(self, path: Optional[str] = None, cwd: str = "."):
        self.cwd = Path(cwd).expanduser()
        if not self.cwd.is_absolute():
            self.cwd = Path(os.getcwd()) / self.cwd
        if path:
            self.path = Path(path).expanduser()
        else:
            self.path = cwd_session_path(cwd)
        if not self.path.is_absolute():
            self.path = Path(os.getcwd()) / self.path

    def exists(self) -> bool:
        return self.path.exists()

    def discover(self) -> list[Path]:
        """Return known session snapshots, newest first."""
        candidates: dict[str, Path] = {}
        if self.path.exists():
            candidates[str(self.path.resolve())] = self.path

        roots = [self.path.parent, self.path.parent / "sessions", self.cwd / ".thinkflow" / "sessions"]
        if self.path.name == "session.json" and self.path.parent.parent.name == "sessions":
            roots.append(self.path.parent.parent)
        for root in roots:
            if not root.exists() or not root.is_dir():
                continue
            for item in root.glob("*.json"):
                if item.name.endswith(".tmp"):
                    continue
                candidates[str(item.resolve())] = item
            for item in root.glob("*/session.json"):
                if item.name.endswith(".tmp"):
                    continue
                candidates[str(item.resolve())] = item
            for item in root.glob("*/history/*.json"):
                if item.name.endswith(".tmp"):
                    continue
                candidates[str(item.resolve())] = item
            for item in root.glob("history/*.json"):
                if item.name.endswith(".tmp"):
                    continue
                candidates[str(item.resolve())] = item

        sorted_candidates = sorted(
            candidates.values(),
            key=lambda item: item.stat().st_mtime if item.exists() else 0,
            reverse=True,
        )
        seen_digests: set[str] = set()
        deduped: list[Path] = []
        for item in sorted_candidates:
            digest = self._snapshot_file_digest(item)
            if digest and digest in seen_digests:
                continue
            if digest:
                seen_digests.add(digest)
            deduped.append(item)
        return deduped

    @staticmethod
    def inspect(path: Path) -> dict:
        def preview(messages: list[dict], max_len: int = 90) -> str:
            for message in messages:
                if message.get("role") != "user":
                    continue
                text = str(message.get("content", "") or "").replace("\n", " ").strip()
                if not text:
                    continue
                return text[: max_len - 1] + "…" if len(text) > max_len else text
            return ""

        try:
            with path.open("r", encoding="utf-8-sig") as f:
                data = json.load(f)
        except Exception as exc:
            return {"path": str(path), "error": str(exc)}
        messages = data.get("messages", []) or []
        mtime = path.stat().st_mtime if path.exists() else 0
        return {
            "path": str(path),
            "mtime": mtime,
            "timestamp": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S") if mtime else "",
            "start": preview(messages),
            "messages": len(messages),
            "records": len(((data.get("context", {}) or {}).get("records", [])) or []),
            "turn_count": int(data.get("turn_count", 0) or 0),
        }
    def load(self) -> dict:
        with self.path.open("r", encoding="utf-8-sig") as f:
            return json.load(f)

    def save(self, snapshot: dict):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._write_json_atomic(self.path, snapshot)
        self._save_history_snapshot(snapshot)

    @staticmethod
    def _snapshot_digest(snapshot: dict) -> str:
        normalized = dict(snapshot)
        normalized.pop("_history_digest", None)
        normalized.pop("_history_saved_at", None)
        payload = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        import hashlib

        return hashlib.sha1(payload.encode("utf-8")).hexdigest()

    @classmethod
    def _snapshot_file_digest(cls, path: Path) -> str:
        try:
            with path.open("r", encoding="utf-8-sig") as f:
                data = json.load(f)
        except Exception:
            return ""
        return str(data.get("_history_digest") or cls._snapshot_digest(data))

    @staticmethod
    def _write_json_atomic(path: Path, snapshot: dict):
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(path.suffix + ".tmp")
        with temp_path.open("w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)
        os.replace(temp_path, path)

    def _save_history_snapshot(self, snapshot: dict, keep: int = 60):
        if not snapshot.get("messages") and not ((snapshot.get("context", {}) or {}).get("records")):
            return
        digest = self._snapshot_digest(snapshot)
        history_dir = self.path.parent / "history"
        history_dir.mkdir(parents=True, exist_ok=True)
        for item in history_dir.glob("*.json"):
            try:
                with item.open("r", encoding="utf-8-sig") as f:
                    existing = json.load(f)
                if existing.get("_history_digest") == digest:
                    return
            except Exception:
                continue
        archived = dict(snapshot)
        archived["_history_digest"] = digest
        archived["_history_saved_at"] = datetime.fromtimestamp(time.time()).isoformat(timespec="seconds")
        stamp = datetime.fromtimestamp(time.time()).strftime("%Y%m%d-%H%M%S")
        path = history_dir / f"{stamp}-{digest[:8]}.json"
        self._write_json_atomic(path, archived)

        items = sorted(
            [item for item in history_dir.glob("*.json") if not item.name.endswith(".tmp")],
            key=lambda item: item.stat().st_mtime if item.exists() else 0,
            reverse=True,
        )
        for old in items[keep:]:
            try:
                old.unlink()
            except OSError:
                pass
