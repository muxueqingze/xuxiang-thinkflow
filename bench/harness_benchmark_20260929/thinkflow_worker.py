"""One isolated ThinkFlow run. Receives only a loopback meter token, never the real key."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.cli import create_agent, resolve_system_prompt
from monitor import Monitor


async def main():
    prompt = sys.stdin.read()
    destination = Path(os.environ['BENCH_RECORD'])
    started = time.monotonic()
    events = []
    agent = None
    monitor = None
    monitor_errors = []

    def monitor_failed(exc):
        monitor_errors.append(type(exc).__name__ + ': ' + str(exc)[:300])
        print('Benchmark monitor unavailable; execution continues.', file=sys.stderr)

    def snapshot():
        if agent is None:
            return {}
        body = agent.build_request_body(agent.build_system_prompt())
        messages = body.get('messages', [])
        return {'context_chars': len(json.dumps(messages, ensure_ascii=False)),
                'message_count': len(messages),
                'turn_usage': [turn.to_dict() for turn in agent.usage.turns],
                'run_active': agent._run_active,
                'stopped_reason': 'running' if agent._run_active else agent.stopped_reason}

    def observe(event):
        if monitor is not None:
            try:
                monitor.observe(event)
            except OSError as exc:
                monitor.disabled = True
                monitor_failed(exc)
        if event['type'] in ('stream_started', 'stream_finished', 'tool_started', 'tool_completed', 'turn_finished', 'run_finished', 'error'):
            fields = ('type','turn','id','tool','channel','flow','status','success','reason','finish_reason',
                      'input_summary','cmd','path','dest','input_hash','error')
            events.append({**{key: event[key] for key in fields if key in event},
                           'seconds': round(time.monotonic() - started, 4)})

    config = {'provider':'openai', 'base_url':os.environ['BENCH_URL'], 'api_key':os.environ['BENCH_API_KEY'],
              'model':'deepseek-flash', 'thinking_mode':'enabled', 'reasoning_effort':'high',
              'max_tokens':8192, 'stream_options_include_usage':True,
              'max_run_turns':24, 'max_run_seconds':300, 'max_retries':1,
              'context':{'enabled':False},
              'interfaces':{'skills':{'enabled':False}, 'web':{'enabled':False}},
              'disabled_native_tools':['web_search','fetch_url','generate_image','list_skills'],
              'security':{'profile':'balanced','bash_policy':'unrestricted','approval_mode':'approve_all',
                          'bash_timeout_seconds':40, 'allowed_roots':[str(Path.cwd())]}}
    if os.environ.get('BENCH_MODE') == 'production':
        config.update(max_tokens=None, max_run_turns=None, max_run_seconds=None,
                      max_auto_continues=None, compaction={'enabled':False})
        config.pop('max_retries')
        config['security'].pop('bash_timeout_seconds')
        config['disabled_native_tools'] = ['web_search','fetch_url','image_generate','list_skills']
    agent = create_agent(config, resolve_system_prompt(config, cwd=str(Path.cwd())), str(Path.cwd()), event_sink=observe)
    try:
        monitor = Monitor(destination.parent, snapshot)
    except OSError as exc:
        monitor_failed(exc)

    async def heartbeat():
        while monitor is not None and not monitor.disabled:
            await asyncio.sleep(1)
            try:
                monitor.flush()
            except OSError as exc:
                monitor.disabled = True
                monitor_failed(exc)

    ticker = asyncio.create_task(heartbeat())
    try:
        await agent.run(prompt)
    finally:
        ticker.cancel()
        try:
            await ticker
        except asyncio.CancelledError:
            pass
        try:
            await agent.close()
        finally:
            if monitor is not None:
                try:
                    monitor.close()
                except OSError as exc:
                    monitor_failed(exc)
        writes = [e for e in events if e['type']=='tool_completed' and e.get('success') and e.get('flow')=='delayed']
        result = {'events':events, 'usage':agent.usage.to_dict(),
                  'execution_config':{k:config.get(k) for k in ('max_tokens','max_run_turns','max_run_seconds','max_auto_continues','compaction')},
                  'monitor_errors':monitor_errors,
                  'last_error':agent.last_error, 'stopped_reason':agent.stopped_reason,
                  'tool_records':[{'id':r.id, 'tool':r.tool, 'status':r.status,
                                   'error':r.error[:1000], 'exit_code':r.exit_code,
                                   'stdout':r.stdout, 'stderr':r.stderr} for r in agent.context.records],
                  'writes_before_stream_finished':sum(any(end['type']=='stream_finished' and end['turn']==e['turn'] and end['seconds']>e['seconds'] for end in events) for e in writes)}
        destination.write_text(json.dumps(result, ensure_ascii=False, indent=2),encoding='utf-8')
    print(json.dumps({'finished':True, 'error':bool(agent.last_error), 'tools':len(agent.context.records)}))


if __name__ == '__main__':
    asyncio.run(main())
