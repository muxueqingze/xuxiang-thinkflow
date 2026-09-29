"""Benchmark-only metadata monitor; never persists model text or reasoning bodies."""
from __future__ import annotations

import json
import os
from pathlib import Path
import time


KEY_EVENTS = {'turn_started', 'stream_started', 'stream_finished', 'tool_started',
              'tool_completed', 'turn_finished', 'run_finished', 'error', 'request_usage'}
SAFE_FIELDS = ('seq', 'turn', 'id', 'tool', 'channel', 'flow', 'risk', 'path', 'dest',
               'need_result', 'input_hash', 'cmd', 'content_bytes', 'input_summary',
               'status', 'success', 'error', 'bytes_written', 'change_id', 'reason',
               'finish_reason', 'cancelled', 'stopped_reason', 'failed', 'turns')


class Monitor:
    def __init__(self, directory, snapshot, *, clock=time.monotonic):
        self.directory = Path(directory)
        self.snapshot = snapshot
        self.clock = clock
        self.started = clock()
        self.last_write = float('-inf')
        self.disabled = False
        self.state = {'turn': 0, 'phase': 'starting', 'last_tool': None,
                      'reasoning_chars': 0, 'text_chars': 0}
        self.turn_activity = {}
        self.tool_inputs = {}
        self.reported_turns = set()
        self.trace = (self.directory / 'trace.jsonl').open('a', encoding='utf-8')
        try:
            self.flush()
        except BaseException:
            self.trace.close()
            raise

    def observe(self, event):
        if self.disabled:
            return
        kind, turn = event['type'], event.get('turn', self.state['turn'])
        self.state['turn'] = turn
        if kind in ('reasoning_activity', 'text_delta'):
            # Only counts enter monitor memory; strings never enter persistence.
            field = 'reasoning_chars' if kind == 'reasoning_activity' else 'text_chars'
            count = max(0, int(event.get('chars', 0))) if kind == 'reasoning_activity' else len(event.get('text', ''))
            self.state[field] += count
            activity = self.turn_activity.setdefault(turn, {'reasoning_chars': 0, 'text_chars': 0})
            activity[field] += count
            self.state['phase'] = 'reasoning' if kind == 'reasoning_activity' else 'generating'
            return
        if kind not in KEY_EVENTS:
            return
        record = {'type': kind, 'seconds': round(self.clock() - self.started, 4),
                  **{key: event[key] for key in SAFE_FIELDS if key in event}}
        for key in ('cmd', 'input_summary', 'error'):
            if key in record:
                record[key] = str(record[key])[:2000]
        if kind == 'tool_started':
            self.tool_inputs[event['id']] = {key: record[key] for key in
                ('cmd', 'input_summary', 'path', 'dest', 'input_hash') if key in record}
        elif kind == 'tool_completed':
            record = {**self.tool_inputs.pop(event['id'], {}), **record}
        phases = {'turn_started': 'preparing', 'stream_started': 'streaming',
                  'stream_finished': 'tool_feedback', 'tool_started': 'tool_running',
                  'tool_completed': 'tool_finished', 'turn_finished': 'turn_finished',
                  'run_finished': 'finished', 'error': 'error'}
        self.state['phase'] = phases.get(kind, self.state['phase'])
        if kind.startswith('tool_'):
            self.state['last_tool'] = {key: record[key] for key in
                ('id', 'tool', 'cmd', 'path', 'input_hash', 'status', 'error') if key in record}
        metadata = self.snapshot()
        record['context_chars'] = metadata.get('context_chars', 0)
        record['message_count'] = metadata.get('message_count', 0)
        self._append(record)
        for usage in metadata.get('turn_usage', []) if kind in ('turn_finished', 'run_finished') else []:
            if usage.get('usage_reported') and usage['turn'] not in self.reported_turns:
                self.reported_turns.add(usage['turn'])
                self._append({'type': 'request_usage', 'seconds': record['seconds'],
                              'turn': usage['turn'], 'usage': usage,
                              **self.turn_activity.get(usage['turn'], {})})

    def _append(self, value):
        self.trace.write(json.dumps(value, ensure_ascii=False) + '\n')
        self.trace.flush()

    def flush(self, *, final=False):
        if self.disabled or (not final and self.clock() - self.last_write < 1):
            return
        metadata = self.snapshot()
        value = {**self.state, **metadata, 'seconds': round(self.clock() - self.started, 4),
                 'updated_at': time.time()}
        temporary = self.directory / 'live.json.tmp'
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        os.replace(temporary, self.directory / 'live.json')
        self.last_write = self.clock()

    def close(self):
        # One final atomic snapshot is exempt from the streaming write throttle.
        try:
            self.state['phase'] = 'finished'
            self.flush(final=True)
        finally:
            self.trace.close()


def meter_progress(meter):
    """Read only public request metadata under the existing meter lock."""
    with meter.lock:
        completed, pending, total = 0, 0, 0
        for request in meter.requests:
            usage = request.get('usage') or {}
            valid = all(type(usage.get(key)) is int and usage[key] >= 0
                        for key in ('prompt_tokens', 'completion_tokens'))
            if request.get('finished') and valid:
                completed += 1
                total += usage['prompt_tokens'] + usage['completion_tokens']
            else:
                pending += 1
        return {'completed_requests': completed, 'completed_tokens': total,
                'pending_usage_requests': pending}


def print_progress(run_id, directory, meter):
    try:
        state = json.loads((Path(directory) / 'live.json').read_text(encoding='utf-8'))
        fields = {key: state.get(key) for key in ('turn', 'phase', 'last_tool', 'reasoning_chars')}
        if isinstance(fields.get('last_tool'), dict):
            fields['last_tool'] = {key: str(value)[:160] for key, value in fields['last_tool'].items()
                                  if key in ('tool', 'cmd', 'path', 'status', 'error')}
    except (OSError, ValueError):
        fields = {'phase': 'waiting_for_worker'}
    print(json.dumps({'progress': run_id, **fields, **meter_progress(meter)}, ensure_ascii=False), flush=True)
