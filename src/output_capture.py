"""Stream decoded process output to storage while keeping a bounded tail."""
from __future__ import annotations

import codecs
import re

from .output_store import OutputStore


class OutputCapture:
    def __init__(self, store: OutputStore, max_chars: int, secrets=()):
        self.writer = store.open_stream()
        self.max_chars = max_chars
        self.text = ""
        self.chars = 0
        self.ref = ""
        self.complete = False
        self._pending = ""
        keys = sorted(set(filter(None, secrets)), key=len, reverse=True)
        self._hold = max((len(key) - 1 for key in keys), default=0)
        self._secrets = re.compile("|".join(re.escape(key) for key in keys)) if keys else None

    @property
    def truncated(self):
        return self.chars > self.max_chars

    def _append(self, text: str, *, final=False):
        self._pending += text
        stop = len(self._pending) if final else max(0, len(self._pending) - self._hold)
        if self._secrets and not final:
            for match in self._secrets.finditer(self._pending):
                if match.start() >= stop:
                    break
                if match.end() > stop:
                    stop = match.start()
                    break
        ready, self._pending = self._pending[:stop], self._pending[stop:]
        if self._secrets:
            ready = self._secrets.sub("[REDACTED]", ready)
        self.writer.write(ready)
        self.chars += len(ready)
        self.text = (self.text + ready)[-self.max_chars:]

    async def read(self, pipe):
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        try:
            while chunk := await pipe.read(65536):
                self._append(decoder.decode(chunk, final=False))
            self.complete = True
        finally:
            self._append(decoder.decode(b"", final=True), final=True)
            self.ref = self.writer.finish()
        return self.text, self.truncated
