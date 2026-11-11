"""
ThinkFlow Session Store — 会话快照持久化

只保存 agent 运行所需的结构化状态，不保存 API key。
"""

import json
import os
import time
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from .runtime_context import cwd_session_path
from .output_store import OutputStore


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
        self._agent = None

    def bind(self, agent):
        """Persist execution boundaries without replacing the host's event view."""
        previous = getattr(agent, "session_store", None)
        if previous is not None:
            previous.unbind()
        if self._agent is not None:
            self.unbind()
        self._agent = agent
        agent.session_store = self
        agent.bind_output_store(self.output_dir)
        agent.events.observers.append(self._on_event)

    def unbind(self):
        if self._agent is not None:
            self._agent.events.observers.remove(self._on_event)
            self._agent.session_store = None
            self._agent = None

    @property
    def journal_path(self) -> Path:
        return self.path.with_suffix(".execution.jsonl")

    def _journal(self, event):
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        with self.journal_path.open("a+b") as stream:
            if stream.tell():
                stream.seek(-1, os.SEEK_END)
                if stream.read(1) != b"\n":
                    stream.write(b"\n")
            stream.write((json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())

    def _on_event(self, event):
        kind = event["type"]
        if kind == "tool_started":
            self._journal({**event, "session_id": self._agent.session_id})
            self.save(self._agent.to_snapshot(), history=False)
        elif kind == "tool_completed":
            # The receipt is committed before the journal marks the intent done.
            self.save(self._agent.to_snapshot(), history=False)
            self._journal({**event, "session_id": self._agent.session_id})
        elif kind in ("turn_started", "turn_finished", "run_finished", "plan_updated"):
            self.save(self._agent.to_snapshot(), history=kind == "run_finished")

    def _recover_intents(self, snapshot):
        if not self.journal_path.exists():
            return
        pending = {}
        with self.journal_path.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                except ValueError as exc:
                    raise ValueError(f"执行日志损坏，须核对工作区: {self.journal_path}") from exc
                if event.get("session_id") != snapshot.get("session_id"):
                    continue
                key = (event.get("channel"), event.get("id"))
                if event["type"] == "tool_started":
                    pending[key] = event
                elif event["type"] == "tool_completed":
                    pending.pop(key, None)
        records = snapshot.setdefault("context", {}).setdefault("records", [])
        known = {record["id"] for record in records if record.get('status') != 'skipped'}
        executed = snapshot.setdefault("executed_ids", [])
        fingerprints = snapshot.setdefault("command_fingerprints", {})
        for event in pending.values():
            identifier = event["id"]
            # A crash after snapshot replace but before journal completion already
            # has a committed receipt. Preserve that result instead of downgrading it.
            if identifier in known:
                continue
            records.append({
                "id": identifier, "tool": event["tool"], "status": "unknown",
                "path": event.get("path"), "dest": event.get("dest"), "cmd": event.get("cmd"),
                "flow": event.get("flow", "delayed"), "risk": event.get("risk", "low"),
                "need_result": event.get("need_result", False), "content_hash": event["input_hash"],
                "error": "进程在执行回执保存前退出；操作结果未知。先检查实际状态，不要自动重放。",
            })
            if identifier not in executed:
                executed.append(identifier)
            fingerprints[identifier] = event["input_hash"]
            if identifier.isdecimal():
                context = snapshot["context"]
                context["next_stamp"] = max(context.get("next_stamp", 1), int(identifier) + 1)

    def exists(self) -> bool:
        return self.path.exists()

    @property
    def output_dir(self) -> Path:
        return self.path.with_suffix(".outputs")

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
            snapshot = json.load(f)
        outputs = snapshot.get("outputs")
        if outputs and outputs.get("root"):
            outputs["root"] = str((self.path.parent / outputs["root"]).resolve())
        self._recover_intents(snapshot)
        return snapshot

    def save(self, snapshot: dict, *, history: bool = True):
        snapshot = dict(snapshot)
        if "outputs" in snapshot:
            outputs = OutputStore(root=self.output_dir)
            outputs.load(snapshot["outputs"])
            snapshot["outputs"] = outputs.to_dict()
            snapshot["outputs"]["root"] = Path(os.path.relpath(self.output_dir, self.path.parent)).as_posix()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._write_json_atomic(self.path, snapshot)
        if history:
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
        temp_name = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             prefix=path.name + ".", suffix=".tmp", delete=False) as f:
                temp_name = f.name
                json.dump(snapshot, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_name, path)
        finally:
            if temp_name and os.path.exists(temp_name):
                os.unlink(temp_name)

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
        if "outputs" in snapshot:
            archived["outputs"] = dict(snapshot["outputs"])
            archived["outputs"]["root"] = Path(os.path.relpath(self.output_dir, history_dir)).as_posix()
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
