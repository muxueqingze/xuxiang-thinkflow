"""Submission and run lifecycle shared by the interactive terminal views."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable
import time

from prompt_toolkit.utils import get_cwidth

from . import renderer
from .agent_loop import AgentLoop
from .provider import thinking_label
from .session import SessionStore


IDLE_COMMANDS = frozenset({
    "/clear", "/compact", "/model", "/new", "/resume", "/save", "/sandbox",
    "/thinking", "/verbose",
})
SESSION_COMMANDS = frozenset({"/clear", "/new", "/resume"})


class InteractiveController:
    def __init__(self, agent: AgentLoop, store: SessionStore | None, autosave: bool,
                 invalidate: Callable[[], None]):
        self.agent = agent
        self.store = store
        self.autosave = autosave
        self.invalidate = invalidate
        self.pending: deque[str] = deque()
        self.paused = False
        self._closing = False
        self._runner: asyncio.Task | None = None
        self._active: asyncio.Task | None = None
        self._started_at = 0.0

    @property
    def running(self) -> bool:
        return self._runner is not None

    def status(self) -> str:
        if self.running and self.paused:
            status = "正在停止"
        elif self.running:
            tool = self.agent.current_text_tool_activity()
            activity = f"流式工具：{tool}" if tool else "运行中"
            elapsed = int(time.monotonic() - self._started_at)
            status = f"Esc 停止 · {activity} {elapsed}s"
        else:
            status = "就绪"
        if self.pending:
            queue = "队列暂停" if self.paused else "待发送"
            status += f" · {queue} {len(self.pending)} · /queue"
        return status

    def status_line(self, columns: int, permission: str) -> str:
        stats = self.agent.message_stats()
        usage = self.agent.usage
        provider = self.agent.config.provider
        segments = [self.status(), f"思考 {thinking_label(provider)}", provider.model, f"权限 {permission}", "/help",
                    f"上下文 {stats['chars']:,} 字符 / {stats['messages']} 条",
                    f"API {usage.api_calls}", f"缓存 {usage.total_cached_tokens:,}"]
        status = " | ".join(segments)
        width = max(1, columns - 1)
        if get_cwidth(status) <= width:
            return status
        end = 0
        used = 0
        for char in status:
            used += get_cwidth(char)
            if used >= width:
                break
            end += 1
        return status[:end] + "…"

    def submit(self, text: str) -> None:
        self.pending.append(text)
        if self.running or self.paused:
            queue = "队列已暂停，/queue resume 继续" if self.paused else "已加入待发队列"
            renderer.render_info(f"{queue}（{len(self.pending)} 条）")
        self._start()
        self.invalidate()

    def _start(self) -> None:
        if self.pending and not (self.running or self.paused or self._closing):
            self._started_at = time.monotonic()
            self._runner = asyncio.create_task(self._run_queue())

    async def _run(self, text: str) -> None:
        with renderer.compact_tool_output():
            await self.agent.run(text)

    async def _run_queue(self) -> None:
        try:
            while self.pending and not (self.paused or self._closing):
                text = self.pending.popleft()
                self._started_at = time.monotonic()
                renderer.render_user_message(text)
                self._active = asyncio.create_task(self._run(text))
                self.invalidate()
                try:
                    await self._active
                    self.paused |= (self.agent.stopped_reason != "completed"
                                    or bool(self.agent.last_error))
                except asyncio.CancelledError:
                    self.paused = True
                    renderer.reset_transient_ui()
                    renderer.render_info("已停止当前任务")
                except Exception as exc:
                    self.paused = True
                    renderer.reset_transient_ui()
                    renderer.render_error(f"运行失败：{exc}")
                finally:
                    self._active = None
                    self.agent.clear_text_tool_activity()
                    if self.autosave and self.store is not None:
                        try:
                            self.store.save(self.agent.to_snapshot())
                        except OSError as exc:
                            self.paused = True
                            renderer.render_error(f"会话保存失败：{exc}")
        finally:
            self._runner = None
            if not self.pending:
                self.paused = False
            self.invalidate()

    def cancel(self) -> bool:
        if not self.running:
            return False
        if not self.paused:
            self.paused = True
            if self._active is not None and not self._active.done():
                self._active.cancel()
            self.invalidate()
        return True

    async def close(self) -> None:
        self._closing = True
        runner = self._runner
        if runner is not None:
            self.cancel()
            await runner

    def handle_command(self, command: str, arg: str) -> bool:
        if command == "/btw":
            if arg.strip():
                self.submit(arg)
            else:
                renderer.render_error("用法：/btw 补充内容")
        elif command == "/cancel":
            if not self.cancel():
                renderer.render_info("当前没有正在运行的任务")
        elif command == "/queue":
            self._queue_command(arg)
        elif self.running and (command in IDLE_COMMANDS or
                               command == "/models" and arg.strip().lower() in {"refresh", "--refresh", "-r"}):
            renderer.render_info("当前任务正在运行，请等任务结束或按 Esc 停止后再操作")
        elif self.pending and command in SESSION_COMMANDS:
            renderer.render_info("当前会话有待发消息，请先用 /queue 处理")
        else:
            return False
        return True

    def _queue_command(self, arg: str) -> None:
        parts = arg.split()
        action = parts[0].lower() if parts else ""
        if not action:
            if not self.pending:
                renderer.render_info("没有待发消息")
                return
            renderer.render_info("队列已暂停" if self.paused else "待发队列")
            for index, text in enumerate(self.pending, 1):
                preview = " ".join(text.split())
                if len(preview) > 100:
                    preview = preview[:99] + "…"
                renderer.console.print(f"  {index}. {preview}", markup=False, highlight=False)
            renderer.render_info("/queue remove 编号 移除一条，/queue resume 继续发送")
        elif action == "resume" and len(parts) == 1:
            if self.running or not self.pending:
                renderer.render_info("当前任务仍在结束中" if self.running else "没有待发消息")
                return
            self.paused = False
            self._start()
        elif action == "remove" and len(parts) == 2:
            try:
                index = int(parts[1])
            except ValueError:
                renderer.render_error("待发消息编号必须为整数")
                return
            if not 1 <= index <= len(self.pending):
                renderer.render_error("没有这个编号的待发消息")
                return
            del self.pending[index - 1]
            if not self.pending and not self.running:
                self.paused = False
            renderer.render_info("已移除待发消息")
        else:
            renderer.render_error("用法：/queue [resume|remove 编号]")
        self.invalidate()


def slash_parts(text: str) -> tuple[str, str]:
    """Recognize a command at the start, retaining its argument's line breaks."""
    text = text.lstrip()
    for index, char in enumerate(text):
        if char.isspace():
            return text[:index].lower(), text[index + 1:]
    return text.lower(), ""


def add_input_bindings(bindings, active):
    @bindings.add("c-m", filter=active)
    def submit(event):
        event.current_buffer.validate_and_handle()

    @bindings.add("c-j", filter=active)
    @bindings.add("escape", "c-m", filter=active)
    def newline(event):
        event.current_buffer.insert_text("\n")
