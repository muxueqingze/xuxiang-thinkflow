from __future__ import annotations

import asyncio
import contextlib
import html
import shutil
import time
from dataclasses import dataclass, field
from typing import Callable

from prompt_toolkit.application import Application
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import TextArea
from rich.console import Console

from .agent_loop import AgentLoop
from .session import SessionStore
from . import renderer


MAX_OUTPUT_CHARS = 120_000
OUTPUT_FLUSH_INTERVAL = 0.05


@dataclass
class TuiState:
    agent: AgentLoop
    store: SessionStore | None = None
    autosave: bool = True
    running_task: asyncio.Task | None = None
    pending_btw: list[str] = field(default_factory=list)
    output_text: str = ""
    output_chunks: list[str] = field(default_factory=list)
    output_flush_handle: asyncio.Handle | None = None
    app: Application | None = None
    output: TextArea | None = None
    handle_slash: Callable[[str, AgentLoop, SessionStore | None], bool] | None = None
    current_permission_mode: Callable[[AgentLoop], str] | None = None

    def is_running(self) -> bool:
        return self.running_task is not None and not self.running_task.done()

    def invalidate(self) -> None:
        if self.app is not None:
            with contextlib.suppress(Exception):
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


def _permission(state: TuiState) -> str:
    if state.current_permission_mode is None:
        return "workspace-write"
    return state.current_permission_mode(state.agent)


def _activity_text(state: TuiState) -> str:
    tool = state.agent.current_text_tool_activity() if hasattr(state.agent, "current_text_tool_activity") else ""
    if tool:
        phase = int(time.monotonic() * 4) % 6
        dots = "".join("*" if index == phase else "." for index in range(6))
        suffix = f" btw {len(state.pending_btw)}" if state.pending_btw else ""
        return f"流式工具调用：{tool} {dots}{suffix}"
    if state.is_running():
        suffix = f" btw {len(state.pending_btw)}" if state.pending_btw else ""
        return f"thinking{suffix}"
    return "ready"


def _status_html(state: TuiState) -> HTML:
    stats = state.agent.message_stats()
    provider = state.agent.config.provider
    activity = _activity_text(state)
    width = max(40, shutil.get_terminal_size((100, 24)).columns - 1)
    segments = [
        "ThinkFlow",
        provider.model,
        f"ctx {stats['chars']:,} chars / {stats['messages']} msgs",
        f"api {state.agent.usage.api_calls}",
    ]
    if not activity.startswith("流式工具调用"):
        segments.append(f"cache {state.agent.usage.total_cached_tokens:,}")
    segments.extend([f"perm {_permission(state)}", activity, "Esc cancel", "/help"])
    status = " | ".join(segments)
    if len(status) > width:
        status = status[: max(12, width - 1)] + "…"
    return HTML(
        "<style bg='#0b2239' fg='#9cdcfe'> "
        f"{html.escape(status)}"
        "</style>"
    )


async def run_tui(
    agent: AgentLoop,
    *,
    store: SessionStore | None,
    autosave: bool,
    handle_slash: Callable[[str, AgentLoop, SessionStore | None], bool],
    current_permission_mode: Callable[[AgentLoop], str],
) -> None:
    state = TuiState(
        agent=agent,
        store=store,
        autosave=autosave,
        handle_slash=handle_slash,
        current_permission_mode=current_permission_mode,
    )

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
        multiline=False,
        prompt="❯ ",
        focus_on_click=True,
    )

    async def start_agent_task(user_input: str, *, render_user: bool = True) -> None:
        if state.is_running():
            state.pending_btw.append(user_input)
            renderer.render_info("当前仍在运行，已作为 /btw 暂存")
            state.invalidate()
            return
        if render_user:
            renderer.render_user_message(user_input)
        state.running_task = asyncio.create_task(agent.run(user_input))
        asyncio.create_task(finalize_task(state.running_task))
        state.invalidate()

    async def finalize_task(task: asyncio.Task) -> None:
        try:
            await task
        except asyncio.CancelledError:
            renderer.reset_transient_ui()
            renderer.render_info("当前思考已打断")
        except Exception as exc:
            renderer.reset_transient_ui()
            renderer.render_error(f"运行失败: {exc}")
        finally:
            if hasattr(agent, "active_text_tool_count"):
                if hasattr(agent, "clear_text_tool_activity"):
                    agent.clear_text_tool_activity()
                else:
                    agent.active_text_tool_count = 0
            if state.running_task is task:
                state.running_task = None
            if state.autosave and state.store:
                state.store.save(agent.to_snapshot())
            state.invalidate()

        if state.pending_btw and not state.is_running():
            notes = state.pending_btw[:]
            state.pending_btw.clear()
            text = "用户在上一轮运行期间补充了旁注：\n" + "\n".join(
                f"- {item}" for item in notes
            )
            await start_agent_task(text, render_user=False)

    def cancel_running() -> bool:
        if state.is_running() and state.running_task is not None:
            state.running_task.cancel()
            state.invalidate()
            return True
        return False

    async def process_input(text: str) -> None:
        stripped = text.strip()
        if not stripped:
            return

        if stripped.startswith("/"):
            if stripped.startswith("/btw"):
                note = stripped[4:].strip()
                if not note:
                    renderer.render_error("用法: /btw 旁注内容")
                elif state.is_running():
                    state.pending_btw.append(note)
                    renderer.render_info("已加入旁注，当前轮结束后会自动继续处理")
                else:
                    agent.messages.append({"role": "user", "content": f"用户旁注：{note}"})
                    renderer.render_info("已加入上下文")
                state.invalidate()
                return
            if stripped == "/cancel":
                if cancel_running():
                    renderer.render_info("已请求打断当前思考")
                else:
                    renderer.render_info("当前没有正在运行的请求")
                return
            if stripped in ("/exit", "/quit", "/q"):
                cancel_running()
                if state.app is not None:
                    state.app.exit()
                return
            if state.handle_slash is not None:
                should_continue = state.handle_slash(stripped, agent, store)
                if not should_continue and state.app is not None:
                    cancel_running()
                    state.app.exit()
                state.invalidate()
                return

        if state.is_running():
            state.pending_btw.append(stripped)
            renderer.render_info("模型仍在运行，这条输入已作为 /btw 暂存")
            state.invalidate()
            return

        await start_agent_task(stripped)

    def accept_input(buffer) -> bool:
        text = buffer.text
        buffer.set_document(Document(""), bypass_readonly=True)
        asyncio.create_task(process_input(text))
        return True

    input_field.buffer.accept_handler = accept_input

    bindings = KeyBindings()

    @bindings.add("escape")
    def _cancel(event):
        if cancel_running():
            renderer.render_info("已请求打断当前思考")

    @bindings.add("c-c")
    def _ctrl_c(event):
        if cancel_running():
            renderer.render_info("已请求打断当前思考")
        else:
            event.app.exit()

    status_bar = Window(
        FormattedTextControl(lambda: _status_html(state)),
        height=1,
        dont_extend_height=True,
    )

    root = HSplit([
        output,
        status_bar,
        input_field,
    ])

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
        if state.output_flush_handle is not None:
            state.output_flush_handle.cancel()
            state.output_flush_handle = None
        state.flush_output()
        renderer.console = old_console
