"""
Streaming-safe text filter.

Models sometimes emit ThinkFlow command blocks in the visible text channel. The
agent still executes those commands through the text parser, but the terminal
must not display raw tool tags. This filter streams ordinary text immediately
while withholding possible command blocks until it can safely drop them.
"""

import re

from .parser import TOOLS, build_open_tag_re


class SafeTextStreamFilter:
    """Remove ThinkFlow command blocks from text chunks without buffering all text."""

    def __init__(self, allow_legacy_tags: bool = True):
        self.buffer = ""
        self.line_buffer = ""
        self.in_fence = False
        self.fence_marker = ""
        self.allow_legacy_tags = allow_legacy_tags
        self._open_tag_re = build_open_tag_re(allow_legacy_tags)

    def feed(self, text: str) -> str:
        self.line_buffer += text
        out: list[str] = []

        while True:
            newline_at = self.line_buffer.find("\n")
            if newline_at == -1:
                break
            line = self.line_buffer[:newline_at + 1]
            self.line_buffer = self.line_buffer[newline_at + 1:]
            out.append(self._feed_line(line))

        if self.line_buffer and not self.in_fence and not self._could_be_fence_delimiter(self.line_buffer):
            out.append(self._feed_plain(self.line_buffer))
            self.line_buffer = ""

        return "".join(out)

    def _feed_plain(self, text: str) -> str:
        self.buffer += text
        out: list[str] = []

        while self.buffer:
            start = self.buffer.find("<")
            if start == -1:
                out.append(self.buffer)
                self.buffer = ""
                break

            if start > 0:
                out.append(self.buffer[:start])
                self.buffer = self.buffer[start:]
                continue

            state = self._tool_prefix_state(self.buffer)
            if state == "partial":
                break
            if state is None:
                out.append("<")
                self.buffer = self.buffer[1:]
                continue

            match = self._open_tag_re.match(self.buffer)
            if not match:
                if ">" not in self.buffer:
                    break
                out.append("<")
                self.buffer = self.buffer[1:]
                continue

            tool = match.group("tool")
            prefix = match.group("prefix") or ""
            if match.group("self_close") == "/":
                self.buffer = self.buffer[match.end():]
                continue

            close_tag = f"</{prefix}{tool}>"
            close_at = self.buffer.find(close_tag, match.end())
            if close_at == -1:
                break
            self.buffer = self.buffer[close_at + len(close_tag):]

        return "".join(out)

    def flush(self) -> str:
        """Return any remaining ordinary text; drop incomplete command blocks."""
        visible = self.feed("")
        if self.line_buffer:
            visible += self._feed_line(self.line_buffer)
            self.line_buffer = ""
        leftover = self.buffer
        self.buffer = ""

        if not leftover:
            return visible

        state = self._tool_prefix_state(leftover)
        if state in ("partial", "tool"):
            return visible
        return visible + leftover

    def reset(self):
        self.buffer = ""
        self.line_buffer = ""
        self.in_fence = False
        self.fence_marker = ""

    def _feed_line(self, line: str) -> str:
        # A fence within an unfinished command is file data, not visible
        # Markdown. Keep it in the command buffer until the closing tag arrives.
        if self.buffer and self._tool_prefix_state(self.buffer) in ("partial", "tool"):
            return self._feed_plain(line)
        marker = self._fence_marker(line)
        if marker:
            if not self.in_fence:
                self.in_fence = True
                self.fence_marker = marker
            elif marker == self.fence_marker:
                self.in_fence = False
                self.fence_marker = ""
            return line

        if self.in_fence:
            return line
        return self._feed_plain(line)

    @staticmethod
    def _fence_marker(line: str) -> str:
        stripped = line.lstrip()
        if stripped.startswith("```"):
            return "```"
        if stripped.startswith("~~~"):
            return "~~~"
        return ""

    @staticmethod
    def _could_be_fence_delimiter(text: str) -> bool:
        stripped = text.lstrip()
        return "```".startswith(stripped) or "~~~".startswith(stripped)

    def _tool_prefix_state(self, text: str) -> str | None:
        if not text.startswith("<"):
            return None

        for tool in TOOLS:
            starts = [f"<tf-{tool}"]
            if self.allow_legacy_tags:
                starts.append(f"<{tool}")
            for prefix in starts:
                if prefix.startswith(text):
                    return "partial"
                if text.startswith(prefix):
                    if len(text) == len(prefix):
                        return "partial"
                    next_char = text[len(prefix)]
                    if next_char.isspace():
                        return "tool"
                    return None
        return None


