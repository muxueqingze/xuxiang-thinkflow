from __future__ import annotations

import asyncio
import html
from dataclasses import dataclass, field
from typing import Callable

from prompt_toolkit.application import Application
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Float, FloatContainer, HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import TextArea
from rich.console import Console

from .agent_loop import AgentLoop
from .interactive_controller import InteractiveController, add_input_bindings, slash_parts
from .session import SessionStore
from . import renderer


MAX_OUTPUT_CHARS = 120_000
OUTPUT_FLUSH_INTERVAL = 0.05


@dataclass
class TuiState:
    output_text: str = ""
    output_chunks: list[str] = field(default_factory=list)
    output_flush_handle: asyncio.Handle | None = None
    app: Application | None = None
    output: TextArea | None = None

    def invalidate(self) -> None:
        if self.app is not None:
            self.app.invalidate()

    def queue_output(self, text: str) -> None:
        if not text:
            return
        self.output_chunks.append(text)
        if self.output_flush_handle is not None:
            return
        loop = asyncio.get_running_loop()
        self.output_flush_handle = loop.call_later(
            OUTPUT_FLUSH_INTERVAL,
            self.flush_output,
        )

    def flush_output(self) -> None:
        self.output_flush_handle = None
        if not self.output_chunks or self.output is None:
            return
        self.output_text += "".join(self.output_chunks)
        self.output_chunks.clear()
        if len(self.output_text) > MAX_OUTPUT_CHARS:
            self.output_text = self.output_text[-MAX_OUTPUT_CHARS:]
            trim_at = self.output_text.find("\n")
            if trim_at >= 0:
                self.output_text = self.output_text[trim_at + 1 :]
        buffer = self.output.buffer
        buffer.set_document(
            Document(self.output_text, cursor_position=len(self.output_text)),
            bypass_readonly=True,
        )
        self.invalidate()


class TuiWriter:
    def __init__(self, state: TuiState):
        self.state = state

    def write(self, text: str) -> int:
        self.state.queue_output(text)
        return len(text)

    def flush(self) -> None:
        self.state.queue_output("")

    def isatty(self) -> bool:
        return False


def _status_html(controller: InteractiveController, permission: str, columns: int) -> HTML:
    status = controller.status_line(columns, permission)
    return HTML(f"<style bg='#0b2239' fg='#9cdcfe'> {html.escape(status)}</style>")


async def run_tui(
    agent: AgentLoop,
    *,
    store: SessionStore | None,
    autosave: bool,
    handle_slash: Callable[[str, AgentLoop, SessionStore | None], bool],
    current_permission_mode: Callable[[AgentLoop], str],
    completer=None,
) -> None:
    state = TuiState()
    controller = InteractiveController(agent, store, autosave, state.invalidate)

    output = TextArea(
        text="",
        read_only=True,
        scrollbar=True,
        focusable=True,
        wrap_lines=True,
    )
    state.output = output

    old_console = renderer.console
    renderer.console = Console(
        file=TuiWriter(state),
        force_terminal=False,
        color_system=None,
        highlight=False,
        theme=renderer.THEME,
        width=120,
    )

    input_field = TextArea(
        height=Dimension(min=1, max=5),
        multiline=True,
        prompt="❯ ",
        focus_on_click=True,
        history=InMemoryHistory(),
        completer=completer,
        complete_while_typing=True,
    )

    def accept_input(buffer) -> bool:
        text = buffer.text
        if text.strip():
            if text.lstrip().startswith("/"):
                command, arg = slash_parts(text)
                if not controller.handle_command(command, arg) and not handle_slash(text, agent, store):
                    app.exit()
            else:
                controller.submit(text)
        return False

    input_field.buffer.accept_handler = accept_input
    bindings = KeyBindings()
    add_input_bindings(bindings, Condition(lambda: app.layout.has_focus(input_field)))

    @bindings.add("escape")
    def _cancel(event):
        if input_field.buffer.complete_state is not None:
            input_field.buffer.cancel_completion()
        else:
            controller.cancel()

    @bindings.add("c-c")
    def _ctrl_c(event):
        if not controller.cancel():
            event.app.exit()

    status_bar = Window(
        FormattedTextControl(lambda: _status_html(
            controller, current_permission_mode(agent), app.output.get_size().columns,
        )),
        height=1,
        dont_extend_height=True,
    )

    root = FloatContainer(
        HSplit([output, status_bar, input_field]),
        floats=[Float(xcursor=True, ycursor=True, content=CompletionsMenu(max_height=6))],
    )

    app = Application(
        layout=Layout(root, focused_element=input_field),
        key_bindings=bindings,
        style=Style.from_dict({
            "textarea": "#d4d4d4",
            "prompt": "bold #569cd6",
        }),
        full_screen=True,
        mouse_support=True,
        refresh_interval=0.25,
    )
    state.app = app

    try:
        renderer.render_banner(agent.config.provider.model)
        await app.run_async()
    finally:
        await controller.close()
        if state.output_flush_handle is not None:
            state.output_flush_handle.cancel()
            state.output_flush_handle = None
        state.flush_output()
        renderer.console = old_console
