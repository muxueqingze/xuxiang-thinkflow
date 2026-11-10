"""Opt-in real-model recovery of a one-use tool output after a disk restore."""
import argparse
import asyncio
import getpass
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'

from src.agent_loop import AgentConfig, AgentLoop
from src.compaction import CompactionConfig
from src.parser import Command
from src.prompts import BUILTIN_SYSTEM_PROMPT
from src.provider import ProviderConfig
from src.security import SecurityPolicy
from src.session import SessionStore


async def probe(profile):
    expected = {'alpha': 'alpha-27-中文', 'beta': 'beta-61-Zoe'}
    output = ('TRACE_START\n' + 'a' * 30000 + '\nCONTRACT_ALPHA=' + expected['alpha'] + '\n'
              + 'b' * 30000 + '\nCONTRACT_BETA=' + expected['beta'] + '\n'
              + 'z' * 90000 + '\nTRACE_COMPLETE\n')
    with tempfile.TemporaryDirectory(prefix='thinkflow-output-live-') as temporary:
        root = Path(temporary)
        workspace = root / 'workspace'
        workspace.mkdir()
        (workspace / 'scan-once.txt').write_bytes(output.encode())
        config = AgentConfig(cwd=str(workspace), system_prompt=BUILTIN_SYSTEM_PROMPT,
            provider=ProviderConfig(base_url=profile['base_url'], api_key=profile['api_key'],
                model='deepseek-v4.1-flash', thinking_mode='enabled', reasoning_effort='high',
                stream_options_include_usage=True, enable_native_tools=False),
            security=SecurityPolicy(allowed_roots=[str(workspace)], bash_policy='unrestricted',
                                    approval_mode='approve_all'),
            compaction=CompactionConfig(enabled=False), max_retries=0,
            max_run_turns=8, max_run_seconds=180)
        protocol_errors = []
        def event_sink(event):
            if event['type'] in ('provider_protocol_error', 'parser_error'):
                protocol_errors.append({'type': event['type'], 'kind': event.get('kind')})
        agent = AgentLoop(config, event_sink=event_sink)
        resumed = None
        try:
            code = 'from pathlib import Path\nimport sys\np=Path("scan-once.txt")\nsys.stdout.buffer.write(p.read_bytes())\np.unlink()'
            await agent._execute_command(Command('1', 'python', cmd=code))
            record = agent.context.records[0]
            record.injected = 1
            agent.context.compact_history(payload_budget=300)
            agent.messages = [{'role': 'user', 'content': record.to_injection_string(max_content_chars=0)}]
            store = SessionStore(str(root / 'session.json'))
            store.save(agent.to_snapshot(), history=False)
            await agent.close()
            resumed = AgentLoop(config, event_sink=event_sink)
            resumed.bind_output_store(store.output_dir)
            resumed.load_snapshot(store.load())
            attempts = 0
            async def count_request(_request):
                nonlocal attempts
                attempts += 1
            resumed.client.event_hooks['request'].append(count_request)
            await resumed.run('The one-use scan has already completed; its input file is gone. '
                'Recover the exact values from its CONTRACT_ALPHA and CONTRACT_BETA records. '
                'Write report.json with keys alpha and beta and those values. Use the saved original '
                'tool output; do not rerun the scan, inspect parent directories, or use the network. '
                'Finish with a concise result. Work only in this directory.')
            artifact = workspace / 'report.json'
            produced = artifact.read_text(encoding='utf-8') if artifact.exists() else ''
            try:
                parsed = json.loads(produced)
            except json.JSONDecodeError:
                parsed = None
            result = {'kind': 'one-use output recovery after clipped receipt and disk restore; not a full task benchmark',
                'date': '2026-11-11', 'model': config.provider.model, 'thinking': 'enabled/high',
                'limits': {'max_run_turns': 8, 'max_run_seconds': 180},
                'source_characters': len(output), 'source_input_removed': not (workspace / 'scan-once.txt').exists(),
                'artifact_passed': parsed == expected,
                'delivery_complete': resumed.stopped_reason == 'completed' and not resumed.last_error,
                'expected': expected, 'artifact': produced,
                'request_attempts': attempts, 'usage': resumed.usage.to_dict(),
                'stopped_reason': resumed.stopped_reason,
                'last_error': resumed.last_error.replace(profile['api_key'], '[REDACTED]'),
                'protocol_errors': protocol_errors,
                'tools': [{'id': rec.id, 'tool': rec.tool, 'status': rec.status, 'ref': rec.ref,
                           'offset': rec.offset, 'limit': rec.limit, 'query': rec.query,
                           'error': rec.error[:300], 'path': rec.path} for rec in resumed.context.records]}
        finally:
            await agent.close()
            if resumed is not None:
                await resumed.close()
    result['temporary_workspace_removed'] = not root.exists()
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--key-stdin', action='store_true')
    parser.add_argument('--output', type=Path, default=ROOT / 'docs/evidence/output-recovery-20261111.json')
    args = parser.parse_args()
    if not args.run:
        parser.error('Use --run to opt in to paid model requests.')
    profile = json.loads((Path.home()/'.thinkflow/config.json').read_text(encoding='utf-8-sig'))['providers']['opencode-go']
    if args.key_stdin:
        profile['api_key'] = getpass.getpass('OpenCode key: ')
    report = asyncio.run(probe(profile))
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({key: report[key] for key in ('artifact_passed', 'delivery_complete', 'request_attempts', 'stopped_reason', 'last_error', 'temporary_workspace_removed')}, ensure_ascii=False))
