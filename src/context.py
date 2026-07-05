"""
ThinkFlow Context Manager — 上下文管理

管理命令记录、戳记分配、注入状态。
负责在每次 API 调用前，把未注入的命令构建为注入文本。
"""

import hashlib
import time
from dataclasses import dataclass, asdict
from typing import Optional

from .parser import Command
from .executor import ExecutionResult


@dataclass
class CommandRecord:
    """已执行命令的完整记录"""
    id: str
    tool: str
    path: Optional[str] = None
    dest: Optional[str] = None
    cmd: Optional[str] = None
    content: Optional[str] = None       # write 的正文
    old_text: Optional[str] = None      # edit 的旧文本
    new_text: Optional[str] = None      # edit 的新文本
    need_result: bool = False
    flow: str = "delayed"
    risk: str = "low"
    content_hash: str = ""
    output_summary: str = ""
    created_at: float = 0.0
    completed_at: float = 0.0
    injected: int = 0                   # 0=未注入全局上下文, 1=已注入
    # 执行结果
    status: str = "pending"             # success / failed / pending
    error: str = ""
    bytes_written: int = 0
    stdout: str = ""
    stderr: str = ""
    exit_code: Optional[int] = None

    @staticmethod
    def _clip_text(text: str, max_chars: int = 4000) -> str:
        if max_chars <= 0 or len(text) <= max_chars:
            return text
        if max_chars <= 120:
            return text[:max_chars]
        head = max_chars // 2
        tail = max_chars - head - 80
        return (
            text[:head]
            + f"\n...[THINKFLOW CLIPPED {len(text) - max_chars} CHARS]...\n"
            + text[-tail:]
        )

    def to_injection_string(self, max_content_chars: int = 4000) -> str:
        """Build text injected into the model context."""
        lines = []

        attrs = [f'id="{self.id}"']

        if self.path:
            attrs.append(f'path="{self.path}"')
        if self.dest:
            attrs.append(f'dest="{self.dest}"')
        if self.cmd:
            safe_cmd = self.cmd.replace('"', "'")
            attrs.append(f'cmd="{safe_cmd}"')

        attrs.append(f'status="{self.status}"')
        attrs.append(f'flow="{self.flow}"')
        attrs.append(f'risk="{self.risk}"')
        if self.content_hash:
            attrs.append(f'hash="{self.content_hash}"')

        if self.tool in ("write", "append", "copy"):
            attrs.append(f'bytes="{self.bytes_written}"')
        if self.tool == "bash":
            attrs.append(f'exit_code="{self.exit_code}"')

        attr_str = " ".join(attrs)

        if self.tool in ("write", "append"):
            lines.append(f'<{self.tool} {attr_str}>')
            lines.append(self._clip_text(self.content or "", max_content_chars))
            lines.append(f'</{self.tool}>')
        elif self.tool == "edit":
            lines.append(f'<edit {attr_str}>')
            old_text = self._clip_text(self.old_text or "", max_content_chars)
            new_text = self._clip_text(self.new_text or "", max_content_chars)
            lines.append(f'<old>{old_text}</old>')
            lines.append(f'<new>{new_text}</new>')
            lines.append('</edit>')
        elif self.tool in ("bash", "read"):
            lines.append(f'<{self.tool} {attr_str} />')
            if self.stdout:
                lines.append(f'  stdout: {self._clip_text(self.stdout, max_content_chars)}')
            if self.stderr:
                lines.append(f'  stderr: {self._clip_text(self.stderr, max_content_chars)}')
        elif self.tool in ("mkdir", "touch", "copy"):
            lines.append(f'<{self.tool} {attr_str} />')

        if self.output_summary:
            lines.append(f'  summary: {self.output_summary}')

        if self.status == "failed" and self.error:
            lines.append(f'  ERROR: {self.error}')

        return "\n".join(lines)

    @classmethod
    def from_dict(cls, data: dict) -> "CommandRecord":
        return cls(**data)


