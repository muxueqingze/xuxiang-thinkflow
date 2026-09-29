"""Private, durable file-change receipts and guarded text restoration.

Receipts start as ``unknown`` before any workspace mutation. Only a completed
write and a durable final receipt become ``success``. This is an intent log,
not a filesystem transaction: an external writer can race the final check.
"""

import base64
import difflib
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
import time
import uuid

from .security import SecurityPolicy


MISSING_REVISION = "missing"
MAX_SNAPSHOT_BYTES = 1024 * 1024
MAX_GUARDED_FILE_BYTES = 8 * 1024 * 1024


def content_revision(data: bytes | None) -> str:
    return MISSING_REVISION if data is None else hashlib.sha256(data).hexdigest()


def reject_symbolic_path(path: str | Path) -> None:
    """Do not follow symlinks or Windows junctions, including parent paths."""
    current = Path(os.path.abspath(path))
    for part in (current, *current.parents):
        if part.is_symlink() or getattr(os.path, "isjunction", lambda _: False)(part):
            raise PermissionError("symbolic_path: file operations cannot follow links or junctions")


def read_optional_bytes(path: str | Path) -> bytes | None:
    reject_symbolic_path(path)
    try:
        info = os.stat(path, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("not_regular_file: expected a regular file")
    if info.st_size > MAX_GUARDED_FILE_BYTES:
        raise ValueError("file_too_large: guarded file operations are limited to 8 MiB")
    with open(path, "rb") as stream:
        data = stream.read(MAX_GUARDED_FILE_BYTES + 1)
    if len(data) > MAX_GUARDED_FILE_BYTES:
        raise ValueError("file_too_large: file grew beyond the 8 MiB limit")
    return data


def atomic_write_bytes(path: str | Path, data: bytes) -> None:
    """Durable file data + atomic replacement; callers own precondition checks."""
    path = Path(path)
    reject_symbolic_path(path)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.tf-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            os.chmod(temporary, stat.S_IMODE(path.stat().st_mode))
        reject_symbolic_path(path)
        os.replace(temporary, path)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class ChangeStore:
    """One session's private receipts. Public methods never return snapshots.

    ``root`` must be a session-specific data directory, outside model inputs.
    ``lock`` serializes this store's executor and revert operations, not other
    processes or unrelated ChangeStore instances. New files are not deleted by
    revert; their removal requires an explicit recoverable UI workflow.
    """

    def __init__(self, root: str | Path, cwd: str | Path, *, max_snapshot_bytes: int = MAX_SNAPSHOT_BYTES):
        self.root = Path(os.path.abspath(root))
        self.cwd = Path(os.path.abspath(cwd))
        reject_symbolic_path(self.root)
        reject_symbolic_path(self.cwd)
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_snapshot_bytes = max(0, min(int(max_snapshot_bytes), MAX_SNAPSHOT_BYTES))
        self.lock = threading.RLock()
        self._security = SecurityPolicy(allowed_roots=[str(self.cwd)])

    def _target(self, path: str | Path) -> Path:
        path = Path(path)
        if not path.is_absolute():
            path = self.cwd / path
        target = Path(os.path.abspath(path))
        self._security.check_path(str(target), [str(self.cwd)], "write")
        reject_symbolic_path(target)
        # Never let the model overwrite the receipts used to authorize rollback.
        if target == self.root or self.root in target.parents:
            raise PermissionError("change_store_path: private receipts cannot be tool targets")
        return target

    def _entry_path(self, change_id: str) -> Path:
        if not isinstance(change_id, str) or len(change_id) != 32 or any(c not in "0123456789abcdef" for c in change_id):
            raise ValueError("invalid_change_id")
        reject_symbolic_path(self.root)
        path = self.root / f"{change_id}.json"
        reject_symbolic_path(path)
        return path

    def _save(self, entry: dict) -> None:
        data = json.dumps(entry, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        atomic_write_bytes(self._entry_path(entry["id"]), data)

    def _load(self, change_id: str) -> dict:
        path = self._entry_path(change_id)
        if path.stat().st_size > 3 * MAX_SNAPSHOT_BYTES + 64_000:
            raise ValueError("invalid_change_receipt: oversized")
        entry = json.loads(path.read_text(encoding="utf-8"))
        if entry.get("id") != change_id or entry.get("workspace") != os.path.normcase(str(self.cwd)):
            raise ValueError("invalid_change_receipt: foreign identity")
        self._target(entry["path"])
        return entry

    def _snapshot_reason(self, path: Path, before: bytes | None, after: bytes | None, kind: str) -> str:
        if kind != "file":
            return "directory_metadata_only"
        try:
            self._security.check_path(str(path), [str(self.cwd)], "read")
        except PermissionError:
            return "sensitive_path"
        for data in (before, after):
            if data is None:
                continue
            if len(data) > self.max_snapshot_bytes:
                return "snapshot_too_large"
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                return "binary_file"
            if "\x00" in text:
                return "binary_file"
            if self._security.redact_text(text) != text:
                return "sensitive_content"
        return ""

    def begin(self, path: str | Path, operation: str, before: bytes | None, after: bytes | None,
              *, command_id: str = "", kind: str = "file", undo_of: str = "") -> str:
        with self.lock:
            target = self._target(path)
            reason = self._snapshot_reason(target, before, after, kind)
            entry = {
                "version": 1, "id": uuid.uuid4().hex,
                "workspace": os.path.normcase(str(self.cwd)),
                "path": target.relative_to(self.cwd).as_posix(),
                "operation": operation, "command_id": command_id,
                "status": "unknown", "created_at": time.time(), "kind": kind,
                "before_hash": content_revision(before),
                "after_hash": "directory" if kind == "directory" else content_revision(after),
                "before_bytes": len(before) if before is not None else 0,
                "after_bytes": len(after) if after is not None else 0,
                "unavailable_reason": reason, "undo_of": undo_of,
            }
            if not reason:
                entry["snapshots"] = {
                    "before": base64.b64encode(before).decode("ascii") if before is not None else None,
                    "after": base64.b64encode(after).decode("ascii") if after is not None else None,
                }
            self._save(entry)
            return entry["id"]

    def complete(self, change_id: str) -> dict:
        with self.lock:
            entry = self._load(change_id)
            if entry["status"] != "unknown":
                raise ValueError("change_already_settled")
            target = self._target(entry["path"])
            if entry["kind"] == "file":
                if content_revision(read_optional_bytes(target)) != entry["after_hash"]:
                    raise ValueError("revision_conflict: file changed before receipt completion")
            elif not target.is_dir():
                raise ValueError("revision_conflict: directory was not created")
            entry["status"] = "success"
            entry["completed_at"] = time.time()
            self._save(entry)
            return self._metadata(entry)

    @staticmethod
    def _metadata(entry: dict) -> dict:
        value = {key: entry.get(key) for key in (
            "id", "path", "operation", "command_id", "status", "created_at", "completed_at",
            "before_hash", "after_hash", "before_bytes", "after_bytes", "kind", "undo_of",
        )}
        reason = entry.get("unavailable_reason", "")
        if entry["status"] != "success":
            reason = "unconfirmed_change"
        value["can_diff"] = not reason
        if not reason and entry["before_hash"] == MISSING_REVISION:
            reason = "new_file_requires_manual_recovery"
        if not reason and entry["operation"] == "touch":
            reason = "touch_timestamp_not_reversible"
        value["can_revert"] = not reason
        value["unavailable_reason"] = reason
        value["expected_revision"] = entry["after_hash"]
        return value

    def list_changes(self) -> list[dict]:
        with self.lock:
            reject_symbolic_path(self.root)
            entries = [self._metadata(self._load(path.stem)) for path in self.root.glob("*.json")]
            return sorted(entries, key=lambda entry: (entry["created_at"], entry["id"]))

    def _snapshots(self, entry: dict) -> tuple[bytes | None, bytes | None]:
        target = self._target(entry["path"])
        values = []
        for name in ("before", "after"):
            encoded = entry.get("snapshots", {}).get(name)
            data = None if encoded is None else base64.b64decode(encoded, validate=True)
            if content_revision(data) != entry[f"{name}_hash"]:
                raise ValueError("invalid_change_receipt: snapshot hash mismatch")
            values.append(data)
        reason = self._snapshot_reason(target, *values, entry["kind"])
        if reason:
            raise ValueError(reason)
        return tuple(values)

    def diff(self, change_id: str) -> dict:
        with self.lock:
            entry = self._load(change_id)
            meta = self._metadata(entry)
            if not meta["can_diff"]:
                raise ValueError(meta["unavailable_reason"])
            before, after = self._snapshots(entry)
            # Diff is the recorded operation, not an assertion about today's file.
            lines = difflib.unified_diff(
                (before or b"").decode("utf-8").splitlines(keepends=True),
                (after or b"").decode("utf-8").splitlines(keepends=True),
                fromfile=f"a/{entry['path']}" if before is not None else "/dev/null",
                tofile=f"b/{entry['path']}",
            )
            patch = "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
                            for line in lines)
            return {**meta, "diff": patch}

    def revert(self, change_id: str, expected_revision: str) -> dict:
        with self.lock:
            entry = self._load(change_id)
            meta = self._metadata(entry)
            if not meta["can_revert"]:
                raise ValueError(meta["unavailable_reason"])
            before, after = self._snapshots(entry)
            target = self._target(entry["path"])
            if not isinstance(expected_revision, str) or expected_revision != entry["after_hash"]:
                raise ValueError("revision_conflict: expected revision does not match the change")
            if content_revision(read_optional_bytes(target)) != expected_revision:
                raise ValueError("revision_conflict: file changed since this operation")
            undo_id = self.begin(target, "revert", after, before, undo_of=change_id)
            # Recheck after durable intent: never overwrite a known later edit.
            if content_revision(read_optional_bytes(target)) != expected_revision:
                raise ValueError("revision_conflict: file changed before restoration")
            atomic_write_bytes(target, before)
            return self.complete(undo_id)
