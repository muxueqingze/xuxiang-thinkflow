"""Content-addressed storage for complete, model-visible tool output."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
from pathlib import Path

from .recall import recall_text


def _filename(ref: str) -> str:
    if not isinstance(ref, str) or not re.fullmatch(r"output:[0-9a-f]{64}", ref):
        raise ValueError("ref must be output:<64 lowercase hex characters>")
    return ref[7:] + ".txt"


class _OutputWriter:
    def __init__(self, store: "OutputStore"):
        self._store = store
        self._file = None
        self._path = None
        self._hash = hashlib.sha256()
        self._chars = 0
        self._bytes = 0

    def write(self, text: str):
        if not text:
            return
        payload = text.encode("utf-8")
        if self._file is None:
            self._file = tempfile.NamedTemporaryFile(
                mode="wb", dir=self._store._ensure_root(),
                prefix="output-", suffix=".part", delete=False,
            )
            self._path = Path(self._file.name)
        self._file.write(payload)
        self._hash.update(payload)
        self._chars += len(text)
        self._bytes += len(payload)

    def finish(self) -> str:
        if self._file is None:
            return ""
        ref = "output:" + self._hash.hexdigest()
        try:
            self._file.flush()
            os.fsync(self._file.fileno())
            self._file.close()
            destination = self._store.root / _filename(ref)
            if not destination.exists():
                os.replace(self._path, destination)
            self._store.entries[ref] = {"chars": self._chars, "bytes": self._bytes}
            return ref
        finally:
            self._file.close()
            if self._path.exists():
                self._path.unlink()


class OutputStore:
    def __init__(self, root: Path | str | None = None):
        self._root = Path(root).expanduser().resolve() if root is not None else None
        self._temporary = None
        self.entries: dict[str, dict[str, int]] = {}

    @property
    def root(self) -> Path | None:
        return self._root

    def _ensure_root(self) -> Path:
        if self._root is None:
            self._temporary = tempfile.TemporaryDirectory(prefix="thinkflow-output-")
            self._root = Path(self._temporary.name).resolve()
        else:
            self._root.mkdir(parents=True, exist_ok=True)
        return self._root

    def open_stream(self) -> _OutputWriter:
        return _OutputWriter(self)

    def put_text(self, text: str) -> str:
        if not text:
            return ""
        payload = text.encode("utf-8")
        ref = "output:" + hashlib.sha256(payload).hexdigest()
        if self._root is not None and (self._root / _filename(ref)).exists():
            self.entries[ref] = {"chars": len(text), "bytes": len(payload)}
            return ref
        writer = self.open_stream()
        writer.write(text)
        return writer.finish()

    def path_for(self, ref: str) -> Path:
        filename = _filename(ref)
        if ref not in self.entries:
            raise ValueError(f"Unknown output reference: {ref}")
        return self._root / filename

    def recall(self, ref: str, offset: int = 0, limit: int = 8000, *, display_ref=None, query=None) -> str:
        path = self.path_for(ref)
        total = self.entries[ref]["chars"]
        with path.open("r", encoding="utf-8", newline="") as source:
            return recall_text(source, total, display_ref or ref, "output", offset, limit, query)

    def to_dict(self) -> dict:
        return {
            "root": str(self._root) if self._root is not None else "",
            "entries": {ref: dict(metadata) for ref, metadata in self.entries.items()},
        }

    @staticmethod
    def _copy_files(source: Path, destination: Path, entries: dict):
        if not entries or source == destination:
            return
        for ref in entries:
            filename = _filename(ref)
            original = source / filename
            target = destination / filename
            if target.exists():
                continue
            destination.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with original.open("rb") as incoming, tempfile.NamedTemporaryFile(
                    mode="wb", dir=destination, prefix="output-", suffix=".part", delete=False,
                ) as outgoing:
                    temporary = Path(outgoing.name)
                    shutil.copyfileobj(incoming, outgoing)
                    outgoing.flush()
                    os.fsync(outgoing.fileno())
                os.replace(temporary, target)
            finally:
                if temporary is not None and temporary.exists():
                    temporary.unlink()

    def load(self, data: dict):
        entries = {}
        for ref, metadata in data.get("entries", {}).items():
            _filename(ref)
            for key in ("chars", "bytes"):
                if type(metadata[key]) is not int or metadata[key] < 0:
                    raise ValueError(f"Invalid output {key}: {ref}")
            entries[ref] = {"chars": metadata["chars"], "bytes": metadata["bytes"]}
        if entries:
            source = Path(data["root"])
            if not source.is_absolute():
                raise ValueError("Output source root must be absolute")
            self._copy_files(source.resolve(), self._ensure_root(), entries)
        self.entries = entries

    def bind(self, root: Path | str):
        destination = Path(root).expanduser().resolve()
        if self._root == destination:
            return
        if self.entries:
            self._copy_files(self._root, destination, self.entries)
        if self._temporary is not None:
            self._temporary.cleanup()
            self._temporary = None
        self._root = destination

    def clear(self):
        self.entries.clear()
        if self._temporary is not None:
            self._temporary.cleanup()
            self._temporary = None
            self._root = None

    def close(self):
        if self._temporary is not None:
            self.clear()
