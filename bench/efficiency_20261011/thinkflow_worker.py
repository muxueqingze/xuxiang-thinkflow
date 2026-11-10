"""Identical isolated worker for current and archived ThinkFlow source trees."""
from __future__ import annotations
import asyncio
import json
import os
import re
from pathlib import Path
import sys
import time

# Select source before importing anything from src; no installed-package fallback.
sys.path.insert(0, os.environ['BENCH_SOURCE'])
from src.cli import create_agent, resolve_system_prompt


async def main():
    started = time.monotonic()
    events = []
    destination = Path(os.environ['BENCH_RECORD'])
    fields = ('type', 'turn', 'seq', 'id', 'tool', 'channel', 'flow', 'status', 'success',
              'reason', 'finish_reason', 'kind', 'error_kind', 'phase', 'cmd', 'path', 'dest', 'input_hash', 'input_summary', 'saved_chars', 'read_results')

    def observe(event):
        if event['type'] in ('stream_started', 'stream_finished', 'tool_started', 'tool_completed',
                             'turn_finished', 'run_finished', 'error', 'parser_error', 'provider_protocol_error', 'context_projection'):
            record = {**{k: event[k] for k in fields if k in event},
                      'seconds': round(time.monotonic() - started, 4)}
            events.append(record)
            with destination.with_suffix('.events.jsonl').open('a', encoding='utf8') as stream:
                stream.write(json.dumps(record, ensure_ascii=True) + '\n')
        # Capture only user-visible final text, never reasoning_activity bodies.
        if event['type'] == 'text_delta':
            print(json.dumps({'type': 'text_delta', 'text': event.get('text', '')}, ensure_ascii=False), flush=True)

    config = {'provider': 'openai', 'base_url': os.environ['BENCH_URL'], 'api_key': os.environ['BENCH_API_KEY'],
              'model': os.environ.get('BENCH_MODEL', 'deepseek-flash'), 'thinking_mode': 'enabled', 'reasoning_effort': 'high',
              'stream_options_include_usage': True, 'max_tokens': None, 'max_run_turns': None,
              'max_run_seconds': None, 'max_auto_continues': None, 'compaction': {'enabled': False},
              'context': {'enabled': False}, 'interfaces': {'skills': {'enabled': False}, 'web': {'enabled': False}},
              'disabled_native_tools': ['web_search', 'fetch_url', 'image_generate', 'list_skills'],
              'security': {'profile': 'balanced', 'bash_policy': 'unrestricted', 'approval_mode': 'approve_all',
                           'allowed_roots': [str(Path.cwd())]}}
    native_setting = os.environ.get('BENCH_NATIVE_TOOLS', 'default')
    if native_setting != 'default':
        config['enable_native_tools'] = native_setting == 'enabled'
    agent = create_agent(config, resolve_system_prompt(config, cwd=str(Path.cwd())), str(Path.cwd()), event_sink=observe)
    response_structure = []
    process_stream = agent._process_stream

    async def observed_stream(response):
        channels = {'text': '', 'thinking': ''}
        native_calls = []
        try:
            async for event in process_stream(response):
                if event.text:
                    channels['text'] += event.text
                if event.thinking_text:
                    channels['thinking'] += event.thinking_text
                if event.tool_name:
                    native_calls.append(event.tool_name)
                yield event
        finally:
            # Diagnose protocol routing without persisting model prose or payloads.
            response_structure.append({
                'turn': len(response_structure) + 1,
                'native_calls': native_calls,
                'channels': {name: {
                    'chars': len(value),
                    'tags': [{'name': match.group(1), 'id': match.group(2)} for match in
                             re.finditer(r'<([\w-]+)\s+id=[\"\x27]([^\"\x27]+)[\"\x27]', value)],
                } for name, value in channels.items()},
            })

    agent._process_stream = observed_stream
    if '--preflight' in sys.argv:
        body = agent.build_request_body(agent.build_system_prompt())
        print(json.dumps({'source': os.environ['BENCH_SOURCE'],
                          'output_limits': {k: body[k] for k in ('max_tokens', 'max_completion_tokens') if k in body},
                          'thinking': body.get('thinking'), 'effort': body.get('reasoning_effort'),
                          'max_run_turns': agent.config.max_run_turns, 'max_run_seconds': agent.config.max_run_seconds,
                          'max_auto_continues': agent.config.max_auto_continues,
                          'max_consecutive_failures': agent.config.max_consecutive_failures,
                          'enable_native_tools': agent.config.provider.enable_native_tools,
                          'compaction_enabled': agent.config.compaction.enabled,
                          'tools': [t.get('function', {}).get('name') for t in body.get('tools', [])]}))
        await agent.close()
        return
    try:
        await agent.run(sys.stdin.read())
    finally:
        await agent.close()
        value = {'events': events, 'usage': agent.usage.to_dict(), 'last_error': agent.last_error,
                 'response_structure': response_structure,
                 'stopped_reason': agent.stopped_reason,
                 'execution_config': {**{k: config[k] for k in ('max_tokens', 'max_run_turns', 'max_run_seconds',
                                                              'max_auto_continues', 'compaction')},
                                      'enable_native_tools': agent.config.provider.enable_native_tools},
                 'effective_consecutive_failure_limit': agent.config.max_consecutive_failures,
                 'tool_records': [{'id': r.id, 'tool': r.tool, 'status': r.status, 'exit_code': r.exit_code,
                                   'cmd': r.cmd, 'stdout': r.stdout, 'stderr': r.stderr,
                                   'verified_revisions': getattr(r, 'verified_revisions', {})}
                                  for r in agent.context.records]}
        destination.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf8')


if __name__ == '__main__':
    asyncio.run(main())
