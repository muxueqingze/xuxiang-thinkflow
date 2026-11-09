"""Attach harness observations to tool results without inventing a user turn."""
from __future__ import annotations
import json


def project_read_results(messages: list[dict], complete_reads: dict[str, int]) -> list[dict]:
    """Reference exact duplicates in the request; keep persisted observations full.

    Sources come only from this retained message list. Rebuilding after compaction
    materializes a full result whenever its earlier source is no longer present.
    """
    sources: dict[str, str] = {}
    projected = []
    for message in messages:
        for call in message.get('tool_calls', []):
            function = call.get('function', {})
            if function.get('name') == 'write':
                arguments = json.loads(function['arguments'])
                content = arguments.get('content')
                if isinstance(content, str):
                    sources.setdefault(content, f'write call {call["id"]}, content argument')
        content = message.get('content')
        identifier = message.get('tool_call_id')
        if message.get('role') == 'tool' and identifier in complete_reads and isinstance(content, str):
            header, separator, body = content.partition('\n')
            if separator and header.startswith('[THINKFLOW RECEIPT ') and 'status="success"' in header:
                length = complete_reads[identifier]
                payload, suffix = body[:length], body[length:]
                source = sources.get(payload)
                if source:
                    reference = f'[Read succeeded. Exact same complete content as {source}; use that retained content.]'
                    if len(reference) < len(payload):
                        message = {**message, 'content': header + '\n' + reference + suffix}
                else:
                    sources[payload] = f'read result {identifier}'
        projected.append(message)
    return projected


def with_observation(messages: list[dict], text: str) -> list[dict]:
    """Return a copy; persisted provider messages remain unchanged."""
    if not text:
        return list(messages)
    if messages:
        last = messages[-1]
        content = last.get("content")
        if last.get("role") in ("tool", "user") and isinstance(content, str):
            return [*messages[:-1], {**last, "content": content + "\n\n" + text}]
        if last.get("role") == "user" and isinstance(content, list):
            # Anthropic tool results have a user role, but are observations,
            # not a new instruction. Preserve IDs and error status verbatim.
            for index in range(len(content) - 1, -1, -1):
                block = content[index]
                if block.get("type") == "tool_result" and isinstance(block.get("content"), str):
                    blocks = list(content)
                    blocks[index] = {**block, "content": block["content"] + "\n\n" + text}
                    return [*messages[:-1], {**last, "content": blocks}]
    return [*messages, {"role": "user", "content": text}]
