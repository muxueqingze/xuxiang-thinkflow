"""
ThinkFlow Renderer — 终端渲染

参考 pi 的 dark.json 配色方案。
正文用 rich Markdown 渲染，thinking 用 dim 色。
"""

import time
from typing import Optional

from rich.console import Console
from rich.text import Text
from rich.panel import Panel
from rich import box
from rich.markdown import Markdown
from rich.theme import Theme
from rich.live import Live
from rich.status import Status

from .parser import TOOLS
from .tool_registry import (
    TOOL_FLOW_LABELS,
    TOOL_KIND_LABELS,
    TOOL_RISK_HIGH,
    TOOL_RISK_LABELS,
    classify_tool_flow,
    classify_tool_kind,
    classify_tool_risk,
)

# ===== VS Code Dark + pi inspired palette =====
PI_COLORS = {
    "cyan": "#4ec9b0",
    "blue": "#569cd6",
    "green": "#6a9955",
    "red": "#f44747",
    "yellow": "#dcdcaa",
    "orange": "#ce9178",
    "text": "#d4d4d4",
    "gray": "#8a8a8a",
    "dim_gray": "#6a6a6a",
    "dark_gray": "#3c3c3c",
    "accent": "#9cdcfe",
    "magenta": "#c586c0",
    "surface": "#1e1e1e",
    "surface_2": "#252526",
    "surface_3": "#2d2d30",
}

THEME = Theme({
    # 工具
    "tool.write": f"bold {PI_COLORS['cyan']}",
    "tool.mkdir": f"bold {PI_COLORS['blue']}",
    "tool.bash": f"bold {PI_COLORS['green']}",
    "tool.edit": f"bold {PI_COLORS['magenta']}",
    "tool.read": f"bold {PI_COLORS['accent']}",
    "tool.append": f"bold {PI_COLORS['cyan']}",
    "tool.touch": f"bold {PI_COLORS['blue']}",
    "tool.copy": f"bold {PI_COLORS['magenta']}",
    "tool.native": f"bold {PI_COLORS['yellow']}",
    "tool.running": f"bold {PI_COLORS['orange']}",
    # 状态
    "ok": f"bold {PI_COLORS['green']}",
    "fail": f"bold {PI_COLORS['red']}",
    "warn": f"bold {PI_COLORS['yellow']}",
    # 文本
    "reply": PI_COLORS["text"],
    "think": f"italic {PI_COLORS['gray']}",
    "path": PI_COLORS["accent"],
    "bytes": PI_COLORS["dim_gray"],
    "tag": PI_COLORS["dim_gray"],
    # 系统
    "banner": f"bold {PI_COLORS['blue']}",
    "prompt": f"bold {PI_COLORS['blue']}",
    "divider": PI_COLORS["dark_gray"],
    "info": PI_COLORS["accent"],
    "user_bg": "#10243a",
    "tool_bg": "#172a3f",
    "ok_bg": "#1f3324",
    "err_bg": "#3a1f1f",
})

console = Console(theme=THEME)

TOOL_LABELS = {
    "write": ("tool.write", "WRITE"),
    "append": ("tool.append", "APPEND"),
    "mkdir": ("tool.mkdir", "MKDIR"),
    "touch": ("tool.touch", "TOUCH"),
    "copy": ("tool.copy", "COPY"),
    "bash": ("tool.bash", "$"),
    "edit": ("tool.edit", "EDIT"),
    "read": ("tool.read", "READ"),
}

_in_thinking = False
_text_buffer = ""
_text_streamed = False
_text_live: Optional[Live] = None
_activity_status: Optional[Status] = None
_last_live_update = 0.0
_LIVE_INTERVAL = 0.08
_strip_legacy_tool_tags = False
_tool_kind_overrides: dict[str, str] = {}
_tool_flow_overrides: dict[str, str] = {}
_tool_risk_overrides: dict[str, str] = {}


def set_allow_legacy_tool_tags(allow: bool):
    global _strip_legacy_tool_tags
    _strip_legacy_tool_tags = bool(allow)


def set_tool_kind_map(kinds: dict[str, str]):
    global _tool_kind_overrides
    _tool_kind_overrides = dict(kinds)


