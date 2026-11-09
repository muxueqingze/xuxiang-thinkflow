"""Read-only project audit; effects stay under the explicit fixture directory.
No provider request leaves httpx.MockTransport. Does not load user config.
Usage: python -B reproduce.py PROJECT_ROOT FIXTURE_ROOT
"""
import asyncio
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, sys.argv[1])
import httpx
from src.agent_loop import AgentConfig, AgentLoop
from src.provider import ProviderConfig
from src.security import SecurityPolicy

ROOT = Path(sys.argv[2]).resolve()
ROOT.mkdir(parents=True, exist_ok=True)


class LocalStream(httpx.AsyncByteStream):
    def __init__(self, frames):
        self.frames = frames

    async def __aiter__(self):
        for frame in self.frames:
            yield ('data: ' + json.dumps({'choices': [frame]}) + '\n\n').encode()
            await asyncio.sleep(.02)
        yield b'data: [DONE]\n\n'


async def run_fixture(name, replies, *, provider=None, security=None, **options):
    cwd = ROOT / name
    cwd.mkdir(exist_ok=True)
    events, requests, approvals = [], [], []
    async def approval(tool, arguments):
        approvals.append(tool)
        return False
    agent = AgentLoop(AgentConfig(cwd=str(cwd), provider=provider or ProviderConfig(
        base_url='https://fixture.invalid', model='fixture'),
        security=security or SecurityPolicy(allowed_roots=[str(cwd)]),
        max_run_turns=6, **options), event_sink=events.append, approval_handler=approval)
    await agent.client.aclose()
    def receive(request):
        requests.append(json.loads(request.content))
        frames = replies[min(len(requests) - 1, len(replies) - 1)]
        return httpx.Response(200, stream=LocalStream(frames))
    agent.client = httpx.AsyncClient(base_url='https://fixture.invalid', transport=httpx.MockTransport(receive))
    try:
        await agent.run('Offline audit fixture')
        return {
            'stopped_reason': agent.stopped_reason, 'last_error': agent.last_error,
            'requests': len(requests), 'approval_requests': approvals,
            'advertised_native_tools': [t['function']['name'] for t in requests[0].get('tools', [])],
            'ledger': [{'id': r.id, 'tool': r.tool, 'status': r.status, 'error': r.error} for r in agent.context.records],
            'parser_errors': [e.get('error_kind') for e in events if e['type'] == 'parser_error'],
            'errors': [e.get('error') for e in events if e['type'] == 'error'],
            'files': sorted(p.name for p in cwd.iterdir() if p.is_file()),
            'marker': (cwd / 'marker.txt').read_text() if (cwd / 'marker.txt').exists() else None,
        }
    finally:
        await agent.close()


def text(value):
    return {'delta': {'content': value}}


STOP = {'delta': {}, 'finish_reason': 'stop'}
DONE = [text('Done.'), STOP]


async def main():
    result = {}
    native_call = [{'delta': {'tool_calls': [{'index': 0, 'id': 'excluded-write',
        'function': {'name': 'write', 'arguments': json.dumps({'path': 'marker.txt', 'content': 'EXECUTED'})}}]}},
        {'delta': {}, 'finish_reason': 'tool_calls'}]
    for name, options in [('allow_read_only', {'native_tools': ['read']}),
                          ('disabled_write', {'disabled_native_tools': ['write']}),
                          ('native_off', {'enable_native_tools': False})]:
        result[name] = await run_fixture(name, [native_call, DONE], provider=ProviderConfig(
            base_url='https://fixture.invalid', model='fixture', **options))

    good = '<tf-write id="1" path="marker.txt">EXECUTED</tf-write>'
    bad = '<tf-write id="2">bad</tf-write>'
    def thinking(value):
        return {'delta': {'reasoning_content': value}}
    result['coalesced'] = await run_fixture('coalesced', [[thinking(good + bad), STOP], [thinking(good), STOP], DONE])
    result['split'] = await run_fixture('split', [[thinking(good), thinking(bad), STOP], DONE])

    for name, failure in [('delivery_bypass', False), ('delivery_failure', True)]:
        cwd = ROOT / name
        cwd.mkdir(exist_ok=True)
        (cwd / 'node_modules').mkdir(exist_ok=True)  # Avoid npm install/network entirely.
        script = 'from pathlib import Path\nimport os\nPath("marker.txt").write_text(os.environ.get("THINKFLOW_AUDIT_SECRET", "FILTERED"))\n'
        if failure:
            script += 'raise SystemExit(7)\n'
        (cwd / 'check.py').write_text(script)
        (cwd / 'package.json').write_text(json.dumps({'name': 'audit-local-fixture', 'scripts': {'build': 'python check.py'}}))
        policy = SecurityPolicy(allowed_roots=[str(cwd)], bash_policy='off', approval_mode='request_all')
        write = text('<tf-write id="1" path="app.txt">local content</tf-write>')
        with patch.dict(os.environ, {'THINKFLOW_AUDIT_SECRET': 'DUMMY_FIXTURE_ONLY'}):
            result[name] = await run_fixture(name, [[write, STOP]], security=policy,
                delivery_verify=True, max_delivery_fix_attempts=0)
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
