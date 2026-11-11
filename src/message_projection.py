"""Project the two persisted provider wire formats into the current request."""
from __future__ import annotations

import json


def project_messages(messages: list[dict], target_format: str) -> list[dict]:
    """Keep history intact, including reasoning which only its provider can replay.

    This covers the text and native tool messages emitted by AgentLoop. Native
    messages keep their fields verbatim; only foreign tool/reasoning forms change.
    """
    projected: list[dict] = []
    for original in messages:
        message = ({key: value for key, value in original.items() if key != '_thinkflow_reads'}
                   if '_thinkflow_reads' in original else original)
        content = message.get('content')
        if isinstance(content, list) and any('_thinkflow_reads' in block for block in content):
            content = [{key: value for key, value in block.items() if key != '_thinkflow_reads'}
                       for block in content]
            message = {**message, 'content': content}
        if target_format == 'anthropic':
            if message.get('role') == 'tool':
                block = {'type': 'tool_result', 'tool_use_id': message['tool_call_id'],
                         'content': content}
                # Native ThinkFlow results carry their actual status in the first
                # receipt line; retain the entire error body as well as its flag.
                header = content.partition('\n')[0]
                if header.startswith('[THINKFLOW RECEIPT '):
                    block['is_error'] = 'status="success"' not in header
                if projected and projected[-1].get('role') == 'user' and isinstance(projected[-1].get('content'), list) and all(
                        item.get('type') == 'tool_result' for item in projected[-1]['content']):
                    projected[-1] = {**projected[-1], 'content': [*projected[-1]['content'], block]}
                else:
                    projected.append({'role': 'user', 'content': [block]})
                continue
            if message.get('tool_calls'):
                blocks = [{'type': 'text', 'text': content}] if isinstance(content, str) and content else []
                if isinstance(content, list):
                    blocks.extend(content)
                blocks.extend({'type': 'tool_use', 'id': call['id'],
                               'name': call['function']['name'],
                               'input': json.loads(call['function']['arguments'])}
                              for call in message['tool_calls'])
                message = {'role': 'assistant', 'content': blocks}
            elif 'reasoning_content' in message:
                message = {key: value for key, value in message.items() if key != 'reasoning_content'}
                if not content:
                    continue
            projected.append(message)
            continue

        if not isinstance(content, list) or not any(
                block.get('type') in ('tool_use', 'tool_result', 'thinking', 'redacted_thinking')
                for block in content):
            projected.append(message)
            continue
        text_blocks = [block for block in content if block.get('type') == 'text']
        if message['role'] == 'assistant':
            calls = [{'id': block['id'], 'type': 'function', 'function': {
                'name': block['name'], 'arguments': json.dumps(block['input'], ensure_ascii=False)}}
                for block in content if block.get('type') == 'tool_use']
            if text_blocks or calls:
                converted = {'role': 'assistant', 'content': text_blocks or None}
                if calls:
                    converted['tool_calls'] = calls
                projected.append(converted)
        else:
            projected.extend({'role': 'tool', 'tool_call_id': block['tool_use_id'],
                              'content': block['content']}
                             for block in content if block.get('type') == 'tool_result')
            if text_blocks:
                projected.append({'role': message['role'], 'content': text_blocks})
    return projected