def set_tool_flow_map(flows: dict[str, str]):
    global _tool_flow_overrides
    _tool_flow_overrides = dict(flows)


def set_tool_risk_map(risks: dict[str, str]):
    global _tool_risk_overrides
    _tool_risk_overrides = dict(risks)


def _tool_kind(name: str) -> str:
    return _tool_kind_overrides.get(name) or classify_tool_kind(name)


def _tool_flow(name: str) -> str:
    return _tool_flow_overrides.get(name) or classify_tool_flow(name)


def _tool_risk(name: str) -> str:
    return _tool_risk_overrides.get(name) or classify_tool_risk(name)


def _tool_badges(name: str, need_result: bool = False) -> str:
    kind_labels = {
        "input": "输入",
        "output": "输出",
        "exec": "执行",
        "generate": "生成",
    }
    flow_labels = {
        "delayed": "流式",
        "blocking": "等待",
        "confirm": "确认",
    }
    risk_labels = {
        "low": "低",
        "medium": "中",
        "high": "高",
    }
    kind = kind_labels.get(_tool_kind(name), TOOL_KIND_LABELS.get(_tool_kind(name), "工具"))
    flow = "等待结果" if need_result else flow_labels.get(_tool_flow(name), TOOL_FLOW_LABELS.get(_tool_flow(name), "工具"))
    risk = _tool_risk(name)
    risk_label = risk_labels.get(risk, TOOL_RISK_LABELS.get(risk, risk.upper()))
    risk_style = "warn" if risk == TOOL_RISK_HIGH else "tag"
    return f"[tag]{kind}[/tag] [tag]{flow}[/tag] [{risk_style}]{risk_label}[/{risk_style}]"


def _stop_activity():
    global _activity_status
    if _activity_status is not None:
        try:
            _activity_status.stop()
        except Exception:
            pass
        _activity_status = None


def reset_transient_ui():
    """Stop live/status renderers after cancellation or unexpected errors."""
    global _text_live, _text_buffer, _text_streamed, _last_live_update, _in_thinking
    _stop_activity()
    if _text_live is not None:
        try:
            _text_live.stop()
        except Exception:
            pass
        _text_live = None
    _text_buffer = ""
    _text_streamed = False
    _last_live_update = 0.0
    _in_thinking = False


def render_command_start(
    tool: str,
    id: str,
    detail: str,
    need_result: bool = False,
):
    """Show an immediate activity hint when a deterministic tool starts."""
    global _in_thinking, _activity_status
    if _in_thinking:
        end_thinking()
    flush_text()
    _stop_activity()

    color, label = TOOL_LABELS.get(tool, ("white", tool.upper()))
    badges = _tool_badges(tool, need_result)
    line = f"  [tool.running]流式工具调用：{tool}[/tool.running] {badges} [tag]#{id}[/tag] [path]{detail}[/path]"

    if console.is_terminal:
        _activity_status = console.status(line, spinner="line")
        _activity_status.start()
    else:
        console.print(line, highlight=False)


def render_command_exec(
    tool: str,
    id: str,
    detail: str,
    success: bool,
    extra: str = "",
    need_result: bool = False,
):
    """渲染 ThinkFlow thinking/text 流式工具。"""
    global _in_thinking
    _stop_activity()
    if _in_thinking:
        end_thinking()
    flush_text()

    color, label = TOOL_LABELS.get(tool, ("white", tool.upper()))

    if success:
        status = "[ok]OK[/ok]"
        extra_str = f" [bytes]{extra}[/bytes]" if extra else ""
    else:
        status = "[fail]FAIL[/fail]"
        extra_str = f" [fail]{extra}[/fail]"
    badges = _tool_badges(tool, need_result)

    action = "流式工具完成" if success else "流式工具失败"
    console.print(
        f"  {status} [tag]{action}：[/tag][{color}]{tool}[/{color}] "
        f"{badges} [tag]#{id}[/tag] [path]{detail}[/path]{extra_str}",
        highlight=False,
    )


