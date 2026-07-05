"""
Streaming-safe text filter.

Models sometimes emit ThinkFlow command blocks in the visible text channel. The
agent still executes those commands through the text parser, but the terminal
must not display raw tool tags. This filter streams ordinary text immediately
while withholding possible command blocks until it can safely drop them.
"""

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
    """Return only text outside Markdown fenced code blocks for command parsing."""

    def __init__(self):
        self.buffer = ""
        self.in_fence = False
        self.fence_marker = ""

    def feed(self, text: str) -> str:
        self.buffer += text
        if not self.buffer:
            return ""

        out: list[str] = []
        while True:
            newline_at = self.buffer.find("\n")
            if newline_at == -1:
                break
            line = self.buffer[:newline_at + 1]
            self.buffer = self.buffer[newline_at + 1:]
            visible = self._process_line(line)
            if visible:
                out.append(visible)
        return "".join(out)

    def flush(self) -> str:
        tail = self.buffer
        self.buffer = ""
        return self._process_line(tail) if tail else ""

    def reset(self):
        self.buffer = ""
        self.in_fence = False
        self.fence_marker = ""

    def _process_line(self, line: str) -> str:
        stripped = line.lstrip()
        marker = ""
        if stripped.startswith("```"):
            marker = "```"
        elif stripped.startswith("~~~"):
            marker = "~~~"

        if marker:
            if not self.in_fence:
                self.in_fence = True
                self.fence_marker = marker
            elif marker == self.fence_marker:
                self.in_fence = False
                self.fence_marker = ""
            return ""

        return "" if self.in_fence else line
