"""Bounded character reads and literal searches for original text."""
from __future__ import annotations

import json
from typing import TextIO


_CHUNK_SIZE = 64 * 1024


def _skip(source: TextIO, count: int):
    while count:
        chunk = source.read(min(count, _CHUNK_SIZE))
        if not chunk:
            raise ValueError("Original text is shorter than its recorded length")
        count -= len(chunk)


def _find(source: TextIO, query: str, offset: int) -> int:
    _skip(source, offset)
    position = offset
    tail = ""
    overlap = len(query) - 1
    while chunk := source.read(_CHUNK_SIZE):
        window = tail + chunk
        match = window.find(query)
        if match >= 0:
            return position - len(tail) + match
        position += len(chunk)
        tail = window[-overlap:] if overlap else ""
    return -1


def recall_text(
    source: TextIO, total: int, ref: str, kind: str,
    offset: int, limit: int, query: str | None,
) -> str:
    """Read from a fresh stream; offsets count Unicode characters, including CRLF."""
    if type(offset) is not int or offset < 0:
        raise ValueError("offset must be a non-negative character offset")
    if type(limit) is not int or not 1 <= limit <= 200_000:
        raise ValueError("limit must be between 1 and 200000 characters")
    if offset > total:
        raise ValueError(f"offset exceeds {kind} length {total}")
    if query is not None and (not isinstance(query, str) or not query):
        raise ValueError("query must be a non-empty literal string")

    title = "user request" if kind == "request" else "tool output"
    if query is None:
        end = min(total, offset + limit)
        _skip(source, offset)
        text = source.read(end - offset)
        result = f"[Original {title} {ref}; characters {offset}:{end} of {total}]\n" + text
        if end < total:
            result += f"\n[More {kind} text: recall ref={ref} offset={end}]"
        return result

    match = _find(source, query, offset)
    quoted = json.dumps(query, ensure_ascii=False)
    if match < 0:
        return f"[No match for query={quoted} in {ref}; characters {offset}:{total} of {total}]"

    start = max(0, match - min(200, limit // 4))
    source.seek(0)
    _skip(source, start)
    prefix = source.read(match - start)
    line_start = max(prefix.rfind("\n"), prefix.rfind("\r")) + 1
    start += line_start
    prefix = prefix[line_start:]
    text = prefix + source.read(limit - len(prefix))
    line_end = len(text)
    for newline in ("\r", "\n"):
        position = text.find(newline, len(prefix))
        if position >= 0:
            line_end = min(line_end, position)
    text = text[:line_end]
    end = start + len(text)
    next_offset = match + len(query)
    return (
        f"[Original {title} {ref}; match at character {match}; characters {start}:{end} of {total}]\n"
        + text
        + f"\n[Continue search: recall ref={ref} offset={next_offset} query={quoted}]"
    )
