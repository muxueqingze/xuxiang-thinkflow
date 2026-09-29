"""Instance-scoped runtime events and the optional legacy terminal view."""

from collections.abc import Callable
import re

from . import renderer
from .text_filter import SafeTextStreamFilter


class RuntimeEvents:
    """Synchronous sinks may persist receipts; UI delivery should be queued."""

    def __init__(self, sink: Callable[[dict], None] | None = None):
        self.sink = sink
        self.sequence = 0
        self.turn = 0

    def emit(self, event_type: str, **payload):
        self.sequence += 1
        event = {"type": event_type, "seq": self.sequence, "turn": self.turn, **payload}
        if self.sink is not None:
            # Durable observers run before execution and must fail closed.
            self.sink(event)


class RuntimeView:
    """Headless instances never touch renderer globals or stdout."""

    def __init__(self, events: RuntimeEvents, *, allow_legacy_tags: bool = False):
        self.events = events
        self.allow_legacy_tags = allow_legacy_tags

    def __getattr__(self, name):
        terminal_method = getattr(renderer, name)

        def render(*args, **kwargs):
            if name == "render_text_chunk":
                self.events.emit("text_delta", text=args[0], channel="text")
            elif name == "render_error":
                self.events.emit("error", error=str(args[0]))
            if self.events.sink is None:
                return terminal_method(*args, **kwargs)
        return render

    def strip_command_blocks(self, text: str) -> str:
        stream_filter = SafeTextStreamFilter(allow_legacy_tags=self.allow_legacy_tags)
        return re.sub(r"\n{3,}", "\n\n", stream_filter.feed(text) + stream_filter.flush()).strip()

    def newline(self):
        if self.events.sink is None:
            print()
