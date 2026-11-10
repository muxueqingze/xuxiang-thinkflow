"""Opt-in DS Flash range-read/edit conformance using the frozen pricing task."""
import argparse
import asyncio
import getpass
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'

from bench.harness_benchmark_20260929.tasks import grade, materialize
from src.agent_loop import AgentConfig, AgentLoop
from src.compaction import CompactionConfig
from src.prompts import BUILTIN_SYSTEM_PROMPT
from src.provider import ProviderConfig
from src.security import SecurityPolicy


async def probe(profile):
    with tempfile.TemporaryDirectory(prefix='thinkflow-range-live-') as temporary:
        workspace = Path(temporary)
        prompt = materialize('pricing_refactor', workspace)
        prompt += ('\nFor this read/edit integration check, read the README, checkout.py, public_tests.py, '
                   'and reporting.py in one information batch. For reporting.py use read offset=3, limit=6 '
                   '(the customer_totals function); its first line is the documented REPORT_FORMAT constant '
                   'and must stay unchanged. Read only that range of reporting.py; use exact edit on the '
                   'observed code. Use the file tools for implementation changes and Python/shell for checks. '
                   'Keep the existing source files and preserve the full public contract. Work only here.')
        config = AgentConfig(cwd=str(workspace), system_prompt=BUILTIN_SYSTEM_PROMPT,
            provider=ProviderConfig(base_url=profile['base_url'], api_key=profile['api_key'],
                model='deepseek-v4.1-flash', thinking_mode='enabled', reasoning_effort='high',
                stream_options_include_usage=True, enable_native_tools=False),
            security=SecurityPolicy(allowed_roots=[str(workspace)], bash_policy='unrestricted',
                                    approval_mode='approve_all'),
            compaction=CompactionConfig(enabled=False), max_retries=0,
            max_run_turns=6, max_run_seconds=180)
        errors = []
        def event_sink(event):
            if event['type'] in ('provider_protocol_error', 'parser_error'):
                errors.append({'type': event['type'], 'kind': event.get('kind')})
        agent = AgentLoop(config, event_sink=event_sink)
        attempts = 0
        async def count_request(_request):
            nonlocal attempts
            attempts += 1
        agent.client.event_hooks['request'].append(count_request)
        try:
            await agent.run(prompt)
            result = {
                'kind': 'range-read/exact-edit integration; frozen pricing_refactor quality checks; not an A/B benchmark',
                'date': '2026-11-11', 'model': config.provider.model, 'thinking': 'enabled/high',
                'prompt': prompt, 'limits': {'max_run_turns': 6, 'max_run_seconds': 180},
                'request_attempts': attempts, 'usage': agent.usage.to_dict(),
                'delivery_complete': agent.stopped_reason == 'completed' and not agent.last_error,
                'stopped_reason': agent.stopped_reason, 'last_error': agent.last_error,
                'protocol_errors': errors,
                'tools': [{'id': r.id, 'tool': r.tool, 'status': r.status,
                           'path': Path(r.path).name if r.path else None,
                           'offset': r.offset, 'limit': r.limit, 'read_note': r.read_note,
                           'output_chars': r.output_chars, 'error': r.error,
                           'cmd': r.cmd, 'stdout': r.stdout, 'stderr': r.stderr} for r in agent.context.records],
                'quality': await asyncio.to_thread(grade, 'pricing_refactor', workspace),
                'sources': {name: (workspace / name).read_text(encoding='utf-8') for name in
                            ('checkout.py', 'reporting.py', 'pricing.py') if (workspace / name).exists()},
            }
            reads = [r for r in agent.context.records if r.tool == 'read' and r.path == 'reporting.py']
            result['range_contract_used'] = bool(reads) and all(r.offset == 3 and r.limit == 6 for r in reads)
            edit_positions = [i for i, r in enumerate(result['tools'])
                              if r['tool'] == 'edit' and r['path'] == 'reporting.py' and r['status'] == 'success']
            preceding_reads = [r for r in result['tools'][:edit_positions[0]]
                               if r['tool'] == 'read' and r['path'] == 'reporting.py'] if edit_positions else []
            result['range_edit_before_any_full_read'] = bool(preceding_reads) and all(
                r['offset'] == 3 and r['limit'] == 6 for r in preceding_reads)
            result['report_constant_unchanged'] = (workspace / 'reporting.py').read_bytes().startswith(b"REPORT_FORMAT='customer-totals-v1'\n\n")
        finally:
            await agent.close()
    result['temporary_workspace_removed'] = not workspace.exists()
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--key-stdin', action='store_true')
    parser.add_argument('--output', type=Path, default=ROOT / 'docs/evidence/read-ranges-20261111.json')
    args = parser.parse_args()
    if not args.run:
        parser.error('Use --run to opt in to paid model requests.')
    profile = json.loads((Path.home() / '.thinkflow/config.json').read_text(encoding='utf-8-sig'))['providers']['opencode-go']
    if args.key_stdin:
        profile['api_key'] = getpass.getpass('OpenCode key: ')
    report = asyncio.run(probe(profile))
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: report[key] for key in ('delivery_complete', 'quality', 'request_attempts',
          'range_contract_used', 'report_constant_unchanged', 'temporary_workspace_removed')}, ensure_ascii=False))
