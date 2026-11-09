"""Tool-result based observations. Reports never contain reasoning or model text."""
from __future__ import annotations
import json
import hashlib
import re
from pathlib import Path


def test_command(command):
    return bool(re.search(r'\bpython(?:\d[\d.]*)?(?:\.exe)?\s+(?:-[BIu]\s+)*(?:public_tests\.py|test_[\w.]+\.py|-m\s+(?:unittest|pytest)|-c\s+[\s\S]*\bassert\b)|(?:^|[;&|]\s*)pytest\b', command))


def thinkflow_metrics(raw):
    events = raw.get('events', [])
    starts = {e['id']: e for e in events if e['type'] == 'tool_started'}
    results = {r['id']: r for r in raw.get('tool_records', [])}
    completed = [e for e in events if e['type'] == 'tool_completed']
    tests = []
    for event in completed:
        start = starts.get(event['id'], {})
        command = start.get('cmd', '')
        if not command and start.get('input_summary'):
            command = json.loads(start['input_summary']).get('cmd', '')
        record = results.get(event['id'], {})
        if not command:
            command = record.get('cmd') or ''
        check = ((event.get('tool') == 'python' and bool(re.search(r'\bassert\b|\bunittest\b|\bpytest\b|public_tests', command)))
                 or (event.get('tool') in ('bash', 'powershell', 'verify') and test_command(command)))
        if check:
            tests.append({'tool_id': event['id'], 'command': command,
                          'exit_code': record.get('exit_code'),
                          'status': record.get('status'),
                          'stdout_sha256': hashlib.sha256(record.get('stdout', '').encode()).hexdigest(),
                          'stderr_sha256': hashlib.sha256(record.get('stderr', '').encode()).hexdigest(),
                          'verified_revisions': record.get('verified_revisions', {}),
                          'success': event.get('success') is True and record.get('exit_code') == 0
                                     and record.get('status') == 'success'})
    writes = [e for e in completed if e.get('success') and e.get('flow') == 'delayed']
    return {'harness_turns': len({e['turn'] for e in events if e['type'] == 'stream_started'}),
            'tool_calls': len(starts), 'tool_failures': sum(e.get('success') is False for e in completed),
            'active_tests': tests, 'active_test_successes': sum(t['success'] for t in tests),
            'writes_before_stream_finished': sum(any(end['type'] == 'stream_finished' and end['turn'] == e['turn']
                and end['seconds'] > e['seconds'] for end in events) for e in writes),
            'stopped_reason': raw.get('stopped_reason'), 'harness_error': raw.get('last_error')}


def pi_metrics(raw):
    value = thinkflow_metrics(raw)
    records = {record['id']: record for record in raw.get('tool_records', [])}
    for test in value['active_tests']:
        record = records[test['tool_id']]
        output = record['stdout']
        explicit_pass = bool(re.search(r'(?:\bOK\b|\b\d+ passed\b|\btests? passed\b)', output))
        explicit_fail = bool(re.search(r'FAILED|Traceback|AssertionError|\bERROR\b', output))
        test['explicit_pass_output'] = explicit_pass
        test['success'] = record['status'] == 'success' and not explicit_fail and (
            record['exit_code'] == 0 or explicit_pass)
    value['active_test_successes'] = sum(test['success'] for test in value['active_tests'])
    value['tool_results_observed'] = len(records)
    value['harness_error'] = raw.get('last_error') or raw.get('stopped_reason') != 'completed'
    return value


def claude_metrics(path: Path):
    tools, results, assistants = {}, {}, set()
    terminal = None
    initialization = None
    invalid_lines = 0
    for line in path.read_text(encoding='utf8', errors='replace').splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            invalid_lines += 1
            continue
        if event.get('type') == 'result':
            terminal = {k: event[k] for k in ('subtype', 'num_turns', 'is_error', 'duration_api_ms', 'permission_denials',
                        'terminal_reason', 'api_error_status', 'stop_reason', 'subagent_stats', 'safety_stops') if k in event}
        if event.get('type') == 'system' and event.get('subtype') == 'init':
            initialization = {k: event[k] for k in ('tools', 'model', 'apiKeySource', 'claude_code_version', 'plugins') if k in event}
        message = event.get('message', {})
        if event.get('type') == 'assistant':
            if message.get('id') and message.get('model') != '<synthetic>' and not event.get('is_api_error_message'):
                assistants.add(message['id'])
            for block in message.get('content', []):
                if block.get('type') == 'tool_use':
                    tools[block['id']] = {'name': block.get('name'), 'input': block.get('input', {})}
        if event.get('type') == 'user':
            for block in message.get('content', []):
                if block.get('type') == 'tool_result':
                    results[block['tool_use_id']] = {'is_error': block.get('is_error', False),
                        'content': block.get('content'), 'detail': event.get('tool_use_result')}
    tests = []
    tool_failures = 0
    for identifier, result in results.items():
        detail = result['detail'] if isinstance(result['detail'], dict) else {}
        code = detail.get('exitCode', detail.get('exit_code'))
        content = str(result['content']) + '\n' + str(detail.get('stdout', '')) + '\n' + str(detail.get('stderr', ''))
        if code is None:
            match = re.search(r'(?:Exit code|exit code|exit_code)\s*[:=]\s*(-?\d+)', content)
            if match:
                code = int(match[1])
        failed = result['is_error'] or (code is not None and code != 0)
        tool_failures += int(failed)
        tool = tools.get(identifier, {})
        command = tool.get('input', {}).get('command', '')
        if tool.get('name') in ('Bash', 'PowerShell') and test_command(command):
            # A successful tool_result alone is not proof a test ran successfully.
            explicit_pass = bool(re.search(r'(?:\bOK\b|\b\d+ passed\b|\btests? passed\b)', content))
            explicit_fail = bool(re.search(r'FAILED|Traceback|AssertionError|\bERROR\b', content))
            success = not failed and not explicit_fail and (code == 0 or explicit_pass)
            tests.append({'tool_id': identifier, 'command': command, 'exit_code': code,
                          'explicit_pass_output': explicit_pass, 'success': success})
    return {'harness_turns': len(assistants), 'tool_calls': len(tools), 'tool_failures': tool_failures,
            'tool_results_observed': len(results), 'unresolved_tool_calls': len(set(tools) - set(results)),
            'active_tests': tests, 'active_test_successes': sum(t['success'] for t in tests),
            'writes_before_stream_finished': 0, 'terminal_result': terminal,
            'initialization': initialization,
            'stream_parse_invalid_lines': invalid_lines,
            'harness_error': terminal is None or terminal.get('is_error', False),
            'measurement_note': 'Claude assistant message ids count observed turns; result.num_turns is retained separately. '
                                'Native tools execute after tool-call stream; no ThinkFlow-style streaming writes.'}