def render_tool_summary(records: list):
    """Render a deterministic end-of-turn command summary."""
    if not records:
        return
    global _in_thinking
    _stop_activity()
    if _in_thinking:
        end_thinking()
    flush_text()

    total = len(records)
    failed = sum(1 for rec in records if getattr(rec, "status", "") != "success")
    status = "[ok]OK[/ok]" if failed == 0 else "[fail]FAIL[/fail]"
    console.print(f"  [tag]流式工具汇总[/tag] {status} [bytes]{total} command(s)[/bytes]", highlight=False)

    shown = records[-8:]
    for rec in shown:
        color, label = TOOL_LABELS.get(getattr(rec, "tool", ""), ("white", str(getattr(rec, "tool", "")).upper()))
        kind = _tool_badges(getattr(rec, "tool", ""))
        mark = "[ok]✓[/ok]" if getattr(rec, "status", "") == "success" else "[fail]✗[/fail]"
        detail = _record_detail(rec)
        extra = ""
        if getattr(rec, "bytes_written", 0):
            extra = f" [bytes]{getattr(rec, 'bytes_written'):,} bytes[/bytes]"
        elif getattr(rec, "exit_code", None) is not None:
            extra = f" [bytes]exit={getattr(rec, 'exit_code')}[/bytes]"
        console.print(
            f"    {mark} {kind} [{color}]{getattr(rec, 'tool', '')}[/{color}]"
            f"[tag]#{getattr(rec, 'id', '')}[/tag] [path]{detail}[/path]{extra}",
            highlight=False,
        )
    if total > len(shown):
        console.print(f"    [tag]... {total - len(shown)} more command(s)[/tag]", highlight=False)


def _record_detail(rec) -> str:
    if getattr(rec, "tool", "") == "copy" and getattr(rec, "path", None) and getattr(rec, "dest", None):
        return f"{rec.path} -> {rec.dest}"
    return getattr(rec, "path", None) or getattr(rec, "dest", None) or getattr(rec, "cmd", None) or ""


def render_need_result_return(tool: str = "", id: str = ""):
    """渲染 need_result 打断后返回给模型的事件。"""
    suffix = f" {tool}#{id}" if tool and id else ""
    console.print(f"  [warn]工具结果已注入[/warn][tag]{suffix} 下一轮会继续[/tag]", highlight=False)


def render_stream_stop(reason: str):
    """渲染 provider stop reason。"""
    if reason in ("length", "max_tokens"):
        console.print(
            f"  [warn]STREAM STOP {reason}[/warn] [tag]模型输出达到 max_tokens，ThinkFlow 将尝试续写。[/tag]",
            highlight=False,
        )
    elif reason == "transport_error":
        console.print(
            "  [warn]STREAM STOP transport_error[/warn] [tag]流式连接中断，ThinkFlow 将尝试续写。[/tag]",
            highlight=False,
        )
    elif reason and reason not in ("stop", "end_turn", "tool_calls"):
        console.print(f"  [warn]STREAM STOP {reason}[/warn]", highlight=False)


def start_thinking():
    """thinking 块开始。"""
    global _in_thinking
    if not _in_thinking:
        _in_thinking = True
        console.print()


def render_thinking_snapshot(text: str):
    """渲染 thinking 内容 —— 暗色斜体。"""
    if text.strip():
        if not _in_thinking:
            start_thinking()
        console.print(Text(text, style="think"), end="")


def end_thinking():
    """thinking 块结束。"""
    global _in_thinking
    if _in_thinking:
        _in_thinking = False
        console.print()


import re as _re

# 匹配命令块（完整和不完整）
_TOOLS_PATTERN = "|".join(_re.escape(tool) for tool in TOOLS)


def _command_prefix_pattern() -> str:
    return r'(?:tf-)?' if _strip_legacy_tool_tags else r'tf-'


def strip_command_blocks(text: str) -> str:
    """移除正文 fallback 中的 ThinkFlow 命令块，避免 UI 和上下文里残留工具标签。"""
    return _strip_command_blocks_outside_fences(text)


