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
                             'description': '已成功执行的工具回执id；completed至少一个'}},
            'required': ['id', 'title', 'acceptance', 'status', 'evidence'], 'additionalProperties': False}},
    }, 'required': ['explanation', 'steps'], 'additionalProperties': False,
}


def validate_plan(value, *, records=None, previous=None):
    if not isinstance(value, dict) or set(value) != {'explanation', 'steps'}:
        raise ValueError('计划必须包含explanation与steps。')
    if not isinstance(value['explanation'], str) or len(value['explanation']) > 2000:
        raise ValueError('计划说明最多2000字符。')
    steps = value['steps']
    if not isinstance(steps, list) or len(steps) > 20:
        raise ValueError('计划最多20项。')
    seen, active = set(), 0
    succeeded = {record.id for record in records or [] if record.status == 'success'}
    old = {step['id']: step for step in (previous or {}).get('steps', [])}
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
        if step['status'] == 'completed':
            if not evidence:
                raise ValueError('完成项必须引用成功工具回执，不能只凭口头宣称完成。')
            # Unchanged old steps can retain receipts that have since been compacted.
            if records is not None and old.get(step['id']) != step and any(e not in succeeded for e in evidence):
                available = ', '.join(f'{record.id}:{record.tool}' for record in records[-20:] if record.status == 'success')
                raise ValueError('完成项引用了不存在或未成功的工具回执。最近成功回执：' + available)
    if active > 1:
        raise ValueError('一次最多一个正在执行的步骤。')
    return copy.deepcopy(value)
