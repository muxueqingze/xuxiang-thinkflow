"""Original user requests and the compacted view of a conversation.

Run input and admitted steering record requests. Provider ``user`` messages may also
be tool results or harness observations and are not a source of user intent.
"""
from __future__ import annotations

import copy
import io
import re
from dataclasses import dataclass, field

from .recall import recall_text


@dataclass
class ConversationContext:
    requests: list[dict] = field(default_factory=list)
    request_positions: dict[int, str] = field(default_factory=dict)
    summary_entries: list[str] = field(default_factory=list)
    summary: str = ""
    compacted_messages: int = 0
    active_refs: list[str] = field(default_factory=list)

    def record_request(self, position: int, text: str, execution_input: str, *,
                       continuation: bool = False, attachment_paths=()) -> str:
        ref = f"request:{len(self.requests) + 1}"
        if continuation:
            self.active_refs = [*(self.active_refs or [self.current_ref]), ref]
        else:
            self.active_refs = []
        entry = {"ref": ref, "text": text}
        if execution_input != text:
            entry["execution_input"] = execution_input
            if attachment_paths:
                entry["attachment_paths"] = list(attachment_paths)
        self.requests.append(entry)
        self.request_positions[position] = ref
        return ref

    def finish_attachments(self):
        for ref in self.active_refs or [self.current_ref]:
            entry = self.requests[int(ref.split(':')[1]) - 1]
            if entry.get('attachment_paths'):
                entry['attachments_delivered'] = True

    def project_attachments(self, messages: list[dict], threshold: int) -> list[dict]:
        """Reference large frozen files after their task has delivered."""
        if not threshold:
            return messages
        result = messages.copy()
        active = self.active_refs or [self.current_ref]
        for index, ref in self.request_positions.items():
            entry = self.requests[int(ref.split(':')[1]) - 1]
            if ref in active or not entry.get('attachments_delivered'):
                continue
            execution, original = entry['execution_input'], entry['text']
            message = messages[index]
            if (len(execution) - len(original) <= threshold or not execution.startswith(original)
                    or not message['content'].startswith(execution)):
                continue
            paths = ', '.join(entry['attachment_paths'])
            view = f"{original}\n[Frozen files: {paths}. Full input: {ref}:input (recall or tf-python ref).]"
            updated = {**message, 'content': view + message['content'][len(execution):]}
            if '_thinkflow_reads' in message:
                shift = len(view) - len(execution)
                updated['_thinkflow_reads'] = [{**s, 'start': s['start'] + shift, 'end': s['end'] + shift}
                                               for s in message['_thinkflow_reads']]
            result[index] = updated
        return result

    @property
    def current_ref(self) -> str:
        return self.requests[-1]["ref"] if self.requests else ""

    @property
    def current_input(self) -> str:
        entry = self.requests[-1]
        return entry.get("execution_input", entry["text"])

    @property
    def active_inputs(self) -> dict[str, str]:
        refs = self.active_refs or ([self.current_ref] if self.requests else [])
        entries = (self.requests[int(ref.split(':')[1]) - 1] for ref in refs)
        return {entry['ref']: entry.get('execution_input', entry['text']) for entry in entries}

    def original_text(self, ref: str) -> str:
        match = re.fullmatch(r"request:([1-9][0-9]*)(:input)?", ref) if isinstance(ref, str) else None
        if not match:
            raise ValueError("ref must be request:<number> or request:<number>:input")
        number = int(match[1])
        if number > len(self.requests):
            raise ValueError(f"Unknown request reference: {ref}")
        entry = self.requests[number - 1]
        if match[2]:
            if "execution_input" not in entry:
                raise ValueError(f"No separate execution input for {ref}")
            return entry["execution_input"]
        return entry["text"]

    def recall(self, ref: str, offset: int = 0, limit: int = 8000, query=None) -> str:
        text = self.original_text(ref)
        kind = "execution input" if ref.endswith(":input") else "request"
        with io.StringIO(text, newline="") as source:
            return recall_text(source, len(text), ref, kind, offset, limit, query)

    def to_dict(self) -> dict:
        return {
            "requests": copy.deepcopy(self.requests),
            "request_positions": {str(key): value for key, value in self.request_positions.items()},
            "summary_entries": list(self.summary_entries),
            "summary": self.summary,
            "compacted_messages": self.compacted_messages,
            **({"active_refs": list(self.active_refs)} if self.active_refs else {}),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ConversationContext":
        return cls(
            requests=copy.deepcopy(data.get("requests", [])),
            request_positions={int(key): value for key, value in data.get("request_positions", {}).items()},
            summary_entries=list(data.get("summary_entries", [])),
            summary=data.get("summary", ""),
            compacted_messages=data.get("compacted_messages", 0),
            active_refs=list(data.get('active_refs', [])),
        )
