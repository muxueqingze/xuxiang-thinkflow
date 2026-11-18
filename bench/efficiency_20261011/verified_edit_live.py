"""Rerun the synthetic verified-edit tasks with live OpenCode Go usage.

Provide OPENCODE_GO_API_KEY in the parent environment. Worker environments
receive only a loopback token. Model messages and tool bodies stay in memory.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from bench.efficiency_20261011.meter import Meter
from bench.efficiency_20261011.run import configuration, stop_child, save


MODEL = 'deepseek-v4.1-flash'
ENDPOINT = 'https://opencode.ai/inference/go/openai/v1'
REQUEST_CAP = 6
OUTPUT_CAP = 2048
TIME_CAP = 100
EVENT_FIELDS = ('type', 'turn', 'tool', 'channel', 'flow', 'status', 'success',
                'reason', 'finish_reason', 'kind', 'error_kind', 'phase', 'code',
                'match', 'id_source', 'input_relation', 'disposition')
EVENT_TYPES = {'stream_started', 'stream_finished', 'tool_completed', 'tool_started',
               'tool_replay', 'turn_finished', 'run_finished',
               'error', 'parser_error', 'provider_protocol_error'}


class CappedMeter(Meter):
    def __init__(self, key, destination, workspace, source):
        super().__init__(key, destination, upstream=ENDPOINT, model=MODEL)
        alias_root = Path('C:/benchmark') if os.name == 'nt' else Path('/benchmark')
        self.path_aliases = {
            str(workspace): str(alias_root / 'workspace'),
            str(source): str(alias_root / 'source'),
            str(ROOT): str(alias_root / 'harness'),
            str(Path.home()): str(alias_root / 'user'),
        }

    def prepare(self, body):
        if len(self.requests) > REQUEST_CAP or time.monotonic() - self.started >= TIME_CAP:
            raise TimeoutError('Benchmark request/time cap')
        incoming, generation = super().prepare(body)
        body['max_tokens'] = generation['max_tokens'] = OUTPUT_CAP
        for message in body['messages']:
            for field in ('content', 'reasoning_content'):
                if not isinstance(message.get(field), str):
                    continue
                for original, alias in self.path_aliases.items():
                    forms = ((original, alias), (original.replace(chr(92), '/'), alias.replace(chr(92), '/')),
                             (json.dumps(original, ensure_ascii=False)[1:-1], json.dumps(alias)[1:-1]))
                    for private, synthetic in forms:
                        message[field] = message[field].replace(private, synthetic)
        return incoming, generation

    def observe_line(self, line, record):
        if line.startswith(b'data:') and line[5:].strip() == b'[DONE]':
            record['sse_done'] = True
        super().observe_line(line, record)


async def worker():
    sys.path.insert(0, os.environ['BENCH_SOURCE'])
    from bench.efficiency_20261011 import thinkflow_worker as bench_worker
    from src.context import _command_hash
    from src.parser import TOOLS, StreamingParser, _parse_attrs

    record_path = Path(os.environ['BENCH_RECORD'])
    event_record = []
    commands = []
    repeated = []
    seen = {}
    text_by_turn = {}
    stream_channels = {}
    native_calls = {}
    tag_positions = []
    workspace = Path.cwd()
    initial_target = (workspace / 'release.cfg').read_bytes()
    agent = None
    build = StreamingParser._build_command
    create_agent = bench_worker.create_agent
    write_text = Path.write_text

    def path_scope(raw_path):
        if not raw_path:
            return None
        path = (workspace / raw_path).resolve()
        if path == workspace / 'release.cfg':
            return 'target'
        if path == workspace / 'applied.txt':
            return 'marker'
        return 'other_workspace' if path.is_relative_to(workspace) else 'outside_workspace'

    def workspace_state():
        target = workspace / 'release.cfg'
        marker = workspace / 'applied.txt'
        target_state = ('other' if target.is_symlink() else 'absent' if not target.is_file() else
                        'seed' if target.read_bytes() == initial_target else 'changed')
        marker_state = ('other' if marker.is_symlink() else 'absent' if not marker.is_file() else
                        'expected' if marker.read_bytes() == b'APPLIED' else 'other')
        return target_state, marker_state, any(p.name not in ('release.cfg', 'applied.txt') for p in workspace.iterdir())

    def observe(event):
        if event['type'] in EVENT_TYPES:
            record = {key: event[key] for key in EVENT_FIELDS if key in event}
            if event['type'] in ('tool_completed', 'tool_started'):
                record['path_scope'] = path_scope(event.get('path'))
                if event.get('dest'):
                    record['dest_scope'] = path_scope(event['dest'])
            if event['type'] == 'tool_completed' and event.get('tool') in ('edit', 'write', 'append', 'copy', 'python', 'bash'):
                record['fixture_state'] = workspace_state()
            event_record.append(record)
        if event['type'] == 'text_delta':
            text_by_turn.setdefault(event['turn'], []).append(event.get('text', ''))

    def create(*args, **kwargs):
        nonlocal agent
        agent = create_agent(*args, **{**kwargs, 'event_sink': observe})
        process_stream = agent._process_stream

        async def observe_placement(response):
            turn = agent.events.turn
            channels = stream_channels[turn] = {'text': '', 'thinking': ''}
            calls = native_calls[turn] = []
            async for event in process_stream(response):
                if event.tool_name:
                    calls.append(event.tool_name)
                if event.text:
                    channels['text'] += event.text
                if event.thinking_text:
                    channels['thinking'] += event.thinking_text
                yield event

        agent._process_stream = observe_placement
        return agent

    def measure_placement(raw, turn, channel):
        key = (turn, channel)
        body = stream_channels.get(turn, {}).get(channel, '')
        end = placement_offsets.get(key, 0)
        start = body.find(raw, end)
        if start < 0:
            return 'unknown'
        placement_offsets[key] = start + len(raw)
        opening_line = body.rfind(chr(10), 0, start) + 1
        closing_line = body.find(chr(10), start + len(raw))
        if closing_line < 0:
            closing_line = len(body)
        fences = [line.strip()[:3] for line in body[:opening_line].splitlines()
                  if line.lstrip().startswith(('~~~', '```'))]
        if len(fences) % 2:
            return 'fenced'
        prefix = body[opening_line:start]
        open_ticks = 0
        for ticks in re.findall(r'`+', prefix):
            if not open_ticks:
                open_ticks = len(ticks)
            elif open_ticks == len(ticks):
                open_ticks = 0
        if open_ticks:
            return 'inline_code'
        if prefix.strip() or body[start + len(raw):closing_line].strip():
            return 'inline_prose'
        return 'standalone'

    placement_offsets = {}

    def observed_build(parser, tool, attrs_str, content, raw, *args, **kwargs):
        if agent is None or parser not in (agent.parser, agent.text_parser):
            return build(parser, tool, attrs_str, content, raw, *args, **kwargs)
        command_id = _parse_attrs(attrs_str).get('id')
        if command_id and command_id in seen:
            clean = StreamingParser(allow_legacy_tags=parser.allow_legacy_tags)
            probe = build(clean, tool, attrs_str, content, raw, *args, **kwargs)
            relation = ('unknown' if probe is None else
                        'same_input' if _command_hash(probe) == seen[command_id] else 'different_input')
            repeated.append({'turn': agent.events.turn, 'tool': tool,
                             'channel': 'thinking' if parser is agent.parser else 'text',
                             'input_relation': relation})
        command = build(parser, tool, attrs_str, content, raw, *args, **kwargs)
        if command is not None:
            if command_id:
                seen.setdefault(command_id, _command_hash(command))
            tag_positions.append((raw, len(commands)))
            commands.append({'turn': agent.events.turn, 'tool': command.tool,
                             'channel': 'thinking' if parser is agent.parser else 'text',
                             'match': command.match, 'path_scope': path_scope(command.path),
                             'id_source': 'explicit' if command_id else 'implicit'})
        return command

    def safe_record(path, data, *args, **kwargs):
        if path == record_path:
            details = json.loads(data)
            for raw, index in tag_positions:
                item = commands[index]
                item['placement'] = measure_placement(raw, item['turn'], item['channel'])
            final_turn = agent.events.turn
            final_text = ''.join(text_by_turn.get(final_turn, ()))
            channels = stream_channels.get(final_turn, {})
            channel_tags_by_turn = {
                turn: {channel: [name for name in re.findall(r'<tf-([a-z_]+)(?:[\s/>]|$)', body)
                                 if name in TOOLS] for channel, body in by_channel.items()}
                for turn, by_channel in stream_channels.items()
            }
            final_turn_structure = {
                'turn': final_turn,
                'channel_chars': {channel: len(body) for channel, body in channels.items()},
                'channel_tag_names': channel_tags_by_turn.get(final_turn, {}),
                'native_tools': native_calls.get(final_turn, []),
                'parsed_tags': [item['tool'] for item in commands if item['turn'] == final_turn],
            }
            data = json.dumps({
                'events': event_record, 'commands': commands, 'explicit_id_repeats': repeated,
                'stop': details['stopped_reason'], 'usage': details['usage'],
                'final_reported_mismatch': any(phrase in final_text
                                               for phrase in ('不符', '不一致', '不匹配', '不相同', '已变', '多了一行', '多出', '额外一行', '差异')),
                'final_has_text': bool(final_text.strip()),
                'final_turn_structure': final_turn_structure,
                'channel_tag_names_by_turn': channel_tags_by_turn,
            }, ensure_ascii=False)
        return write_text(path, data, *args, **kwargs)

    bench_worker.create_agent = create
    StreamingParser._build_command = observed_build
    Path.write_text = safe_record
    try:
        await bench_worker.main()
    finally:
        Path.write_text = write_text
        StreamingParser._build_command = build


def run_job(key, source, scratch, fixture, scenario, repeat):
    run = scratch / f'{scenario}-{repeat}'
    workspace = run / 'workspace'
    workspace.mkdir(parents=True)
    seed = fixture[f'{scenario}_seed_hex']
    target = workspace / 'release.cfg'
    marker = workspace / 'applied.txt'
    target.write_bytes(bytes.fromhex(seed))
    meter = CappedMeter(key, run / 'meter.json', workspace, source)
    endpoint = meter.start()
    command, profile = configuration('thinkflow', run, endpoint, meter.token, {'generation': {'model': MODEL}})
    profile['BENCH_SOURCE'] = str(source)
    command = [command[0], '-B', str(Path(__file__).resolve()), '--worker']
    started = time.monotonic()
    worker_error = None
    process = None
    try:
        process = subprocess.Popen(command, cwd=workspace, env=profile, stdin=subprocess.PIPE,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   start_new_session=os.name != 'nt',
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        process.communicate(fixture['prompt'].encode('utf-8'), timeout=TIME_CAP)
    except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
        worker_error = type(exc).__name__
        if process is not None:
            stop_child(process)
        if isinstance(exc, KeyboardInterrupt):
            raise
    finally:
        usage = meter.finish()
    record = run / 'harness.json'
    harness = json.loads(record.read_text(encoding='utf-8')) if record.is_file() else {}
    target_hex = target.read_bytes().hex() if target.is_file() else None
    marker_hex = marker.read_bytes().hex() if marker.is_file() else None
    request_fields = ('index', 'http_status', 'finished', 'finish_reason', 'sse_done',
                      'client_disconnected', 'transport_error', 'forwarded_generation')
    requests = [{**{name: row[name] for name in request_fields if name in row},
                 'usage': row.get('raw_usage')} for row in meter.requests]
    price_fields = ('list_cost', 'allowance')
    costs = [{name: (row['usage'] or {}).get('opencode', {}).get(name) for name in price_fields}
             for row in requests]
    exit_code = process.returncode if process is not None else None
    completed = bool(exit_code == 0 and not worker_error and harness.get('stop') == 'completed'
                     and requests and usage['usage_complete']
                     and all(row.get('http_status') == 200 and row.get('finished') and row.get('sse_done')
                             and row.get('finish_reason') for row in requests))
    result = {'scenario': scenario, 'repeat': repeat, 'seconds': round(time.monotonic() - started, 3),
              'exit': exit_code, 'worker_error': worker_error, 'completed': completed,
              'actual_file_hex': target_hex, 'actual_marker_hex': marker_hex,
              'expected_bytes_pass': (target_hex == (fixture['expected_target_hex'] if scenario == 'match' else seed)
                                      and marker_hex == (fixture['expected_marker_hex'] if scenario == 'match' else None)),
              'workspace_entries': sorted(path.name for path in workspace.iterdir()),
              'usage': usage, 'requests': requests,
              'cost_usd': {name: round(sum(row[name] for row in costs), 8)
                           if costs and all(row[name] is not None for row in costs) else None
                           for name in price_fields}, 'harness': harness}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--scratch', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source-id', required=True)
    parser.add_argument('--fixture', type=Path, default=ROOT / 'docs/evidence/verified-edits-20261118.json')
    args = parser.parse_args()
    if any((args.scratch / f'{scenario}-{repeat}').exists()
           for repeat in (1, 2) for scenario in ('match', 'mismatch')):
        parser.error('--scratch must not contain previous runs')
    if args.output.exists():
        parser.error('--output must be a new file')
    fixture = json.loads(args.fixture.read_text(encoding='utf-8'))
    original = fixture['experiments'][0]
    fixture = {**original['fixture_bytes'], 'prompt': original['prompt']}
    source = args.source.resolve()
    key = os.environ['OPENCODE_GO_API_KEY']
    evidence = {'source': args.source_id, 'model': MODEL, 'endpoint': ENDPOINT,
                'caps': {'requests': REQUEST_CAP, 'max_tokens_per_request': OUTPUT_CAP,
                         'worker_seconds': TIME_CAP}, 'jobs': []}
    for repeat in (1, 2):
        for scenario in ('match', 'mismatch'):
            print(f'running {scenario}-{repeat}', flush=True)
            result = run_job(key, source, args.scratch.resolve(), fixture, scenario, repeat)
            evidence['jobs'].append(result)
            save(args.output, evidence)
            print(json.dumps({field: result[field] for field in ('scenario', 'repeat', 'completed',
                                                                  'expected_bytes_pass', 'cost_usd')}),
                  flush=True)
            if any(row.get('http_status') in (401, 403) for row in result['requests']):
                return 1
    return 0


if __name__ == '__main__':
    if sys.argv[1:] == ['--worker']:
        asyncio.run(worker())
    else:
        raise SystemExit(main())