def _strip_command_blocks_outside_fences(text: str) -> str:
    chunks: list[str] = []
    outside: list[str] = []
    in_fence = False
    fence_marker = ""

    def flush_outside():
        if outside:
            chunks.append(_strip_command_blocks_raw("".join(outside)))
            outside.clear()

    for line in text.splitlines(keepends=True):
        marker = _fence_marker(line)
        if marker:
            if not in_fence:
                flush_outside()
                in_fence = True
                fence_marker = marker
                chunks.append(line)
            elif marker == fence_marker:
                chunks.append(line)
                in_fence = False
                fence_marker = ""
            else:
                chunks.append(line)
            continue

        if in_fence:
            chunks.append(line)
        else:
            outside.append(line)

    flush_outside()
    return _re.sub(r'\n{3,}', '\n\n', "".join(chunks)).strip()


def _strip_command_blocks_raw(text: str) -> str:
    prefix = _command_prefix_pattern()
    block_re = _re.compile(
        rf'<{prefix}(?:{_TOOLS_PATTERN})\b[^>]*(?:/>|>[\s\S]*?</{prefix}(?:{_TOOLS_PATTERN})>)',
        _re.MULTILINE,
    )
    text = block_re.sub('', text)
    text = _re.sub(rf'<{prefix}(?:{_TOOLS_PATTERN})\b[^>]*$', '', text)
    text = _re.sub(rf'^\s*</{prefix}(?:{_TOOLS_PATTERN})>', '', text)
    return text


def _fence_marker(line: str) -> str:
    stripped = line.lstrip()
    if stripped.startswith("```"):
        return "```"
    if stripped.startswith("~~~"):
        return "~~~"
    return ""


def render_text_chunk(text: str):
    """流式渲染正文 chunk。终端中用 Live Markdown 预览，结束时渲染最终 Markdown。"""
    global _text_buffer, _text_streamed, _in_thinking, _text_live, _last_live_update
    if not text:
        return
    _stop_activity()
    if _in_thinking:
        end_thinking()
    _text_streamed = True
    _text_buffer += text
    if not console.is_terminal:
        return
    now = time.monotonic()
    renderable = _markdown_preview(strip_command_blocks(_text_buffer))
    if _text_live is None:
        _text_live = Live(renderable, console=console, transient=True, auto_refresh=False)
        _text_live.start()
        _last_live_update = now
        return
    if now - _last_live_update >= _LIVE_INTERVAL:
        _text_live.update(renderable, refresh=True)
        _last_live_update = now


def accumulate_text(text: str):
    """累积正文到 buffer，不输出。"""
    global _text_buffer, _in_thinking
    if _in_thinking:
        end_thinking()
    _text_buffer += text


def flush_text():
    """正文流结束 —— 过滤命令块后用 Markdown 渲染。"""
    global _text_buffer, _text_streamed, _text_live, _last_live_update
    if not _text_buffer.strip():
        _text_buffer = ""
        _text_streamed = False
        if _text_live is not None:
            _text_live.stop()
            _text_live = None
        return

    text = _text_buffer
    _text_buffer = ""

    text = strip_command_blocks(text)
    if _text_live is not None:
        _text_live.stop()
        _text_live = None

    _text_streamed = False
    _last_live_update = 0.0
    if text:
        console.print()
        console.print(Markdown(text), style="reply")
        console.print()


def _markdown_preview(text: str):
    """让不完整 Markdown 在 Live 预览里尽量稳定。"""
    text = text or " "
    if text.count("```") % 2 == 1:
        text += "\n```"
    return Markdown(text)


def render_tool_call(name: str, args: dict):
    """渲染传统 provider tool_call。"""
    global _in_thinking, _activity_status
    _stop_activity()
    if _in_thinking:
        end_thinking()
    flush_text()

    color = TOOL_LABELS.get(name, ("tool.native", name.upper()))[0]
    badges = _tool_badges(name)
    args_str = _format_args(args)
    line = f"  [tool.running]tool_call：{name}[/tool.running] {badges} {args_str}"
    if console.is_terminal:
        _activity_status = console.status(line, spinner="line")
        _activity_status.start()
    else:
        console.print(line, highlight=False)