class MarkdownFenceCommandGate:
    """Exclude top-level fences and same-line inline code from live commands."""

    def __init__(self, allow_legacy_tags: bool = False):
        self._tag_prefixes = [f"<tf-{tool}" for tool in TOOLS]
        if allow_legacy_tags:
            self._tag_prefixes.extend(f"<{tool}" for tool in TOOLS)
        self._start_re = re.compile(
            r"<(?P<name>" + "|".join(re.escape(prefix[1:]) for prefix in self._tag_prefixes) + r")(?=\s|/?>)"
        )
        self.reset()

    def feed(self, text: str) -> str:
        lines = text.split("\n")
        out = [self._feed_line_piece(line + "\n", ended=True) for line in lines[:-1]]
        if lines[-1]:
            out.append(self._feed_line_piece(lines[-1], ended=False))
        return "".join(out)

    def flush(self) -> str:
        return self._feed_line_piece("", ended=True)

    def reset(self):
        self._line_prefix = ""
        self._source_end = 0
        self.command_ends: dict[str, int] = {}
        self._pass_line = None
        self._fence_marker = ""
        self._inline_width = 0
        self._backticks = 0
        self._pending_tag = ""
        self._active_close = ""
        self._close_tail = ""

    def _feed_line_piece(self, text: str, *, ended: bool) -> str:
        self._source_end += len(text)
        if self._pass_line is None:
            text = self._line_prefix + text
            self._line_prefix = ""
            self._pass_line = self._classify_line(text, ended)
            if self._pass_line is None:
                self._line_prefix = text
                return ""

        line = text.removesuffix("\n")
        line_end = self._source_end - int(text.endswith("\n"))
        out = self._feed_plain(line, line_end) if self._pass_line else ""
        if ended:
            if self._pass_line and text.endswith("\n"):
                self._track_commands("\n", self._source_end)
                out += "\n"
            # An unclosed inline example is scoped to the current logical line.
            self._inline_width = self._backticks = 0
            self._pass_line = None
        return out

    def _classify_line(self, text: str, ended: bool) -> bool | None:
        # Once a command body opens, its line prefixes belong to the payload.
        if self._active_close:
            return True
        start = text.lstrip()
        marker = start[:1]
        if self._fence_marker:
            closing = start.rstrip()
            if closing.strip(self._fence_marker[0]):
                return False
            if not ended:
                return None
            if closing.startswith(self._fence_marker):
                self._fence_marker = ""
            return False
        if not start:
            return True if ended else None
        if marker not in ("`", "~"):
            return True

        suffix = start.lstrip(marker)
        width = len(start) - len(suffix)
        if not suffix and not ended:
            return None
        if width < 3 or (marker == "`" and "`" in suffix):
            return True
        if marker == "`" and not ended:
            return None
        self._fence_marker = marker * width
        return False

    def _feed_plain(self, text: str, end_offset: int) -> str:
        if not self._inline_width and not self._backticks and "`" not in text:
            self._track_commands(text, end_offset)
            return text

        out: list[str] = []
        position = end_offset - len(text)
        for part in re.findall(r"`+|[^`]+", text):
            position += len(part)
            if part.startswith("`") and not (self._active_close or self._start_re.match(self._pending_tag)):
                self._backticks += len(part)
                continue
            if self._backticks:
                if not self._inline_width:
                    self._inline_width = self._backticks
                    # Keep a delimiter so removing a span cannot join tag fragments.
                    self._track_commands("`", position - len(part))
                    out.append("`")
                elif self._backticks == self._inline_width:
                    self._inline_width = 0
                self._backticks = 0
            if not self._inline_width:
                self._track_commands(part, position)
                out.append(part)
        return "".join(out)

    def _track_commands(self, text: str, end_offset: int) -> None:
        """Track boundaries only; command validation/execution stays in parser.

        Retain partial open tags and a short closing-tag tail, never a second
        copy of the whole payload. Quoted > characters do not end an open tag.
        """
        text = self._pending_tag + text
        self._pending_tag = ""
        while text:
            if self._active_close:
                text = self._close_tail + text
                end = text.find(self._active_close)
                if end < 0:
                    self._close_tail = text[-(len(self._active_close) - 1):]
                    return
                text = text[end + len(self._active_close):]
                tool = self._active_close[2:-1].removeprefix("tf-")
                self.command_ends[tool] = end_offset - len(text)
                self._active_close = self._close_tail = ""
                continue
            match = self._start_re.search(text)
            if match is None:
                tail = text[text.rfind("<"):] if "<" in text else ""
                if tail and any(prefix.startswith(tail) for prefix in self._tag_prefixes):
                    self._pending_tag = tail
                return
            quote, escaped, end = "", False, None
            for index in range(match.end(), len(text)):
                char = text[index]
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif quote:
                    if char == quote:
                        quote = ""
                elif char in ('"', "\'"):
                    quote = char
                elif char == ">":
                    end = index
                    break
            if end is None:
                self._pending_tag = text[match.start():]
                return
            if not text[match.start():end].rstrip().endswith("/"):
                self._active_close = f"</{match.group('name')}>"
            else:
                tool = match.group('name').removeprefix("tf-")
                self.command_ends[tool] = end_offset - len(text) + end + 1
            text = text[end + 1:]
