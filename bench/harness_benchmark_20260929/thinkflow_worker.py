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


async def main():
    prompt = sys.stdin.read()
    destination = Path(os.environ['BENCH_RECORD'])
    started = time.monotonic()
    events = []

    def observe(event):
        if event['type'] in ('stream_started', 'stream_finished', 'tool_started', 'tool_completed', 'run_finished', 'error'):
            fields = ('type','turn','id','tool','channel','flow','status','success','reason','finish_reason')
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
    agent = create_agent(config, resolve_system_prompt(config, cwd=str(Path.cwd())), str(Path.cwd()), event_sink=observe)
    try:
        await agent.run(prompt)
    finally:
        writes = [e for e in events if e['type']=='tool_completed' and e.get('success') and e.get('flow')=='delayed']
        result = {'events':events, 'usage':agent.usage.to_dict(),
                  'last_error':agent.last_error, 'stopped_reason':agent.stopped_reason,
                  'tool_records':[{'id':r.id, 'tool':r.tool, 'status':r.status,
                                   'error':r.error[:1000], 'exit_code':r.exit_code} for r in agent.context.records],
                  'writes_before_stream_finished':sum(any(end['type']=='stream_finished' and end['turn']==e['turn'] and end['seconds']>e['seconds'] for end in events) for e in writes)}
        destination.write_text(json.dumps(result, ensure_ascii=False, indent=2),encoding='utf-8')
    print(json.dumps({'finished':True, 'error':bool(agent.last_error), 'tools':len(agent.context.records)}))


if __name__ == '__main__':
    asyncio.run(main())
