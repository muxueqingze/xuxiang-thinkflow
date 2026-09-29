"""Small, inspectable task plan with explicit execution evidence references."""
from __future__ import annotations

import copy
import re


PLAN_SCHEMA = {
    'type': 'object',
    'properties': {
        'explanation': {'type': 'string', 'description': '当前计划或变更原因，最多2000字符'},
        'steps': {'type': 'array', 'maxItems': 20, 'items': {
            'type': 'object', 'properties': {
                'id': {'type': 'string'}, 'title': {'type': 'string'},
                'acceptance': {'type': 'string', 'description': '可判定的验收条件'},
                'status': {'type': 'string', 'enum': ['pending', 'in_progress', 'completed', 'blocked']},
                'evidence': {'type': 'array', 'items': {'type': 'string'},
                             'description': '成功回执的短引用receipt:N或完整id；completed至少一个，保存时转换为完整id'}},
            'required': ['id', 'title', 'acceptance', 'status', 'evidence'], 'additionalProperties': False}},
    }, 'required': ['explanation', 'steps'], 'additionalProperties': False,
}


def validate_plan(value, *, records=None, previous=None):
    if not isinstance(value, dict) or set(value) != {'explanation', 'steps'}:
        raise ValueError('计划必须包含explanation与steps。')
    if not isinstance(value['explanation'], str) or len(value['explanation']) > 2000:
        raise ValueError('计划说明最多2000字符。')
    value = copy.deepcopy(value)
    steps = value['steps']
    if not isinstance(steps, list) or len(steps) > 20:
        raise ValueError('计划最多20项。')
    seen, active = set(), 0
    succeeded = {record.id for record in records or [] if record.status == 'success'}
    old = {step['id']: step for step in (previous or {}).get('steps', [])}
    aliases, identifiers = {}, {}
    for record in records or []:
        reference = getattr(record, 'evidence_ref', '')
        if reference:
            aliases.setdefault(reference, []).append(record)
        identifiers.setdefault(record.id, []).append(record)

    def invalid_evidence(references):
        choices = []
        for record in reversed(records or []):
            reference = getattr(record, 'evidence_ref', '')
            if (record.status != 'success' or not reference or len(aliases.get(reference, [])) != 1
                    or not 1 <= len(record.id) <= 128):
                continue
            if any(candidate is not record for candidate in identifiers.get(reference, [])):
                continue
            path = ' '.join((getattr(record, 'path', None) or getattr(record, 'dest', None) or '').split())
            path = path if len(path) <= 60 else '…' + path[-59:]
            choices.append(f'{reference}/{record.tool}' + (f'/{path}' if path else ''))
            if len(choices) == 6:
                break
        invalid = ', '.join(dict.fromkeys(references))
        available = ', '.join(reversed(choices)) or '无'
        return ValueError('无效或有歧义的工具回执引用：' + invalid + '。最近成功回执：' + available)

    for step in steps:
        if not isinstance(step, dict) or set(step) != {'id', 'title', 'acceptance', 'status', 'evidence'}:
            raise ValueError('计划步骤字段不完整。')
        if not isinstance(step['id'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', step['id']) or step['id'] in seen:
            raise ValueError('每项步骤须有唯一id。')
        seen.add(step['id'])
        for field, limit in [('title', 200), ('acceptance', 1000)]:
            if not isinstance(step[field], str) or not step[field].strip() or len(step[field]) > limit:
                raise ValueError(f'步骤{field}须为非空文本，最多{limit}字符。')
        if step['status'] not in ('pending', 'in_progress', 'completed', 'blocked'):
            raise ValueError('计划步骤状态无效。')
        active += step['status'] == 'in_progress'
        evidence = step['evidence']
        if not isinstance(evidence, list) or len(evidence) > 20 or any(not isinstance(e, str) or not 1 <= len(e) <= 128 for e in evidence):
            raise ValueError('每项最多20条工具回执引用。')
        if records is not None:
            normalized, invalid = [], []
            unchanged = old.get(step['id']) == step and step['status'] == 'completed'
            for reference in evidence:
                candidates = aliases.get(reference, [])
                if candidates:
                    # Validate the specific receipt before canonicalizing. A failed
                    # alias must not inherit success from another record of the same id.
                    record = candidates[0]
                    if (len(candidates) != 1 or record.status != 'success' or not 1 <= len(record.id) <= 128
                            or any(candidate is not record for candidate in identifiers.get(reference, []))):
                        invalid.append(reference)
                    normalized.append(record.id)
                else:
                    if reference.startswith('receipt:') and reference not in identifiers and not unchanged:
                        invalid.append(reference)
                    normalized.append(reference)
            if invalid:
                raise invalid_evidence(invalid)
            step['evidence'] = evidence = normalized
        if step['status'] == 'completed':
            if not evidence:
                raise ValueError('完成项必须引用成功工具回执，不能只凭口头宣称完成。')
            # Unchanged old steps can retain receipts that have since been compacted.
            if records is not None and old.get(step['id']) != step:
                invalid = [reference for reference in evidence if reference not in succeeded]
                if invalid:
                    raise invalid_evidence(invalid)
    if active > 1:
        raise ValueError('一次最多一个正在执行的步骤。')
    return value
