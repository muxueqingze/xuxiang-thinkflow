"""
Deterministic context compaction for ThinkFlow.

The compactor never calls a model. It keeps the newest messages verbatim and
turns older history into a bounded, inspectable summary message.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .context_budget import estimate_message_tokens
from .conversation import ConversationContext


COMPACTION_HEADER = "[THINKFLOW COMPACTED CONTEXT]"


@dataclass
class CompactionConfig:
    enabled: bool = True
    max_messages: int = 80
    max_chars: int = 200_000
    keep_recent_messages: int = 30
    max_summary_chars: int = 16_000
    max_item_chars: int = 900
    max_input_tokens: int = 0
    keep_recent_tokens: int = 0

    @classmethod
    def from_config(cls, config: dict) -> "CompactionConfig":
        raw = config.get("compaction", {}) or {}
        return cls(
            enabled=bool(raw.get("enabled", True)),
            max_messages=int(raw.get("max_messages", 80)),
            max_chars=int(raw.get("max_chars", 200_000)),
            keep_recent_messages=int(raw.get("keep_recent_messages", 30)),
            max_summary_chars=int(raw.get("max_summary_chars", 16_000)),
            max_item_chars=int(raw.get("max_item_chars", 900)),
            max_input_tokens=int(raw.get("max_input_tokens", 0)),
            keep_recent_tokens=int(raw.get("keep_recent_tokens", 0)),
        )


@dataclass
class CompactionStats:
    changed: bool
    before_messages: int
    after_messages: int
    before_chars: int
    after_chars: int
    compacted_messages: int = 0
    summary_chars: int = 0


def estimate_message_chars(messages: list[dict[str, Any]]) -> int:
    """Estimate request payload size without counting provider metadata."""
    return sum(len(_stable_json(message)) for message in messages)


def compact_messages(
    messages: list[dict[str, Any]],
    config: CompactionConfig,
    *,
    force: bool = False,
    history: ConversationContext | None = None,
) -> tuple[list[dict[str, Any]], CompactionStats]:
    """Compact messages if limits are exceeded.

    Returns a new message list and stats. The newest messages stay verbatim.
    Older messages become a single user message so both OpenAI-compatible and
    Anthropic-compatible APIs can consume it.
    """
    before_messages = len(messages)
    before_chars = estimate_message_chars(messages)
    stats = CompactionStats(
        changed=False,
        before_messages=before_messages,
        after_messages=before_messages,
        before_chars=before_chars,
        after_chars=before_chars,
    )

    if not messages or (not config.enabled and not force):
        return list(messages), stats

    over_messages = config.max_messages > 0 and before_messages > config.max_messages
    over_chars = config.max_chars > 0 and before_chars > config.max_chars
    if not force and not (over_messages or over_chars):
        return list(messages), stats

    if config.keep_recent_tokens > 0:
        split_at = _split_for_recent_tokens(messages, config.keep_recent_tokens)
    else:
        keep_count = max(1, min(config.keep_recent_messages, before_messages - 1))
        split_at = max(1, before_messages - keep_count)
    split_at = _adjust_split_for_tool_messages(messages, split_at)

    older = messages[:split_at]
    recent = messages[split_at:]
    if not older:
        return list(messages), stats

    history = history if history is not None else ConversationContext()
    entries = list(history.summary_entries)
    count = history.compacted_messages
    active_inputs = history.active_inputs
    active_positions = {position: ref for position, ref in history.request_positions.items()
                        if ref in active_inputs}
    pinned = {position: ref for position, ref in sorted(active_positions.items()) if position < split_at}
    expanded_requests = {entry["ref"] for entry in history.requests[:-1] if "execution_input" in entry}
    for position, message in enumerate(older):
        if position == 0 and history.summary and message.get("content") == history.summary:
            continue
        if position in pinned:
            # with_observation only appends to the admitted input. Keep the user
            # input verbatim and summarize the appended observations separately.
            suffix = message["content"][len(active_inputs[pinned[position]]):].strip()
            if not suffix:
                continue
            message = {"role": "observation", "content": suffix}
        count += 1
        entry = _message_to_summary_line(count, message, config.max_item_chars)
        if position in history.request_positions and position not in active_positions:
            ref = history.request_positions[position]
            if ref in expanded_requests:
                entry = f"{ref} | frozen input: {ref}:input | {entry}"
            else:
                entry = f"{ref} | {entry}"
        entries.append(entry)

    summary, kept_entries = _build_summary(entries, config, count=count, history=history,
                                           has_expanded_requests=bool(expanded_requests))
    prefix = [{"role": "user", "content": summary}]
    prefix.extend({"role": "user", "content": active_inputs[ref]} for ref in pinned.values())
    compacted = prefix + list(recent)
    after_chars = estimate_message_chars(compacted)

    if compacted == messages:
        return list(messages), stats
    history.summary_entries = kept_entries
    history.summary = summary
    history.compacted_messages = count
    positions = {position - split_at + len(prefix): ref
                 for position, ref in history.request_positions.items() if position >= split_at}
    positions.update({index: ref for index, ref in enumerate(pinned.values(), start=1)})
    history.request_positions = positions

    return compacted, CompactionStats(
        changed=True,
        before_messages=before_messages,
        after_messages=len(compacted),
        before_chars=before_chars,
        after_chars=after_chars,
        compacted_messages=len(older),
        summary_chars=len(summary),
    )


def _split_for_recent_tokens(messages: list[dict[str, Any]], budget: int) -> int:
    """Find the longest suffix within budget, always keeping the last message."""
    last = len(messages) - 1
    remaining = budget - estimate_message_tokens(messages[last])
    for position in range(last - 1, -1, -1):
        remaining -= estimate_message_tokens(messages[position])
        if remaining < 0:
            return position + 1
    return 0


def _adjust_split_for_tool_messages(messages: list[dict[str, Any]], split_at: int) -> int:
    """Avoid keeping orphan tool results without their assistant tool call."""
    while split_at > 0 and split_at < len(messages):
        message = messages[split_at]
        content = message.get("content")
        anthropic_results = (message.get("role") == "user" and isinstance(content, list)
                             and any(block.get("type") == "tool_result" for block in content))
        if message.get("role") != "tool" and not anthropic_results:
            break
        split_at -= 1
    return split_at


def _build_summary(entries: list[str], config: CompactionConfig, *, count: int,
                   history: ConversationContext, has_expanded_requests: bool) -> tuple[str, list[str]]:
    lines = [
        COMPACTION_HEADER,
        "Older conversation history was compacted deterministically. No model summarized this text.",
        f"Compacted messages: {count}",
    ]
    if history.requests:
        lines.extend([
            f"Original user requests: request:1 through {history.current_ref}, in chronological order.",
            (f"Active requests {', '.join(history.active_refs)} are retained verbatim below."
             if history.active_refs else f"Current request {history.current_ref} is retained verbatim below."),
            "Use recall(ref) to retrieve earlier requirements or revisions missing from these excerpts.",
        ])
    lines.extend(["", "Recent compacted entries:"])
    input_hint = ("For a request with attachments, use recall(request:N:input) for its frozen input."
                  if has_expanded_requests else "")

    budget = max(500, config.max_summary_chars - len("\n".join(lines)) - len(input_hint) - 80)
    kept: list[str] = []
    total = 0
    omitted = 0
    for entry in reversed(entries):
        entry_len = len(entry) + 1
        if total + entry_len > budget:
            omitted += 1
            continue
        kept.append(entry)
        total += entry_len
    kept.reverse()

    if omitted:
        lines.append(f"... {omitted} older compacted entries omitted ...")
    lines.extend(kept)
    if input_hint:
        lines.append(input_hint)
    lines.append("[END COMPACTED CONTEXT]")

    text = "\n".join(lines)
    if len(text) > config.max_summary_chars:
        tail = text[-config.max_summary_chars + 80 :]
        text = f"{COMPACTION_HEADER}\n... compacted summary clipped ...\n{tail}"
    return text, kept


def _message_to_summary_line(index: int, message: dict[str, Any], max_chars: int) -> str:
    role = message.get("role", "unknown")
    parts = [f"{index}. role={role}"]

    if message.get("tool_calls"):
        calls = []
        for call in message.get("tool_calls", []) or []:
            fn = call.get("function", {}) if isinstance(call, dict) else {}
            calls.append(f"{fn.get('name', '')}:{call.get('id', '')}")
        parts.append(f"tool_calls={', '.join(calls)}")

    if message.get("tool_call_id"):
        parts.append(f"tool_call_id={message.get('tool_call_id')}")

    content = message.get("content")
    if content not in (None, ""):
        parts.append(f"content={_clip(_content_to_text(content), max_chars)}")

    return " | ".join(parts)


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return " ".join(content.split())
    return " ".join(_stable_json(content).split())


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _clip(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    if max_chars <= 40:
        return text[:max_chars]
    head = max_chars // 2
    tail = max_chars - head - 20
    return f"{text[:head]} ...[clipped]... {text[-tail:]}"
