"""Read saved tool receipts only; never execute a harness, solution, or test.

This is a conservative process-evidence audit, separate from hidden grading.
Unrecognized commands/output are unknown, never inferred functional failures.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
TEST = re.compile(r'(?:python(?:3|\.exe)?\s+(?:-B\s+)?(?:-m\s+(?:unittest|pytest)\b|[^\s;&|]*test[^\s;&|]*\.py\b)|\bpytest\b)', re.I)
SUMMARY = re.compile(r'Ran\s+(\d+)\s+tests?\s+in\s+[^\r\n]+\s+\n\s*(OK(?:\s*\([^\n]*\))?|FAILED\s*\([^\n]*\))', re.I)
FAILURE = re.compile(r'^\s*(?:FAILED\s*\(|FAIL:|ERROR:|[\w.]+Error:)|\b\d+\s+failed\b', re.M)
SHELL_WRITE = re.compile(r'\b(?:Set-Content|Add-Content|Out-File|write_text|write_bytes|open\([^\n]+["\'](?:w|a)|sed\s+-i|tee\b|cat\s*>|apply_patch)\b', re.I)
MUTATORS = {'write', 'edit', 'apply_patch', 'move', 'delete', 'create_file', 'replace'}
RECOVERY_SAMPLE = 'repair_routes-thinkflow-r2'
# These two scripts were manually inspected in the saved production workspaces.
# They assert router/CLI behavior and print these summaries only at completion.
AD_HOC_TEST = re.compile(r'python\s+(\.thinkflow[/\\]+(?:contract_matrix|verify_cli_edges)\.py)\b', re.I)
MANUAL_AUDIT = {
    'repair_routes-thinkflow-r2': 'Verification helper, not production source: router.py last changed at events[21]; public 1/OK at events[51], custom 26/OK at events[53]; final contract_matrix script at events[88] saved 92 checks passed. Helper assertions and final print inspected offline.',
    'receipt_package-thinkflow-r2': 'Verification helper, not production source: receipt_tools last changed at events[25]; public 1/OK at events[30], custom 35/OK at events[47] and discover at events[112]; final helper at events[122] saved 13/13 CLI edge checks passed. Helper checks and exit handling inspected offline.',
    'receipt_package-pi-r2': 'Shell line 8227 wrote a temporary check_receipt.py and failed import, not final production source. Line 8405 reran it with PYTHONPATH=. and saved model checks OK / CLI checks OK; public test at line 9478 saved 1/OK. Initial probe failure retained here, not treated as model completion failure.',
    'frame_decoder-pi-r1': 'Shell line 9999 rewrote decoder.py state constants; line 12366, after the final test edit, saved public 1/OK and custom 26/OK. The earlier shell mutation does not invalidate later saved test success.',
    'repair_routes-pi-r1': 'Shell line 7087 rewrote test_router_extended.py; final router.py write at line 9390 preceded line 9486 public 1/OK and custom 23/OK, followed by combined 24/OK at line 9878.',
}


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def relative(path):
    value = str(path).replace('\\', '/')
    if '/workspace/' in value:
        return value.split('/workspace/', 1)[1]
    if re.match(r'^[A-Za-z]:|^/', value):
        return '[external-path]/' + value.rsplit('/', 1)[-1]
    return value


def clean_command(command):
    # Preserve the command itself for local reasoning, publish only sanitized text.
    text = re.sub(r'(?i)[A-Z]:[\\/][^\r\n"\']*?workspace[\\/]', '', command)
    text = re.sub(r'(?i)(["\'])[A-Z]:[\\/][^\r\n]*?\1', r'\1[absolute-path]\1', text)
    text = re.sub(r'(?i)[A-Z]:[\\/][^\s"\';|]+', '[absolute-path]', text)
    text = re.sub(r'(?i)((?:api[_-]?key|authorization|token)\s*[=:]\s*)[^\s;]+', r'\1[redacted]', text)
    if len(text) > 700 or '\n' in text:
        return text.splitlines()[0][:500] + ' [multiline/long command omitted]'
    return text


def thinkflow(directory):
    data = load(directory / 'harness.json')
    records = {r['id']: r for r in data.get('tool_records', [])}
    starts = {}
    operations = []
    for index, event in enumerate(data.get('events', [])):
        ident = event.get('id')
        if event.get('type') == 'tool_started':
            starts[ident] = (index, event)
        if event.get('type') != 'tool_completed':
            continue
        start_index, start = starts.get(ident, (index, event))
        record = records.get(ident, {})
        operations.append({'ref': f'events[{index}]', 'id': ident, 'tool': event.get('tool'),
                           'start': start.get('seconds', start_index), 'end': event.get('seconds', index),
                           'path': relative(event.get('path') or event.get('dest') or ''),
                           'command': event.get('cmd', ''), 'ok': bool(event.get('success')),
                           'exit_code': record.get('exit_code'),
                           'output': record.get('stdout', '') + '\n' + record.get('stderr', ''),
                           'receipt_saved': bool(record), 'truncated': False})
    return operations, 'harness.json'


def jsonl_tools(directory, harness):
    operations, starts = [], {}
    path = directory / 'stdout.jsonl'
    # Only tool event fields are inspected; assistant messages/reasoning are ignored.
    for line_index, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if harness == 'opencode' and event.get('type') == 'tool_use':
            part = event.get('part', {})
            state = part.get('state', {})
            args = state.get('input', {})
            timing = state.get('time', {})
            meta = state.get('metadata', {})
            operations.append({'ref': f'line {line_index}', 'id': part.get('callID'),
                               'tool': part.get('tool'), 'start': timing.get('start', line_index),
                               'end': timing.get('end', line_index),
                               'path': relative(args.get('filePath') or args.get('path') or ''),
                               'command': args.get('command', ''), 'ok': state.get('status') == 'completed',
                               'exit_code': meta.get('exit'), 'output': state.get('output', ''),
                               'receipt_saved': True, 'truncated': meta.get('truncated', False)})
        if harness == 'pi' and event.get('type') == 'tool_execution_start':
            starts[event.get('toolCallId')] = (line_index, event.get('args', {}))
        if harness == 'pi' and event.get('type') == 'tool_execution_end':
            ident = event.get('toolCallId')
            begin, args = starts.get(ident, (line_index, {}))
            result = event.get('result', {})
            output = '\n'.join(block.get('text', '') for block in result.get('content', []) if block.get('type') == 'text')
            operations.append({'ref': f'line {line_index}', 'id': ident, 'tool': event.get('toolName'),
                               'start': begin, 'end': line_index,
                               'path': relative(args.get('path', '')), 'command': args.get('command', ''),
                               'ok': not event.get('isError', False), 'exit_code': result.get('exitCode'),
                               'output': output, 'receipt_saved': True, 'truncated': False})
    return operations, 'stdout.jsonl'


def is_test_path(path):
    parts = path.lower().replace('\\', '/').split('/')
    return ('tests' in parts or parts[-1].startswith('test') or 'test' in parts[-1] and parts[-1].endswith('.py')
            or '/'.join(parts) in ('.thinkflow/contract_matrix.py', '.thinkflow/verify_cli_edges.py'))


def test_targets(command):
    # This intentionally handles simple command segments only, not a shell grammar.
    command = re.sub(r'\d?>&\d', '', command)
    targets = []
    for segment in re.split(r'[;&|]', command):
        match = TEST.search(segment) or AD_HOC_TEST.search(segment)
        if match:
            target = segment[match.start():].strip()
            target = re.sub(r'\s+-(?:v|q)\b', '', target)
            target = re.sub(r'\s+\d?>.*$', '', target)
            targets.append(clean_command(target).replace('\\', '/').lower())
    return targets


def audit_operations(operations):
    mutations, tests, unknown_writes = [], [], []
    for op in operations:
        tool, command, path = op['tool'], op['command'], op['path']
        if tool in MUTATORS and op['ok']:
            # All non-document mutations count conservatively, including unknown paths.
            if not path.lower().endswith(('.md', '.txt')):
                mutations.append({'ref': op['ref'], 'id': op['id'], 'tool': tool,
                                  'path': path, 'kind': 'test' if is_test_path(path) else 'implementation',
                                  'completed_at': op['end']})
        if tool != 'bash':
            continue
        if SHELL_WRITE.search(command):
            py_paths = re.findall(r'[\w./\\-]+\.py\b', command)
            # Shell writes to test fixtures/data do not necessarily mutate code;
            # absent an explicit Python destination, flag instead of guessing.
            if py_paths:
                mutations.append({'ref': op['ref'], 'id': op['id'], 'tool': tool,
                                  'path': '[shell write: review required]', 'kind': 'uncertain',
                                  'completed_at': op['end']})
                unknown_writes.append(op['ref'])
        invocations = TEST.findall(command)
        helper = AD_HOC_TEST.search(command)
        if helper:
            invocations.append(helper.group(0))
        if not invocations:
            continue
        output = op['output']
        summaries = [{'tests': int(match[0]), 'outcome': 'passed' if match[1].upper().startswith('OK') else 'failed'}
                     for match in SUMMARY.findall(output)]
        if helper:
            if 'contract_matrix.py' in helper.group(1):
                checked = re.search(r'contract matrix: ([1-9]\d*) checks passed', output)
                if checked:
                    summaries.append({'tests': int(checked.group(1)), 'outcome': 'passed', 'format': 'ad_hoc_checks'})
            else:
                checked = re.search(r'([1-9]\d*)/([1-9]\d*) CLI edge checks passed', output)
                if checked:
                    summaries.append({'tests': int(checked.group(2)), 'outcome': 'passed' if checked.group(1) == checked.group(2) else 'failed', 'format': 'ad_hoc_checks'})
        failure = bool(FAILURE.search(output)) or any(s['outcome'] == 'failed' for s in summaries)
        pytest_pass = bool(re.search(r'\b[1-9]\d* passed\b', output)) and not failure
        all_outputs_pass = len(summaries) >= len(invocations) and all(s['outcome'] == 'passed' and s['tests'] > 0 for s in summaries)
        outcome = 'explicit_failure' if failure else 'confirmed_success' if (all_outputs_pass or pytest_pass and len(invocations) == 1) else 'output_unknown'
        # A tool error remains unknown unless explicit test failure is saved.
        if outcome == 'confirmed_success' and not op['ok']:
            outcome = 'output_unknown'
        tests.append({'ref': op['ref'], 'id': op['id'], 'command': clean_command(command),
                      'started_at': op['start'], 'completed_at': op['end'],
                      'tool_success': op['ok'], 'exit_code': op['exit_code'],
                      'shell_can_mask_exit': bool(re.search(r'[;&|]', re.sub(r'\d?>&\d', '', command))),
                      'invocations_detected': len(invocations), 'summaries': summaries,
                      'targets': test_targets(command),
                      'supplied_public_tests_in_command': any('public_tests.py' in invocation.lower() for invocation in invocations),
                      'outcome': outcome, 'receipt_saved': op['receipt_saved'],
                      'output_truncated': op['truncated']})
    last_change = max((m['completed_at'] for m in mutations), default=-1)
    last_implementation_change = max((m['completed_at'] for m in mutations if m['kind'] != 'test'), default=-1)
    final_tests = [t for t in tests if t['started_at'] >= last_change]
    public_confirmed = None
    public_failure_unresolved = False
    for test in tests:
        if test['started_at'] < last_implementation_change or not test['supplied_public_tests_in_command']:
            continue
        if test['outcome'] == 'confirmed_success':
            public_confirmed = True
            public_failure_unresolved = False
        elif test['outcome'] == 'explicit_failure' and test['invocations_detected'] == 1:
            public_confirmed = False
            public_failure_unresolved = True
        else:
            # Mixed/partial output cannot identify which suite failed or ran zero tests.
            public_confirmed = None
    if not tests:
        classification = 'not_executed'
    elif not final_tests:
        classification = 'not_reverified_after_change'
    else:
        # A later success can supersede failure only for the same explicit targets.
        latest_by_target = {}
        for test in final_tests:
            for target in test['targets'] or ['[unrecognized-target]']:
                latest_by_target[target] = test['outcome']
        outcomes = set(latest_by_target.values())
        classification = ('explicit_failure' if 'explicit_failure' in outcomes else
                          'success_unknown' if 'output_unknown' in outcomes else 'confirmed_success')
    if public_failure_unresolved:
        classification = 'explicit_failure'
    # Uncertain shell writes remain in mutation chronology. A test started after
    # their completion can prove final-code execution; do not downgrade forever.
    return {'classification': classification, 'supplied_public_tests_confirmed': public_confirmed,
            'file_mutation_order': mutations,
            'tool_evidence': tests, 'last_code_or_test_change': last_change,
            'last_implementation_change': last_implementation_change,
            'uncertain_shell_mutations': unknown_writes,
            'limitations': 'Saved output proves execution only, not coverage quality. Tests modified by the model remain listed. Shell mutation detection is conservative; unrecognized writes require review.'}


def validate_recovery(run, recovery):
    """Allow only the recorded observer encoding accident, never model failures."""
    original_path = run / RECOVERY_SAMPLE / 'result.json'
    original = load(original_path)
    error = original.get('infrastructure_error')
    if not (isinstance(error, str) and error.startswith('UnicodeEncodeError:') and "'gbk'" in error):
        raise ValueError('Recovery refused: original sample lacks the explicit GBK UnicodeEncodeError.')
    expected = {'id': RECOVERY_SAMPLE, 'task': 'repair_routes', 'harness': 'thinkflow', 'repeat': 2}
    if any(original.get(key) != value for key, value in expected.items()):
        raise ValueError('Recovery refused: original interrupted sample identity differs.')
    original_manifest, recovery_manifest = load(run / 'manifest.json'), load(recovery / 'manifest.json')
    for key in ('source_commit', 'files', 'generation', 'versions'):
        if key not in original_manifest or key not in recovery_manifest or original_manifest[key] != recovery_manifest[key]:
            raise ValueError(f'Recovery refused: manifest {key} differs or is missing.')
    if original_manifest.get('mode') != 'production' or recovery_manifest.get('mode') != 'production':
        raise ValueError('Recovery refused: both experiments must use production mode.')
    result_path = recovery / RECOVERY_SAMPLE / 'result.json'
    if result_path.is_file():
        replacement = load(result_path)
        for key, value in expected.items():
            if replacement.get(key) != value:
                raise ValueError('Recovery refused: replacement sample identity differs.')
        if not original.get('prompt_sha256') or replacement.get('prompt_sha256') != original['prompt_sha256']:
            raise ValueError('Recovery refused: prompt fingerprint differs.')
    return recovery / RECOVERY_SAMPLE


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_dir', type=Path)
    parser.add_argument('--output', type=Path, default=ROOT / 'bench/harness_benchmark_20260929/reports-production/verification-evidence.json')
    parser.add_argument('--recovery-experiment', type=Path,
                        help='Separate experiment containing only the authorized observer-encoding recovery evidence.')
    args = parser.parse_args()
    run = args.run_dir.resolve()
    recovery_directory = validate_recovery(run, args.recovery_experiment.resolve()) if args.recovery_experiment else None
    rows = []
    for planned in load(run / 'plan.json'):
        row = {k: planned[k] for k in ('id', 'task', 'harness', 'repeat')}
        directory = run / planned['id']
        if recovery_directory is not None and planned['id'] == RECOVERY_SAMPLE:
            row.update(original_interrupted=True, original_infrastructure_error_type='UnicodeEncodeError',
                       original_result=(directory / 'result.json').relative_to(ROOT).as_posix(),
                       recovery_result=(recovery_directory / 'result.json').relative_to(ROOT).as_posix(),
                       recovery_manifest_verified=['source_commit', 'files', 'generation', 'versions', 'mode'],
                       recovery_prompt_verified=(recovery_directory / 'result.json').is_file())
            directory = recovery_directory
        if not (directory / 'result.json').is_file():
            row.update(classification='pending', supplied_public_tests_confirmed=None,
                       reason='No completed result.json at audit snapshot.')
        else:
            try:
                operations, source = thinkflow(directory) if planned['harness'] == 'thinkflow' else jsonl_tools(directory, planned['harness'])
                row.update(audit_operations(operations))
                row['source_log'] = (directory / source).relative_to(ROOT).as_posix()
                if planned['id'] in MANUAL_AUDIT:
                    row['manual_audit'] = MANUAL_AUDIT[planned['id']]
            except (ValueError, OSError, KeyError) as error:
                row.update(classification='success_unknown', supplied_public_tests_confirmed=None,
                           reason=f'Unavailable/unreadable tool evidence ({type(error).__name__}).')
        rows.append(row)
    report = {'scope': 'Independent offline audit of saved test tool output after final implementation/test mutation. No model, harness, or solution execution; unknown is not hidden-grader failure.',
              'classifications': {'confirmed_success': '最终代码与最终测试修改后，有真实成功输出',
                                  'success_unknown': '最终测试输出不明或变更证据需人工复核',
                                  'not_reverified_after_change': '最后实现或测试修改后未复验',
                                  'not_executed': '未执行可识别测试命令',
                                  'explicit_failure': '最终测试保存了明确失败输出', 'pending': '样本尚未完成'},
              'counts': dict(Counter(row['classification'] for row in rows)), 'runs': rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report['counts'], ensure_ascii=False))


if __name__ == '__main__':
    main()