def render_tool_result(name: str, result: str, max_lines: int = 8):
    """渲染 tool_result。"""
    _stop_activity()
    badges = _tool_badges(name)
    lines = result.strip().split("\n")
    if len(lines) > max_lines:
        shown = lines[:max_lines]
        shown.append(f"... ({len(lines) - max_lines} more lines)")
    else:
        shown = lines

    console.print(
        f"  [ok]OK[/ok] [tool.native]tool_result：{name}[/tool.native] {badges}",
        highlight=False,
    )
    for line in shown:
        console.print(f"  [think]{line}[/think]", highlight=False)


def _format_args(args: dict, max_len: int = 140) -> str:
    parts = []
    for key, value in args.items():
        text = str(value).replace("\n", "\\n")
        if len(text) > 48:
            text = text[:45] + "..."
        parts.append(f"{key}=[path]{text}[/path]")
    rendered = " ".join(parts)
    if len(rendered) > max_len:
        rendered = rendered[:max_len - 3] + "..."
    return rendered


def render_usage_compact(turns: int, prompt: int, completion: int, cmds: int, cache: int = 0):
    """底部 usage —— 极简暗色。"""
    parts = [
        f"[tag]turns[/tag] [bytes]{turns}[/bytes]",
        f"[tag]in[/tag] [bytes]{prompt:,}[/bytes]",
        f"[tag]out[/tag] [bytes]{completion:,}[/bytes]",
    ]
    if cmds:
        parts.append(f"[tag]cmds[/tag] [bytes]{cmds}[/bytes]")
    if cache:
        parts.append(f"[tag]cache[/tag] [bytes]{cache:,}[/bytes]")

    console.print(f"\n[tag] · [/tag]".join(parts), highlight=False)
    console.print()


def render_context_compact(agent):
    """底部状态：优先显示上下文、模型和压缩状态。"""
    stats = agent.message_stats()
    provider = agent.config.provider
    parts = [
        f"[tag]model[/tag] [bytes]{provider.model}[/bytes]",
        f"[tag]ctx[/tag] [bytes]{stats['chars']:,} chars[/bytes]",
        f"[tag]msgs[/tag] [bytes]{stats['messages']}[/bytes]",
    ]
    if agent.usage.api_calls:
        parts.append(f"[tag]api[/tag] [bytes]{agent.usage.api_calls}[/bytes]")
    if agent.usage.total_commands:
        parts.append(f"[tag]cmds[/tag] [bytes]{agent.usage.total_commands}[/bytes]")
    if agent.usage.estimated_saved_api_calls:
        parts.append(f"[tag]saved[/tag] [bytes]{agent.usage.estimated_saved_api_calls} calls[/bytes]")
    if stats["compactions"]:
        parts.append(f"[tag]compact[/tag] [bytes]{stats['compactions']}[/bytes]")
    if agent.usage.total_cached_tokens:
        parts.append(f"[tag]cache[/tag] [bytes]{agent.usage.total_cached_tokens:,}[/bytes]")

    console.print(f"\n[tag] · [/tag]".join(parts), highlight=False)
    console.print()


def render_error(msg: str):
    console.print(f"\n[fail]✗ {msg}[/fail]\n", highlight=False)


def render_info(msg: str):
    console.print(f"[info]{msg}[/info]", highlight=False)


def render_banner(model: str = ""):
    """渲染启动 banner。"""
    def _banner_panel():
        body = Text()
        body.append("续想", style="accent")
        body.append(" agent  ", style="banner")
        body.append("=v=", style="banner")
        if model:
            body.append("\n模型 ", style="tag")
            body.append(model, style="bytes")
        body.append("\n/help 命令    Esc 打断", style="tag")
        return Panel.fit(
            body,
            title="续想 agent",
            subtitle="流式执行 / 推理续接",
            border_style="blue",
            box=box.ASCII,
            padding=(0, 2),
        )

    console.print()
    console.print(_banner_panel())
    console.print()


def render_prompt():
    """渲染输入提示符。"""
    console.print()
    console.print("[prompt]❯[/prompt] ", end="", highlight=False)


def render_user_message(text: str):
    """Render user input as a compact chat line."""
    console.print()
    console.print(Text(f"> {text}", style="reply"))
