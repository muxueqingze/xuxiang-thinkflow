"""Streaming Markdown boundaries for ThinkFlow commands and visible text."""

import re

from .parser import TOOLS


class _MarkdownCommandStream:
    def __init__(self, allow_legacy_tags: bool, *, visible: bool):
        self._visible = visible
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

        if not self._pass_line:
            out = text if self._visible else ""
        else:
            line = text.removesuffix("\n")
            line_end = self._source_end - int(text.endswith("\n"))
            out = self._feed_plain(line, line_end)
            if ended:
                if self._visible and self._backticks:
                    ticks = "`" * self._backticks
                    out += ticks if self._inline_width else self._track_commands(ticks, line_end)
                if text.endswith("\n"):
                    out += self._track_commands("\n", self._source_end)
        if ended:
            # An unclosed inline example is scoped to the current logical line.
            self._inline_width = self._backticks = 0
            self._pass_line = None
        return out

    def _classify_line(self, text: str, ended: bool) -> bool | None:
        # Once a command body opens, its line prefixes belong to the payload.
        if self._active_close or (self._visible and self._start_re.match(self._pending_tag)):
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
            return self._track_commands(text, end_offset)

        out: list[str] = []
        position = end_offset - len(text)
        for part in re.findall(r"`+|[^`]+", text):
            position += len(part)
            if part.startswith("`") and not (self._active_close or self._start_re.match(self._pending_tag)):
                self._backticks += len(part)
                continue
            if self._backticks:
                ticks = self._backticks
                self._backticks = 0
                if not self._inline_width:
                    self._inline_width = ticks
                    # A delimiter keeps the gate from joining tag fragments.
                    delimiter = "`" * ticks if self._visible else "`"
                    out.append(self._track_commands(delimiter, position - len(part)))
                else:
                    if self._visible:
                        out.append("`" * ticks)
                    if ticks == self._inline_width:
                        self._inline_width = 0
            if not self._inline_width:
                out.append(self._track_commands(part, position))
            elif self._visible:
                out.append(part)
        return "".join(out)

    def _track_commands(self, text: str, end_offset: int) -> str:
        """Track complete boundaries while advancing through ordinary text.

        Retain partial open tags and a short closing-tag tail. The execution
        parser still validates commands and owns their full payloads.
        """
        original = text
        visible: list[str] = []
        text = self._pending_tag + text
        self._pending_tag = ""
        while text:
            if self._active_close:
                text = self._close_tail + text
                end = text.find(self._active_close)
                if end < 0:
                    self._close_tail = text[-(len(self._active_close) - 1):]
                    break
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
                if self._visible:
                    visible.append(text[:-len(self._pending_tag)] if self._pending_tag else text)
                break
            if self._visible:
                visible.append(text[:match.start()])
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
                elif char in ('"', "'"):
                    quote = char
                elif char == ">":
                    end = index
                    break
            if end is None:
                self._pending_tag = text[match.start():]
                break
            if not text[match.start():end].rstrip().endswith("/"):
                self._active_close = f"</{match.group('name')}>"
            else:
                tool = match.group('name').removeprefix("tf-")
                self.command_ends[tool] = end_offset - len(text) + end + 1
            text = text[end + 1:]
        return "".join(visible) if self._visible else original


class SafeTextStreamFilter(_MarkdownCommandStream):
    """Keep Markdown examples and remove live commands from visible text."""

    def __init__(self, allow_legacy_tags: bool = True):
        super().__init__(allow_legacy_tags, visible=True)

    def flush(self) -> str:
        visible = super().flush()
        self._pending_tag = self._active_close = self._close_tail = ""
        return visible


class MarkdownFenceCommandGate(_MarkdownCommandStream):
    """Exclude top-level fences and same-line inline code from live commands."""

    def __init__(self, allow_legacy_tags: bool = False):
        super().__init__(allow_legacy_tags, visible=False)


def strip_command_blocks(text: str, *, allow_legacy_tags: bool) -> str:
    stream_filter = SafeTextStreamFilter(allow_legacy_tags=allow_legacy_tags)
    visible = stream_filter.feed(text) + stream_filter.flush()
    return re.sub(r"\n{3,}", "\n\n", visible).strip()
