"""生产流的已观测用量和请求次数。"""

import json
import time
from dataclasses import asdict, dataclass, field, fields


def _token_count(*values) -> int | None:
    return next((v for v in values if type(v) is int and v >= 0), None)


def _numeric_usage(usage: dict) -> dict:
    counts = {}
    for key, value in usage.items():
        if isinstance(value, dict):
            counts[key] = _numeric_usage(value)
        elif type(value) in (int, float):
            counts[key] = value
    return counts


def _display_tokens(value: int | None, complete: bool) -> str:
    if value is None:
        return "—"
    return f"{'' if complete else '≥ '}{value:,}"


@dataclass
class TurnUsage:
    """一个逻辑轮，可发生多次 HTTP 尝试。"""

    turn: int
    timestamp: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    text_tokens: int = 0
    cached_tokens: int = 0
    cache_miss_tokens: int = 0
    commands_executed: int = 0
    tool_calls_traditional: int = 0
    abort_reason: str = ""
    delayed_successes: int | None = None
    usage_reported: bool = False
    end_time: float | None = None
    http_attempts: int | None = None  # 旧快照缺少 HTTP 次数。
    successful_responses: int = 0
    usage_present: bool = False
    input_reported: bool = False
    output_reported: bool = False
    cache_read_reported: bool = False
    cache_write_tokens: int | None = None
    non_cached_input_tokens: int | None = None
    raw_usage: dict = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def effective_input_tokens(self) -> int | None:
        """非缓存输入，包含已报的缓存写入。"""
        return self.non_cached_input_tokens

    def observe_usage(self, usage: dict | None, *, anthropic: bool = False, final: bool = False) -> None:
        if not isinstance(usage, dict) or not usage:
            return
        self.usage_present = True
        # 事件携带累计值。后续快照覆盖同名分量，保留省略的分量。
        for key, value in _numeric_usage(usage).items():
            if isinstance(value, dict):
                self.raw_usage.setdefault(key, {}).update(value)
            else:
                self.raw_usage[key] = value
        counts = self.raw_usage
        if anthropic:
            prompt = _token_count(counts.get("input_tokens"))
            completion = _token_count(counts.get("output_tokens"))
            final_output = _token_count(usage.get("output_tokens"))
            cached = _token_count(counts.get("cache_read_input_tokens"))
            cache_write = _token_count(counts.get("cache_creation_input_tokens"))
            self.cache_write_tokens = cache_write
            self.non_cached_input_tokens = None if prompt is None else prompt + (cache_write or 0)
            self.prompt_tokens = (prompt or 0) + (cached or 0) + (cache_write or 0)
        else:
            prompt = _token_count(counts.get("prompt_tokens"), counts.get("input_tokens"), counts.get("input"))
            completion = _token_count(counts.get("completion_tokens"), counts.get("output_tokens"), counts.get("output"))
            final_output = _token_count(usage.get("completion_tokens"), usage.get("output_tokens"), usage.get("output"))
            cache_miss = _token_count(counts.get("prompt_cache_miss_tokens"))
            cached = _token_count(
                counts.get("prompt_tokens_details", {}).get("cached_tokens"),
                counts.get("prompt_cache_hit_tokens"),
                counts.get("cache_read_input_tokens"),
            )
            if cached is None and prompt is not None and cache_miss is not None:
                cached = prompt - cache_miss
            self.cache_miss_tokens = cache_miss or 0
            self.cache_write_tokens = _token_count(counts.get("cache_creation_input_tokens"))
            self.non_cached_input_tokens = cache_miss
            if self.non_cached_input_tokens is None and prompt is not None and cached is not None:
                self.non_cached_input_tokens = prompt - cached
            self.prompt_tokens = prompt or 0
        input_complete = prompt is not None
        if anthropic:
            self.input_reported = input_complete or cached is not None or cache_write is not None
            input_complete = input_complete and cached is not None and cache_write is not None
        else:
            self.input_reported = input_complete
        self.output_reported = completion is not None
        self.cache_read_reported = cached is not None
        self.cached_tokens = cached or 0
        self.completion_tokens = completion or 0
        self.reasoning_tokens = _token_count(counts.get("completion_tokens_details", {}).get("reasoning_tokens")) or 0
        self.text_tokens = self.completion_tokens - self.reasoning_tokens
        self.usage_reported = final and input_complete and final_output is not None

    def to_dict(self) -> dict:
        return {**asdict(self), "total_tokens": self.total_tokens}


@dataclass
class RunUsage:
    start_time: float
    end_time: float | None = None
    elapsed: float = 0.0


