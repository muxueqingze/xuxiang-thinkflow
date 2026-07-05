"""
ThinkFlow Usage Tracker — Token 用量统计

跟踪每次 API 调用的 token 消耗，支持多轮对比。
"""

import json
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TurnUsage:
    """单轮 API 调用的 token 用量"""
    turn: int                          # 第几轮
    timestamp: float = 0.0
    # DeepSeek / OpenAI 格式
    prompt_tokens: int = 0             # 输入 token（含系统提示+历史+注入）
    completion_tokens: int = 0         # 输出 token（thinking + 正文）
    reasoning_tokens: int = 0          # thinking token
    text_tokens: int = 0               # 正文输出 token (completion - reasoning)
    cached_tokens: int = 0             # 缓存命中 token
    cache_miss_tokens: int = 0         # 缓存未命中 token
    # 附加信息
    commands_executed: int = 0         # 本轮执行的 ThinkFlow 命令数
    tool_calls_traditional: int = 0    # 本轮传统 tool_call 数
    abort_reason: str = ""

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def effective_input_tokens(self) -> int:
        """实际需要计费的输入 token（不含缓存命中）"""
        return self.prompt_tokens - self.cached_tokens

    def to_dict(self) -> dict:
        return {
            "turn": self.turn,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "text_tokens": self.text_tokens,
            "cached_tokens": self.cached_tokens,
            "cache_miss_tokens": self.cache_miss_tokens,
            "total_tokens": self.total_tokens,
            "commands_executed": self.commands_executed,
            "tool_calls_traditional": self.tool_calls_traditional,
            "abort_reason": self.abort_reason,
        }


