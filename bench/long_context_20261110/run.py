"""Opt-in DS Flash probe of seeded long-context recovery, not a full task benchmark."""
from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
os.environ['PYTHONUTF8'] = '1'

from src.agent_loop import AgentConfig, AgentLoop
from src.compaction import CompactionConfig, estimate_message_chars
from src.conversation import ConversationContext
from src.prompts import BUILTIN_SYSTEM_PROMPT
from src.provider import ProviderConfig
from src.security import SecurityPolicy
from src.session import SessionStore


BASELINE = 'ee48f51'
STUB = 'def summarize(rows):\n    raise NotImplementedError\n'
PUBLIC = '''from invoice_summary import summarize
assert summarize([{'sku': 'widget', 'quantity': 2, 'unit_price': '3.00'}]) == [
    {'sku': 'widget', 'units': 2, 'total': '6.00'}]
print('PUBLIC_OK')
'''
REQUIREMENTS = '''Implement invoice_summary.py: summarize(rows).
Input is a list of dictionaries with sku (string), quantity (integer), and unit_price (decimal string).
Return one dictionary per SKU, containing sku, units, and total (a string with exactly two decimals).
Use the Python standard library. public_checks.py is a small public smoke check, not the full contract.
Work only in this directory; do not access parent directories, personal files, credentials, or the network.
The following reference notes are background, followed by the authoritative accounting rules.
''' + ''.join(f'Reference note {i}: archived sample row family {i % 7}; these identifiers are not product requirements.\n'
              for i in range(30)) + '''
AUTHORITATIVE ACCOUNTING RULES:
- Canonicalize each SKU with Unicode NFC followed by casefold. Merge equal canonical SKUs and sort by canonical SKU.
- Quantize EACH ROW subtotal (quantity times unit_price) to cents with decimal ROUND_HALF_UP, THEN sum the row totals.
- Reject negative quantity OR negative unit_price with ValueError. Zero quantity rows contribute no group.
- Never mutate the input list or its dictionaries. Return fresh dictionaries.
''' + ''.join(f'Reference note {i}: archived sample row family {i % 7}; these identifiers are not product requirements.\n'
              for i in range(30, 60)) + '''
Deliver invoice_summary.py, run public_checks.py and the checks needed for the full rules, then give a concise result.
Do not edit public_checks.py. No extra packages are needed.
'''
REVISION = 'Keep the original accounting rules. Also omit groups whose final units are zero. The public API and output keys stay unchanged.'

GRADER = r'''
import copy, json
from invoice_summary import summarize
outcomes = {}
def check(name, test):
    try:
        test()
        outcomes[name] = True
    except Exception as exc:
        outcomes[name] = type(exc).__name__ + ': ' + str(exc)
def equal(actual, expected):
    assert actual == expected, (actual, expected)
def rejected(row):
    try:
        summarize([row])
    except ValueError:
        return
    raise AssertionError('negative value accepted')
rows = [dict(sku='STRASSE', quantity=2, unit_price='1.00'),
        dict(sku='Café', quantity=1, unit_price='1.00'),
        dict(sku='Straße', quantity=1, unit_price='1.00'),
        dict(sku='CAFÉ', quantity=1, unit_price='1.00')]
def input_unchanged():
    original = copy.deepcopy(rows)
    summarize(original)
    equal(original, rows)
def fresh_results():
    original = [dict(sku='x', quantity=1, unit_price='1.00')]
    first = summarize(original)
    assert first[0] is not original[0]
    first[0]['total'] = 'changed'
    equal(summarize(original), [dict(sku='x', units=1, total='1.00')])
check('unicode_merge_and_order', lambda: equal([x['sku'] for x in summarize(rows)], ['café', 'strasse']))
check('round_each_row_half_up', lambda: equal(summarize([
    dict(sku='x',quantity=1,unit_price='0.005'), dict(sku='x',quantity=1,unit_price='0.005')
]), [dict(sku='x',units=2,total='0.02')]))
check('aggregation', lambda: equal(summarize([
    dict(sku='x',quantity=1,unit_price='1.00'), dict(sku='x',quantity=2,unit_price='1.00')
]), [dict(sku='x',units=3,total='3.00')]))
check('negative_quantity', lambda: rejected(dict(sku='x',quantity=-1,unit_price='1.00')))
check('negative_price', lambda: rejected(dict(sku='x',quantity=1,unit_price='-0.01')))
check('zero_quantity', lambda: equal(summarize([dict(sku='x',quantity=0,unit_price='1.00')]), []))
check('input_unchanged', input_unchanged)
check('fresh_result_dicts', fresh_results)
check('empty_input', lambda: equal(summarize([]), []))
print(json.dumps(outcomes, ensure_ascii=False))
'''


def legacy_compactor():
    source = subprocess.check_output(['git', 'show', f'{BASELINE}:src/compaction.py'], cwd=ROOT)
    module = types.ModuleType('long_context_baseline_compaction')
    sys.modules[module.__name__] = module
    exec(compile(source, f'{BASELINE}:src/compaction.py', 'exec'), module.__dict__)
    return module.compact_messages


def progress(agent, start, count):
    for number in range(start, start + count):
        # Synthetic history exercises the compaction path; it is not claimed as
        # an actual run of all these earlier steps or tests.
        agent.messages.extend([
            {'role': 'assistant', 'content': f'Historical fixture note {number}: ' + 'repository context ' * 35},
            {'role': 'user', 'content': f'[Fixture observation {number}] ' + 'background material ' * 35},
        ])


def grade(workspace):
    completed = subprocess.run([sys.executable, '-B', '-c', GRADER], cwd=workspace,
                               capture_output=True, text=True, encoding='utf-8', timeout=15)
    if completed.returncode:
        return {'grader_error': completed.stderr[-2000:]}
    return json.loads(completed.stdout.splitlines()[-1])