class ContextManager:
    """上下文管理器"""

    def __init__(self, start_stamp: int = 1):
        self.records: list[CommandRecord] = []
        self.next_stamp: int = start_stamp
        self._last_failure: Optional[CommandRecord] = None
        self._last_need_result: Optional[CommandRecord] = None
        self._pending_auto_result: Optional[CommandRecord] = None

    @property
    def start_stamp(self) -> int:
        """当前起始戳记（告诉模型从这开始递增）。"""
        return self.next_stamp

    def record(self, command: Command, result: ExecutionResult, flow: str = "delayed", risk: str = "low"):
        """记录一条已执行的命令。"""
        rec = CommandRecord(
            id=command.id,
            tool=command.tool,
            path=command.path,
            dest=command.dest,
            cmd=command.cmd,
            content=command.content,
            old_text=command.old_text,
            new_text=command.new_text,
            need_result=command.need_result,
            flow=flow,
            risk=risk,
            content_hash=_command_hash(command),
            output_summary=_result_summary(command, result),
            created_at=time.time(),
            completed_at=time.time(),
            injected=0,
            status=result.status_str,
            error=result.error,
            bytes_written=result.bytes_written,
            stdout=result.stdout or result.content,
            stderr=result.stderr,
            exit_code=result.exit_code,
        )
        self.records.append(rec)

        # 更新戳记
        try:
            id_int = int(command.id)
            if id_int >= self.next_stamp:
                self.next_stamp = id_int + 1
        except ValueError:
            pass

        # 记录失败和 need_result
        if not result.success:
            self._last_failure = rec
        if command.need_result:
            self._last_need_result = rec
        elif result.success and flow == "blocking":
            self._pending_auto_result = rec

    @property
    def last_failure(self) -> Optional[CommandRecord]:
        return self._last_failure

    @property
    def last_need_result(self) -> Optional[CommandRecord]:
        return self._last_need_result

    @property
    def pending_auto_result(self) -> Optional[CommandRecord]:
        return self._pending_auto_result

    def clear_flags(self):
        """清除一次性标记（每轮 API 调用后调用）。"""
        self._last_failure = None
        self._last_need_result = None
        self._pending_auto_result = None

    def mark_injected(self, record_id: str):
        """把指定命令标记为已注入，避免打断消息和下一轮普通日志重复注入。"""
        for rec in self.records:
            if rec.id == record_id:
                rec.injected = 1
                return

    def build_injection(self) -> Optional[str]:
        """
        构建注入文本。把所有 injected=0 的命令构建为注入块。
        注入后标记为 injected=1。
        """
        pending = [r for r in self.records if r.injected == 0]
        if not pending:
            return None

        lines = []
        lines.append("[THINKFLOW COMMAND LEDGER — 上一轮可审计工具记录]")
        lines.append("以下是上一轮执行过的结构化命令记录。hash 用于对账；summary 用于快速恢复上下文。\n")

        for rec in pending:
            lines.append(rec.to_injection_string())
            lines.append("")  # 空行分隔
            rec.injected = 1

        lines.append("[END COMMAND LEDGER]")

        return "\n".join(lines)

    def build_failure_message(self) -> Optional[str]:
        """构建失败打断消息。"""
        if not self._last_failure:
            return None
        rec = self._last_failure
        return (
            f"[THINKFLOW ERROR — 命令执行失败，思考被打断]\n"
            f"以下命令执行失败：\n\n"
            f'{rec.to_injection_string()}\n\n'
            f"请根据错误信息调整你的后续操作。"
        )

    def build_need_result_message(self) -> Optional[str]:
        """构建 need_result 打断消息。"""
        if not self._last_need_result:
            return None
        rec = self._last_need_result
        lines = [
            f"[THINKFLOW RESULT — 你请求了命令执行结果，思考被打断]",
            f"以下命令的执行结果：\n",
            rec.to_injection_string(),
        ]

        # bash 的详细输出
        if rec.tool in ("bash", "read") and rec.stdout:
            lines.append(f"\n--- stdout ---\n{rec.stdout}")
        if rec.tool in ("bash", "read") and rec.stderr:
            lines.append(f"\n--- stderr ---\n{rec.stderr}")
        return "\n".join(lines)

    def build_auto_result_message(self) -> Optional[str]:
        """Return blocking tool output when a text command omitted need_result."""
        if not self._pending_auto_result:
            return None
        rec = self._pending_auto_result
        lines = [
            "[THINKFLOW RESULT - blocking tool output returned automatically]",
            "The previous turn executed an information-bearing command. Continue the original task using this result; do not stop after reading it.",
            "",
            rec.to_injection_string(),
        ]
        if rec.tool in ("bash", "read") and rec.stdout:
            lines.append(f"\n--- stdout ---\n{rec.stdout}")
        if rec.tool in ("bash", "read") and rec.stderr:
            lines.append(f"\n--- stderr ---\n{rec.stderr}")
        return "\n".join(lines)

    def build_system_prompt_suffix(self) -> str:
        """构建系统提示词的后缀（告知模型起始戳记）。"""
        return f"\n\n当前 ThinkFlow 起始戳记：{self.next_stamp}"

    def reset(self):
        """重置（新会话）。"""
        self.records.clear()
        self.next_stamp = 1
        self._last_failure = None
        self._last_need_result = None
        self._pending_auto_result = None

    def to_dict(self) -> dict:
        """序列化为会话快照。"""
        return {
            "records": [asdict(r) for r in self.records],
            "next_stamp": self.next_stamp,
            "last_failure_id": self._last_failure.id if self._last_failure else None,
            "last_need_result_id": self._last_need_result.id if self._last_need_result else None,
            "pending_auto_result_id": self._pending_auto_result.id if self._pending_auto_result else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ContextManager":
        """从会话快照恢复。"""
        ctx = cls(start_stamp=data.get("next_stamp", 1))
        ctx.records = [
            CommandRecord.from_dict(item)
            for item in data.get("records", [])
        ]
        by_id = {r.id: r for r in ctx.records}
        ctx._last_failure = by_id.get(data.get("last_failure_id"))
        ctx._last_need_result = by_id.get(data.get("last_need_result_id"))
        ctx._pending_auto_result = by_id.get(data.get("pending_auto_result_id"))
        return ctx


def _command_hash(command: Command) -> str:
    parts = [
        command.tool or "",
        command.path or "",
        command.dest or "",
        command.cmd or "",
        command.content or "",
        command.old_text or "",
        command.new_text or "",
    ]
    payload = "\0".join(parts).encode("utf-8", errors="replace")
    return hashlib.sha256(payload).hexdigest()[:16]


def _result_summary(command: Command, result: ExecutionResult) -> str:
    if not result.success:
        return _clip_inline(result.error or "failed")
    if command.tool in ("write", "append"):
        return f"{command.tool} {result.bytes_written} bytes to {command.path}"
    if command.tool == "edit":
        return f"edit applied to {command.path}; {result.bytes_written} bytes after edit"
    if command.tool == "copy":
        return f"copied {command.path} -> {command.dest}; {result.bytes_written} bytes"
    if command.tool in ("mkdir", "touch"):
        return f"{command.tool} {command.path}"
    if command.tool == "bash":
        return f"bash exit_code={result.exit_code} stdout={_clip_inline(result.stdout, 120)} stderr={_clip_inline(result.stderr, 120)}"
    return f"{command.tool} ok"


def _clip_inline(text: str, max_chars: int = 180) -> str:
    text = " ".join((text or "").split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 15] + " ...[clipped]"
