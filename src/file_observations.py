"""Line-range reads and character coverage for exact file edits."""

import codecs
from dataclasses import dataclass, field
import hashlib

from .changes import MAX_GUARDED_FILE_BYTES


@dataclass
class ReadObservation:
    revision: str
    ranges: list[tuple[int, int]] = field(default_factory=list)

    def include(self, start: int, end: int) -> None:
        if start == end:
            return
        merged = []
        for left, right in sorted([*self.ranges, (start, end)]):
            if merged and left <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], right))
            else:
                merged.append((left, right))
        self.ranges = merged

    def covers(self, start: int, end: int) -> bool:
        return any(left <= start and end <= right for left, right in self.ranges)

    def after_edit(self, start: int, end: int, new_length: int, revision: str) -> None:
        delta = new_length - (end - start)
        updated = []
        for left, right in self.ranges:
            if right <= start:
                pass
            elif left >= end:
                left, right = left + delta, right + delta
            else:
                right += delta
            if left < right:
                updated.append((left, right))
        self.ranges = updated
        self.revision = revision


@dataclass
class FileSlice:
    content: str
    revision: str
    start: int
    end: int
    total_chars: int
    first_line: int
    last_line: int
    total_lines: int
    truncated: bool

    @property
    def read_note(self) -> str:
        note = f"[Lines {self.first_line}-{self.last_line} of {self.total_lines}]"
        if self.end < self.total_chars:
            if self.content.endswith("\n"):
                note += f" [More content: read offset={self.last_line + 1}]"
            else:
                note += f" [Line {self.last_line} clipped by output limit; use python to extract within this long line.]"
        return note


def read_file_range(path: str, offset: int, limit: int | None, max_chars: int) -> FileSlice:
    """Hash and count the file in chunks, retaining only the requested text window.

    LF ends a line, including its preceding CR when present. A final LF does not
    add a phantom line; an empty file has one empty line readable at offset 1.
    """
    digest = hashlib.sha256()
    decoder = codecs.getincrementaldecoder("utf-8")()
    pieces = []
    line = 1
    total_chars = 0
    output_chars = 0
    output_bytes = 0
    start = None
    last_line = offset
    ends_with_lf = False
    truncated = False
    stop_line = None if limit is None else offset + limit

    def consume(text: str) -> None:
        nonlocal line, total_chars, output_chars, output_bytes, start, last_line, ends_with_lf, truncated
        kept_pieces = []
        segments = text.split("\n")
        for index, segment in enumerate(segments):
            newline = index < len(segments) - 1
            if newline:
                segment += "\n"
            if not segment:
                continue
            selected = line >= offset and (stop_line is None or line < stop_line)
            if selected and not truncated:
                if start is None:
                    start = total_chars
                kept = segment[:max(0, max_chars - output_chars)] if max_chars > 0 else segment
                encoded = kept.encode("utf-8")
                remaining_bytes = MAX_GUARDED_FILE_BYTES - output_bytes
                if len(encoded) > remaining_bytes:
                    kept = encoded[:remaining_bytes].decode("utf-8", errors="ignore")
                if kept:
                    kept_pieces.append(kept)
                    output_chars += len(kept)
                    output_bytes += len(kept.encode("utf-8"))
                    last_line = line
                if len(kept) < len(segment):
                    truncated = True
            total_chars += len(segment)
            ends_with_lf = newline
            if newline:
                line += 1
        if kept_pieces:
            pieces.append("".join(kept_pieces))

    with open(path, "rb") as stream:
        while chunk := stream.read(64 * 1024):
            digest.update(chunk)
            consume(decoder.decode(chunk))
        consume(decoder.decode(b"", final=True))
    total_lines = max(1, line - (1 if ends_with_lf else 0))
    if offset > total_lines:
        raise ValueError(f"offset {offset} exceeds file length {total_lines} lines")
    if start is None:
        start = total_chars
    return FileSlice(
        content="".join(pieces), revision=digest.hexdigest(), start=start,
        end=start + output_chars, total_chars=total_chars, first_line=offset,
        last_line=last_line, total_lines=total_lines, truncated=truncated,
    )