async def probe(profile, variant, scenario, old_compact):
    with tempfile.TemporaryDirectory(prefix=f'thinkflow-long-{variant}-{scenario}-') as temporary:
        workspace = Path(temporary) / 'workspace'
        workspace.mkdir()
        (workspace / 'invoice_summary.py').write_text(STUB, encoding='utf-8')
        (workspace / 'public_checks.py').write_text(PUBLIC, encoding='utf-8')
        config = AgentConfig(
            cwd=str(workspace), system_prompt=BUILTIN_SYSTEM_PROMPT,
            provider=ProviderConfig(base_url=profile['base_url'], api_key=profile['api_key'],
                model='deepseek-v4.1-flash', thinking_mode='enabled', reasoning_effort='high',
                stream_options_include_usage=True, enable_native_tools=False),
            security=SecurityPolicy(allowed_roots=[str(workspace)], bash_policy='unrestricted',
                                    approval_mode='approve_all'),
            compaction=CompactionConfig(enabled=False, keep_recent_messages=6, max_summary_chars=6000),
            max_run_turns=12, max_run_seconds=300, max_retries=0,
        )
        events = []
        agent = AgentLoop(config, event_sink=lambda event: events.append(event)
                          if event['type'] in ('tool_completed', 'provider_protocol_error') else None)
        try:
            agent.messages = [{'role': 'user', 'content': REQUIREMENTS}]
            agent.conversation.record_request(0, REQUIREMENTS, REQUIREMENTS)
            progress(agent, 0, 24)
            before_chars = estimate_message_chars(agent.messages)
            def compact():
                if variant == 'old_compaction':
                    agent.messages, _ = old_compact(agent.messages, config.compaction, force=True)
                    agent.conversation = ConversationContext()
                else:
                    agent.compact(force=True)
            compact()
            if scenario == 'archived_request':
                agent.conversation.record_request(len(agent.messages), REVISION, REVISION)
                agent.messages.append({'role': 'user', 'content': REVISION})
            progress(agent, 24, 12)
            compact()
            # Test an actual disk snapshot round trip before the continuation.
            store = SessionStore(str(Path(temporary) / 'session.json'))
            store.save(agent.to_snapshot(), history=False)
            agent.load_snapshot(store.load())
            initial_chars = estimate_message_chars(agent.messages)
            attempts = 0
            async def count_request(_request):
                nonlocal attempts
                attempts += 1
            agent.client.event_hooks['request'].append(count_request)
            await agent.run('Continue the unfinished invoice-summary task. Follow its original requirements and later revisions, implement the module, verify it, and finish.')
            checks = grade(workspace)
            result = {
                'variant': variant, 'scenario': scenario, 'baseline_compaction_commit': BASELINE,
                'model': config.provider.model, 'thinking': 'enabled/high',
                'seed_before_chars': before_chars, 'resumed_context_chars': initial_chars,
                'request_attempts': attempts, 'usage': agent.usage.to_dict(),
                'stopped_reason': agent.stopped_reason,
                'last_error': agent.last_error.replace(profile['api_key'], '[REDACTED]'),
                'checks': checks, 'passed': all(value is True for value in checks.values()),
                'tools': [{'tool': record.tool, 'status': record.status,
                           'ref': record.ref, 'offset': record.offset, 'limit': record.limit}
                          for record in agent.context.records],
                'implementation': (workspace / 'invoice_summary.py').read_text(encoding='utf-8'),
                'public_checks_unchanged': (workspace / 'public_checks.py').read_text(encoding='utf-8') == PUBLIC,
                'protocol_errors': [event.get('kind') for event in events if event['type'] == 'provider_protocol_error'],
            }
        finally:
            await agent.close()
    result['temporary_workspace_removed'] = not workspace.exists()
    return result


async def main(args):
    profile = json.loads(args.config.read_text(encoding='utf-8-sig'))['providers']['opencode-go']
    if args.key_stdin:
        supplied_key = getpass.getpass('OpenCode key: ')
        print(json.dumps({'supplied_key_matches_saved_key': supplied_key == profile['api_key']}), flush=True)
        profile['api_key'] = supplied_key
    old_compact = legacy_compactor()
    report = {'kind': 'seeded-context functional probe; not a full long-task benchmark',
              'date': '2026-11-10', 'limits': {'max_run_turns': 12, 'max_run_seconds': 300}, 'runs': []}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for scenario in args.scenarios:
        for variant in args.variants:
            result = await probe(profile, variant, scenario, old_compact)
            report['runs'].append(result)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            print(json.dumps({key: result[key] for key in ('variant', 'scenario', 'passed',
                'request_attempts', 'stopped_reason', 'temporary_workspace_removed')}, ensure_ascii=False), flush=True)
            if result['last_error'].startswith('HTTP 401:'):
                raise SystemExit('OpenCode authentication failed; remaining cases were not started.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', help='Explicitly enable paid Go requests')
    parser.add_argument('--key-stdin', action='store_true', help='Read a supplied key without echoing or saving it')
    parser.add_argument('--config', type=Path, default=Path.home() / '.thinkflow' / 'config.json')
    parser.add_argument('--output', type=Path, default=ROOT / 'docs/evidence/long-context-20261110.json')
    parser.add_argument('--scenarios', nargs='+', choices=('current_request', 'archived_request'),
                        default=['current_request', 'archived_request'])
    parser.add_argument('--variants', nargs='+', choices=('old_compaction', 'current'),
                        default=['old_compaction', 'current'])
    arguments = parser.parse_args()
    if not arguments.run:
        parser.error('Use --run to opt in to paid requests.')
    asyncio.run(main(arguments))
