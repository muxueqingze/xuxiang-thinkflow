"""Attach harness observations to tool results without inventing a user turn."""
from __future__ import annotations
import json


def project_read_results(messages: list[dict], complete_reads: dict[str, int]) -> list[dict]:
    """Reference exact duplicates in the request; keep persisted observations full.

    Sources come only from this retained message list. Rebuilding after compaction
    materializes a full result whenever its earlier source is no longer present.
    """
    sources: dict[str, str] = {}
    retained_text: list[str] = []

    def project_text(content, spans, identifier=None):
        header, separator, _ = content.partition('\n')
        reads = list(spans)
        if (identifier in complete_reads and separator
                and header.startswith('[THINKFLOW RECEIPT ') and 'status="success"' in header):
            start = len(header) + 1
            reads.insert(0, {'id': identifier, 'start': start,
                             'end': start + complete_reads[identifier]})
        chunks = []
        cursor = 0
        for span in reads:
            start, end = span['start'], span['end']
            chunks.append(content[cursor:start])
            payload = content[start:end]
            source = sources.get(payload)
            if not source and payload and (any(payload in text for text in retained_text)
                                           or payload in ''.join(chunks)):
                source = 'earlier retained message text'
            reference = (f'[Read succeeded. Exact same complete content as {source}; '
                         'use that retained content.]')
            if (span['id'] in complete_reads and len(payload) == complete_reads[span['id']]
                    and source and len(reference) < len(payload)):
                chunks.append(reference)
            else:
                chunks.append(payload)
                if span['id'] in complete_reads:
                    sources.setdefault(payload, f'read result {span["id"]}')
            cursor = end
        chunks.append(content[cursor:])
        text = ''.join(chunks)
        retained_text.append(text)
        return text

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
        if isinstance(content, str):
            text = project_text(content, message.get('_thinkflow_reads', ()),
                                message.get('tool_call_id') if message.get('role') == 'tool' else None)
            if text != content:
                message = {**message, 'content': text}
        elif isinstance(content, list):
            blocks = []
            for block in content:
                if block.get('type') == 'tool_use' and block.get('name') == 'write':
                    payload = block['input'].get('content')
                    if isinstance(payload, str):
                        sources.setdefault(payload, f'write call {block["id"]}, content argument')
                elif block.get('type') == 'tool_result' and isinstance(block.get('content'), str):
                    text = project_text(block['content'], block.get('_thinkflow_reads', ()),
                                        None if block.get('is_error') else block['tool_use_id'])
                    if text != block['content']:
                        block = {**block, 'content': text}
                elif block.get('type') == 'text':
                    retained_text.append(block['text'])
                blocks.append(block)
            if any(before is not after for before, after in zip(content, blocks)):
                message = {**message, 'content': blocks}
        projected.append(message)
    return projected


def with_observation(messages: list[dict], text: str, *, read_spans=()) -> list[dict]:
    """Return a copy; persisted provider messages remain unchanged."""
    if not text:
        return list(messages)

    def attach(target, prefix=''):
        updated = {**target, 'content': prefix + text}
        if read_spans:
            updated['_thinkflow_reads'] = [*target.get('_thinkflow_reads', ()),
                *({**span, 'start': span['start'] + len(prefix), 'end': span['end'] + len(prefix)}
                  for span in read_spans)]
        return updated

    if messages:
        last = messages[-1]
        content = last.get("content")
        if last.get("role") in ("tool", "user") and isinstance(content, str):
            return [*messages[:-1], attach(last, content + '\n\n')]
        if last.get("role") == "user" and isinstance(content, list):
            # Anthropic tool results have a user role, but are observations,
            # not a new instruction. Preserve IDs and error status verbatim.
            for index in range(len(content) - 1, -1, -1):
                block = content[index]
                if block.get("type") == "tool_result" and isinstance(block.get("content"), str):
                    blocks = list(content)
                    blocks[index] = attach(block, block['content'] + '\n\n')
                    return [*messages[:-1], {**last, "content": blocks}]
    return [*messages, attach({'role': 'user'})]