@dataclass
class SessionUsage:
    """一个会话的累计用量，活跃耗时排除两次 run 之间的等待。"""

    turns: list[TurnUsage] = field(default_factory=list)
    start_time: float = field(default_factory=time.time)
    label: str = ""
    runs: list[RunUsage] = field(default_factory=list)
    _run_clock: float | None = field(default=None, init=False, repr=False)
    _legacy_elapsed: float = field(default=0.0, init=False, repr=False)

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
    def total_effective_input(self) -> int | None:
        values = [t.non_cached_input_tokens for t in self.turns if t.non_cached_input_tokens is not None]
        return sum(values) if values else None

    @property
    def api_calls(self) -> int:
        return sum(t.http_attempts or 0 for t in self.turns)

    @property
    def total_commands(self) -> int:
        return sum(t.commands_executed for t in self.turns)

    @property
    def estimated_saved_api_calls(self) -> int:
        """相对一工具一往返基线估算避免的往返数。"""
        return sum(max(0, t.delayed_successes or 0) for t in self.turns)

    @property
    def estimated_avoided_prompt_tokens(self) -> int:
        if not self.api_calls or not self.estimated_saved_api_calls:
            return 0
        return int(self.total_prompt_tokens / self.api_calls * self.estimated_saved_api_calls)

    @property
    def elapsed(self) -> float:
        if self.runs:
            active = 0.0 if self._run_clock is None else time.perf_counter() - self._run_clock
            return sum(run.elapsed for run in self.runs) + active
        # 直接调用单轮的入口也记录结束时刻。
        durations = [t.end_time - t.timestamp for t in self.turns if t.timestamp and t.end_time is not None]
        return sum(durations) if durations else self._legacy_elapsed

    @property
    def end_time(self) -> float | None:
        if self.runs:
            return self.runs[-1].end_time
        return self.turns[-1].end_time if self.turns else None

    def start_run(self) -> None:
        self.runs.append(RunUsage(start_time=time.time()))
        self._run_clock = time.perf_counter()

    def finish_run(self) -> None:
        run = self.runs[-1]
        run.end_time = time.time()
        run.elapsed = time.perf_counter() - self._run_clock
        self._run_clock = None

    def add_turn(self, usage: TurnUsage):
        self.turns.append(usage)

    def totals(self) -> dict:
        tracked = [t for t in self.turns if t.http_attempts is not None]
        successful = [t for t in tracked if t.successful_responses]
        reported = sum(t.usage_reported for t in successful)
        partial = sum(t.usage_present and not t.usage_reported for t in successful)
        attempts = self.api_calls
        unknown = attempts - reported - partial
        legacy = len(self.turns) - len(tracked)
        cache_writes = [t.cache_write_tokens for t in self.turns if t.cache_write_tokens is not None]
        return {
            "logical_turns": len(self.turns),
            "api_calls": attempts,
            "successful_responses": sum(t.successful_responses for t in tracked),
            "usage_complete": attempts > 0 and not (partial or unknown or legacy),
            "reported_turns": reported,
            "partial_usage_requests": partial,
            "unknown_usage_requests": unknown,
            "legacy_turns": legacy,
            "input_reported": any(t.input_reported or t.prompt_tokens > 0 for t in self.turns),
            "output_reported": any(t.output_reported or t.completion_tokens > 0 for t in self.turns),
            "prompt_tokens": self.total_prompt_tokens,
            "completion_tokens": self.total_completion_tokens,
            "reasoning_tokens": self.total_reasoning_tokens,
            "text_tokens": self.total_text_tokens,
            "cached_tokens": self.total_cached_tokens,
            "cache_read_reported": any(t.cache_read_reported or t.cached_tokens > 0 for t in self.turns),
            "cache_write_tokens": sum(cache_writes) if cache_writes else None,
            "non_cached_input_tokens": self.total_effective_input,
            "effective_input": self.total_effective_input,
            "total_tokens": self.total_tokens,
            "total_commands": self.total_commands,
            "estimated_saved_api_calls": self.estimated_saved_api_calls,
            "estimated_avoided_prompt_tokens": self.estimated_avoided_prompt_tokens,
        }

    def summary(self) -> str:
        totals = self.totals()
        complete = totals["usage_complete"]
        prompt = totals["prompt_tokens"] if totals["input_reported"] else None
        output = totals["completion_tokens"] if totals["output_reported"] else None
        tokens = totals["total_tokens"] if totals["input_reported"] or totals["output_reported"] else None
        legacy = totals["legacy_turns"]
        lines = [
            f"{'='*60}",
            f"Token 用量报告 — {self.label}",
            f"{'='*60}",
            f"逻辑轮: {totals['logical_turns']}  HTTP 尝试: {totals['api_calls']}  成功响应: {totals['successful_responses']}",
            f"用量完整: {totals['reported_turns']}  部分: {totals['partial_usage_requests']}  缺失: {totals['unknown_usage_requests']}",
            f"ThinkFlow 命令: {self.total_commands}  传统 tool_call: {sum(t.tool_calls_traditional for t in self.turns)}",
            f"估算避免往返（一工具一往返）: {self.estimated_saved_api_calls}",
            f"{'活跃耗时' if self.runs or not legacy else '旧记录耗时'}: {self.elapsed:.1f}s",
            "",
            f"输入: {_display_tokens(prompt, complete)}",
            f"  已报缓存读取: {_display_tokens(self.total_cached_tokens if totals['cache_read_reported'] else None, False)}",
            f"  已报缓存写入: {_display_tokens(totals['cache_write_tokens'], False)}",
            f"  已知非缓存输入: {_display_tokens(totals['non_cached_input_tokens'], False)}",
            f"输出: {_display_tokens(output, complete)}",
            f"  已报推理: {_display_tokens(self.total_reasoning_tokens or None, False)}",
            f"总计: {_display_tokens(tokens, complete)}",
        ]
        if legacy:
            lines.append(f"旧记录 {legacy} 轮缺少 HTTP 尝试与用量完整性记录。")
        lines.append(f"{'='*60}")
        return "\n".join(lines)

    def compact_summary(self) -> str:
        totals = self.totals()
        complete = totals["usage_complete"]
        prompt = self.total_prompt_tokens if totals["input_reported"] else None
        output = self.total_completion_tokens if totals["output_reported"] else None
        tokens = self.total_tokens if prompt is not None or output is not None else None
        return (
            f"{self.label:20s} | turns={len(self.turns)} | calls={self.api_calls} | cmds={self.total_commands} | "
            f"in={_display_tokens(prompt, complete)} | out={_display_tokens(output, complete)} | "
            f"total={_display_tokens(tokens, complete)} | {self.elapsed:.1f}s"
        )

    def per_turn_table(self) -> str:
        lines = [
            f"\n{'='*80}",
            f"逐轮明细 — {self.label}",
            f"{'='*80}",
            f"{'轮':>3} {'尝试':>4} {'输入':>8} {'输出':>8} {'命令':>4} {'原因'}",
            f"{'-'*80}",
        ]
        for t in self.turns:
            prompt = t.prompt_tokens if t.input_reported or t.prompt_tokens > 0 else None
            output = t.completion_tokens if t.output_reported or t.completion_tokens > 0 else None
            complete = t.http_attempts == 1 and t.usage_reported
            attempts = "—" if t.http_attempts is None else str(t.http_attempts)
            lines.append(
                f"{t.turn:>3} {attempts:>4} {_display_tokens(prompt, complete):>8} "
                f"{_display_tokens(output, complete):>8} {t.commands_executed:>4} {t.abort_reason}"
            )
        return "\n".join(lines)

    def to_dict(self) -> dict:
        runs = [asdict(run) for run in self.runs]
        if self._run_clock is not None:
            runs[-1]["elapsed"] = time.perf_counter() - self._run_clock
        return {
            "label": self.label,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "elapsed": sum(run["elapsed"] for run in runs) if runs else self.elapsed,
            "legacy_elapsed": self._legacy_elapsed,
            "runs": runs,
            "turns": [t.to_dict() for t in self.turns],
            "totals": self.totals(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SessionUsage":
        usage = cls(
            start_time=float(data.get("start_time") or time.time()),
            label=str(data.get("label", "") or ""),
            runs=[RunUsage(**item) for item in data.get("runs", [])],
        )
        allowed = {item.name for item in fields(TurnUsage)}
        usage.turns = [
            TurnUsage(**{key: value for key, value in item.items() if key in allowed})
            for item in data.get("turns", []) or [] if isinstance(item, dict)
        ]
        for turn in usage.turns:
            if turn.http_attempts is None and turn.usage_reported:
                turn.usage_present = True
                turn.usage_reported = False
        # 历史值只到最后一轮开始；旧版也可能已存入负值。
        legacy_elapsed = data.get("legacy_elapsed", data.get("elapsed", 0))
        usage._legacy_elapsed = max(0.0, float(legacy_elapsed))
        return usage

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)
