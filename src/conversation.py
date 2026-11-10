"""Original user requests and the compacted view of a conversation.

Only the run entry point records requests. Provider ``user`` messages may also
be tool results or harness observations and are not a source of user intent.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field


@dataclass
class ConversationContext:
    requests: list[dict] = field(default_factory=list)
    request_positions: dict[int, str] = field(default_factory=dict)
    summary_entries: list[str] = field(default_factory=list)
    summary: str = ""
    compacted_messages: int = 0

    def record_request(self, position: int, text: str, execution_input: str) -> str:
        ref = f"request:{len(self.requests) + 1}"
        entry = {"ref": ref, "text": text}
        if execution_input != text:
            entry["execution_input"] = execution_input
        self.requests.append(entry)
        self.request_positions[position] = ref
        return ref

    @property
    def current_ref(self) -> str:
        return self.requests[-1]["ref"] if self.requests else ""

    @property
    def current_input(self) -> str:
        entry = self.requests[-1]
        return entry.get("execution_input", entry["text"])

    def recall(self, ref: str, offset: int = 0, limit: int = 8000) -> str:
        if not isinstance(ref, str) or not re.fullmatch(r"request:[1-9][0-9]*", ref):
            raise ValueError("ref must be request:<number>")
        number = int(ref.split(":", 1)[1])
        if number > len(self.requests):
            raise ValueError(f"Unknown request reference: {ref}")
        if type(offset) is not int or offset < 0:
            raise ValueError("offset must be a non-negative character offset")
        if type(limit) is not int or not 1 <= limit <= 200_000:
            raise ValueError("limit must be between 1 and 200000 characters")
        text = self.requests[number - 1]["text"]
        if offset > len(text):
            raise ValueError(f"offset exceeds request length {len(text)}")
        end = min(len(text), offset + limit)
        header = f"[Original user request {ref}; characters {offset}:{end} of {len(text)}]"
        result = header + "\n" + text[offset:end]
        if end < len(text):
            result += f"\n[More request text: recall ref={ref} offset={end}]"
        return result

    def to_dict(self) -> dict:
        return {
            "requests": copy.deepcopy(self.requests),
            "request_positions": {str(key): value for key, value in self.request_positions.items()},
            "summary_entries": list(self.summary_entries),
            "summary": self.summary,
            "compacted_messages": self.compacted_messages,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ConversationContext":
        return cls(
            requests=copy.deepcopy(data.get("requests", [])),
            request_positions={int(key): value for key, value in data.get("request_positions", {}).items()},
            summary_entries=list(data.get("summary_entries", [])),
            summary=data.get("summary", ""),
            compacted_messages=data.get("compacted_messages", 0),
        )