@dataclass
class SessionUsage:
    """整次会话的累计用量"""
    turns: list[TurnUsage] = field(default_factory=list)
    start_time: float = field(default_factory=time.time)
    label: str = ""                    # 标签（如 "thinkflow" / "baseline"）

    @property
    def total_prompt_tokens(self) -> int:
        return sum(t.prompt_tokens for t in self.turns)

    @property
    def total_completion_tokens(self) -> int:
        return sum(t.completion_tokens for t in self.turns)

    @property
    def total_reasoning_tokens(self) -> int:
        return sum(t.reasoning_tokens for t in self.turns)

    @property
    def total_text_tokens(self) -> int:
        return sum(t.text_tokens for t in self.turns)

    @property
    def total_tokens(self) -> int:
        return self.total_prompt_tokens + self.total_completion_tokens

    @property
    def total_cached_tokens(self) -> int:
        return sum(t.cached_tokens for t in self.turns)

    @property
    def total_effective_input(self) -> int:
        """实际计费输入（不含缓存命中）"""
        return sum(t.effective_input_tokens for t in self.turns)

    @property
    def api_calls(self) -> int:
        return len(self.turns)

    @property
    def total_commands(self) -> int:
        return sum(t.commands_executed for t in self.turns)

    @property
    def estimated_saved_api_calls(self) -> int:
        """Conservative estimate: deterministic commands that did not need a follow-up turn."""
        interrupted = sum(
            1
            for t in self.turns
            if t.abort_reason in ("need_result", "tool_failed")
        )
        return max(0, self.total_commands - interrupted)

    @property
    def estimated_avoided_prompt_tokens(self) -> int:
        if not self.api_calls or not self.estimated_saved_api_calls:
            return 0
        avg_prompt = self.total_prompt_tokens / self.api_calls
        return int(avg_prompt * self.estimated_saved_api_calls)

    @property
    def elapsed(self) -> float:
        if not self.turns:
            return 0
        return self.turns[-1].timestamp - self.start_time

    def add_turn(self, usage: TurnUsage):
        self.turns.append(usage)

    def summary(self) -> str:
        """生成可读的用量摘要。"""
        lines = [
            f"{'='*60}",
            f"Token 用量报告 — {self.label}",
            f"{'='*60}",
            f"API 调用次数:     {self.api_calls}",
            f"ThinkFlow 命令:   {self.total_commands}",
            f"传统 tool_call:   {sum(t.tool_calls_traditional for t in self.turns)}",
            f"估算避免 API 往返: {self.estimated_saved_api_calls}",
            f"估算少重发输入:   {self.estimated_avoided_prompt_tokens:>10,} tokens",
            f"耗时:             {self.elapsed:.1f}s",
            f"",
            f"--- Token 分解 ---",
            f"输入 (prompt):        {self.total_prompt_tokens:>10,}",
            f"  其中缓存命中:       {self.total_cached_tokens:>10,}",
            f"  实际计费输入:       {self.total_effective_input:>10,}",
            f"输出 (completion):    {self.total_completion_tokens:>10,}",
            f"  其中 thinking:      {self.total_reasoning_tokens:>10,}",
            f"  其中正文:           {self.total_text_tokens:>10,}",
            f"总计:                 {self.total_tokens:>10,}",
            f"{'='*60}",
        ]
        return "\n".join(lines)

    def compact_summary(self) -> str:
        """单行摘要，用于对比。"""
        return (
            f"{self.label:20s} | "
            f"calls={self.api_calls:3d} | "
            f"cmds={self.total_commands:3d} | "
            f"in={self.total_prompt_tokens:>8,} | "
            f"out={self.total_completion_tokens:>8,} | "
            f"think={self.total_reasoning_tokens:>8,} | "
            f"cache={self.total_cached_tokens:>8,} | "
            f"total={self.total_tokens:>9,} | "
            f"{self.elapsed:.1f}s"
        )

    def per_turn_table(self) -> str:
        """每轮详细表格。"""
        lines = [
            f"\n{'='*80}",
            f"逐轮明细 — {self.label}",
            f"{'='*80}",
            f"{'轮':>3} {'输入':>8} {'输出':>8} {'think':>8} {'正文':>8} {'缓存':>8} {'命令':>4} {'原因'}",
            f"{'-'*80}",
        ]
        for t in self.turns:
            lines.append(
                f"{t.turn:>3} "
                f"{t.prompt_tokens:>8,} "
                f"{t.completion_tokens:>8,} "
                f"{t.reasoning_tokens:>8,} "
                f"{t.text_tokens:>8,} "
                f"{t.cached_tokens:>8,} "
                f"{t.commands_executed:>4} "
                f"{t.abort_reason}"
            )
        lines.append(f"{'-'*80}")
        lines.append(
            f"{'Σ':>3} "
            f"{self.total_prompt_tokens:>8,} "
            f"{self.total_completion_tokens:>8,} "
            f"{self.total_reasoning_tokens:>8,} "
            f"{self.total_text_tokens:>8,} "
            f"{self.total_cached_tokens:>8,} "
            f"{self.total_commands:>4}"
        )
        return "\n".join(lines)



    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "start_time": self.start_time,
            "elapsed": self.elapsed,
            "turns": [t.to_dict() for t in self.turns],
            "totals": {
                "prompt_tokens": self.total_prompt_tokens,
                "completion_tokens": self.total_completion_tokens,
                "reasoning_tokens": self.total_reasoning_tokens,
                "text_tokens": self.total_text_tokens,
                "cached_tokens": self.total_cached_tokens,
                "effective_input": self.total_effective_input,
                "total_tokens": self.total_tokens,
                "api_calls": self.api_calls,
                "total_commands": self.total_commands,
                "estimated_saved_api_calls": self.estimated_saved_api_calls,
                "estimated_avoided_prompt_tokens": self.estimated_avoided_prompt_tokens,
            },
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SessionUsage":
        usage = cls(
            start_time=float(data.get("start_time", time.time()) or time.time()),
            label=str(data.get("label", "") or ""),
        )
        for item in data.get("turns", []) or []:
            if not isinstance(item, dict):
                continue
            usage.turns.append(TurnUsage(
                turn=int(item.get("turn", 0) or 0),
                timestamp=float(item.get("timestamp", 0.0) or 0.0),
                prompt_tokens=int(item.get("prompt_tokens", 0) or 0),
                completion_tokens=int(item.get("completion_tokens", 0) or 0),
                reasoning_tokens=int(item.get("reasoning_tokens", 0) or 0),
                text_tokens=int(item.get("text_tokens", 0) or 0),
                cached_tokens=int(item.get("cached_tokens", 0) or 0),
                cache_miss_tokens=int(item.get("cache_miss_tokens", 0) or 0),
                commands_executed=int(item.get("commands_executed", 0) or 0),
                tool_calls_traditional=int(item.get("tool_calls_traditional", 0) or 0),
                abort_reason=str(item.get("abort_reason", "") or ""),
            ))
        return usage

    def to_json(self) -> str:
        """序列化为 JSON。"""
        return json.dumps({
            "label": self.label,
            "start_time": self.start_time,
            "elapsed": self.elapsed,
            "turns": [t.to_dict() for t in self.turns],
            "totals": {
                "prompt_tokens": self.total_prompt_tokens,
                "completion_tokens": self.total_completion_tokens,
                "reasoning_tokens": self.total_reasoning_tokens,
                "text_tokens": self.total_text_tokens,
                "cached_tokens": self.total_cached_tokens,
                "effective_input": self.total_effective_input,
                "total_tokens": self.total_tokens,
                "api_calls": self.api_calls,
                "total_commands": self.total_commands,
                "estimated_saved_api_calls": self.estimated_saved_api_calls,
                "estimated_avoided_prompt_tokens": self.estimated_avoided_prompt_tokens,
            },
        }, indent=2, ensure_ascii=False)


def parse_usage_from_data(data: dict, turn: int) -> Optional[TurnUsage]:
    """从 API 响应数据中提取 usage。"""
    usage_data = data.get("usage")
    if not usage_data or usage_data is None:
        return None

    # 非字典可能是 null
    if not isinstance(usage_data, dict):
        return None

    prompt = (
        usage_data.get("prompt_tokens")
        or usage_data.get("input_tokens")
        or usage_data.get("input")
        or 0
    )
    completion = (
        usage_data.get("completion_tokens")
        or usage_data.get("output_tokens")
        or usage_data.get("output")
        or 0
    )

    # reasoning tokens
    comp_details = usage_data.get("completion_tokens_details", {}) or {}
    reasoning = comp_details.get("reasoning_tokens", 0) or 0
    text = completion - reasoning

    # cached tokens
    prompt_details = usage_data.get("prompt_tokens_details", {}) or {}
    cached = prompt_details.get("cached_tokens", 0) or 0
    cache_miss = usage_data.get("prompt_cache_miss_tokens", 0) or 0

    # 如果没有 prompt_tokens_details，尝试顶层
    if not cached:
        cached = usage_data.get("prompt_cache_hit_tokens", 0) or 0
    if not cached:
        cached = usage_data.get("cache_read_input_tokens", 0) or 0

    return TurnUsage(
        turn=turn,
        timestamp=time.time(),
        prompt_tokens=prompt,
        completion_tokens=completion,
        reasoning_tokens=reasoning,
        text_tokens=text,
        cached_tokens=cached,
        cache_miss_tokens=cache_miss,
    )
