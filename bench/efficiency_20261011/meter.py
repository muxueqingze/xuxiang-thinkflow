"""Unbounded loopback meters. Persist usage and metadata, never model bodies or keys."""
from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from bench.harness_benchmark_20260929.meter import summarize as openai_summary


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def normalize_anthropic_usage(usage):
    """Anthropic input_tokens excludes cache reads/creation; keep raw usage too."""
    value = dict(usage)
    if type(usage.get('input_tokens')) is int:
        read = usage.get('cache_read_input_tokens')
        write = usage.get('cache_creation_input_tokens')
        if type(read) is int and type(write) is int:
            value['prompt_tokens'] = usage['input_tokens'] + read + write
            value['prompt_cache_hit_tokens'] = read
        else:
            # Do not silently report uncached tokens as complete when cache fields are absent.
            value['prompt_tokens'] = usage['input_tokens']
    if type(usage.get('output_tokens')) is int:
        value['completion_tokens'] = usage['output_tokens']
    return value


class Meter:
    def __init__(self, key, destination: Path, *, protocol='openai', upstream=None,
                 model='deepseek-flash', harness='thinkflow', session_id=None, remove_sdk_cap=False):
        if protocol not in ('openai', 'anthropic'):
            raise ValueError(protocol)
        self._key = key
        self.destination = Path(destination)
        self.protocol = protocol
        self.model = model
        self.harness = harness
        self.session_id = session_id or secrets.token_hex(16)
        self.user_agent = f'ThinkFlow-efficiency-benchmark/20261109 ({harness})'
        self.remove_sdk_cap = remove_sdk_cap
        self.upstream = (upstream or ('https://api.deepseek.com/v1' if protocol == 'openai'
                                     else 'https://api.deepseek.com/anthropic')).rstrip('/')
        self.token = secrets.token_urlsafe(32)
        self.requests = []
        self.started = time.monotonic()
        self.lock = threading.RLock()
        self.active = 0
        self.closed = False
        self.server = None

    def totals(self):
        result = openai_summary(self.requests)
        result['cache_creation_tokens'] = sum((r.get('raw_usage') or {}).get('cache_creation_input_tokens', 0)
                                             for r in self.requests)
        result['provider_input_excluding_cache_tokens'] = sum((r.get('raw_usage') or {}).get('input_tokens', 0)
                                                             for r in self.requests)
        result['protocol'] = self.protocol
        result['input_usage_complete'] = result['usage_complete'] and (
            self.protocol == 'openai' or result['cache_usage_complete'])
        if self.protocol == 'anthropic':
            result['usage_complete'] = result['usage_complete'] and result['input_usage_complete']
        result['totals_are_partial'] = not result['usage_complete']
        return result

    def save(self):
        with self.lock:
            self.destination.parent.mkdir(parents=True, exist_ok=True)
            value = {'model': self.model, 'protocol': self.protocol,
                     'upstream': self.upstream, 'harness': self.harness,
                     'identity': {'User-Agent': self.user_agent, 'x-opencode-session': self.session_id},
                     'generation': {'thinking': 'enabled', 'effort': 'high',
                                    'output_limit': None, 'request_limit': None, 'time_limit': None},
                     'requests': self.requests, 'totals': self.totals()}
            temporary = self.destination.with_suffix('.tmp')
            temporary.write_text(json.dumps(value, indent=2) + '\n', encoding='utf8')
            temporary.replace(self.destination)

    def prepare(self, body):
        """Retain exact system/user/tool messages. Explicitly disclose generation-field normalization."""
        before = {k: body[k] for k in ('model', 'thinking', 'output_config', 'reasoning_effort',
                                     'temperature', 'max_tokens', 'max_completion_tokens', 'max_output_tokens') if k in body}
        body['model'] = self.model
        if self.protocol == 'openai':
            if self.remove_sdk_cap:
                for field in ('max_tokens', 'max_completion_tokens', 'max_output_tokens'):
                    body.pop(field, None)
            elif any(k in body for k in ('max_tokens', 'max_completion_tokens', 'max_output_tokens')):
                raise ValueError('ThinkFlow must not send any output cap')
            body.update(thinking={'type': 'enabled'}, reasoning_effort='high', temperature=0)
            if body.get('stream'):
                body['stream_options'] = {**body.get('stream_options', {}), 'include_usage': True}
        else:
            # Claude's SDK supplies max_tokens even without a CLI budget. Omission delegates
            # to DeepSeek's provider default; never lower a supplied numeric limit.
            for field in ('max_tokens', 'max_completion_tokens', 'max_output_tokens'):
                body.pop(field, None)
            body['thinking'] = {'type': 'enabled'}
            body['output_config'] = {**body.get('output_config', {}), 'effort': 'high'}
            body['temperature'] = 0
        after = {k: body[k] for k in before.keys() | {'model', 'thinking', 'output_config', 'reasoning_effort', 'temperature'} if k in body}
        return before, after

    def observe(self, data, record):
        if self.protocol == 'openai':
            if isinstance(data.get('usage'), dict) and 'prompt_tokens' in data['usage']:
                record['raw_usage'] = data['usage']
                usage = dict(data['usage'])
                if 'prompt_cache_hit_tokens' not in usage and type(usage.get('prompt_cache_miss_tokens')) is int:
                    usage['prompt_cache_hit_tokens'] = usage['prompt_tokens'] - usage['prompt_cache_miss_tokens']
                record['usage'] = usage
            if data.get('model'):
                record['response_model'] = data['model']
            for choice in data.get('choices', []):
                if choice.get('finish_reason'):
                    record['finish_reason'] = choice['finish_reason']
        else:
            message = data.get('message') or {}
            usage = message.get('usage') if data.get('type') == 'message_start' else data.get('usage')
            if isinstance(usage, dict):
                record['raw_usage'] = {**record.get('raw_usage', {}), **usage}
                record['usage'] = normalize_anthropic_usage(record['raw_usage'])
            if message.get('model') or data.get('model'):
                record['response_model'] = message.get('model') or data['model']
            reason = (data.get('delta') or {}).get('stop_reason') or data.get('stop_reason')
            if reason:
                record['finish_reason'] = reason
        if data.get('type') == 'error':
            record['provider_error_type'] = (data.get('error') or {}).get('type', 'unknown')

    def observe_line(self, line, record):
        if line.startswith(b'data:'):
            try:
                self.observe(json.loads(line[5:].strip()), record)
            except ValueError:
                pass  # [DONE] and non-JSON keep-alives are normal SSE framing.

    def start(self):
        meter = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *_):
                pass

            def reply(self, status, message):
                payload = json.dumps({'error': {'message': message}}).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_POST(self):
                auth = self.headers.get('Authorization') == 'Bearer ' + meter.token or self.headers.get('x-api-key') == meter.token
                if not auth:
                    self.reply(401, 'Local meter token required')
                    return
                route = '/v1/chat/completions' if meter.protocol == 'openai' else '/v1/messages'
                if urlsplit(self.path).path != route:
                    self.reply(404, 'Only the selected provider completion route is accepted')
                    return
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                with meter.lock:
                    if meter.closed:
                        self.reply(503, 'Meter closed')
                        return
                    record = {'index': len(meter.requests) + 1, 'started_seconds': round(time.monotonic() - meter.started, 4),
                              'messages_sha256': digest(body.get('messages')), 'system_sha256': digest(body.get('system') if meter.protocol == 'anthropic'
                                 else [m for m in body.get('messages', []) if m.get('role') in ('system', 'developer')]),
                              'message_count': len(body.get('messages', [])), 'tool_count': len(body.get('tools', [])),
                              'stream': bool(body.get('stream')), 'finished': False}
                    meter.requests.append(record)
                    meter.active += 1
                headers_sent = False
                connected = True
                started = time.monotonic()
                try:
                    record['incoming_generation'], record['forwarded_generation'] = meter.prepare(body)
                    record['forwarded_messages_sha256'] = digest(body.get('messages'))
                    record['forwarded_system_sha256'] = digest(body.get('system') if meter.protocol == 'anthropic'
                        else [m for m in body.get('messages', []) if m.get('role') in ('system', 'developer')])
                    meter.save()
                    headers = {'Content-Type': 'application/json', 'User-Agent': meter.user_agent,
                               'x-opencode-session': meter.session_id}
                    record['identity'] = {'User-Agent': meter.user_agent, 'x-opencode-session': meter.session_id}
                    if meter.protocol == 'openai':
                        headers['Authorization'] = 'Bearer ' + meter._key
                    else:
                        headers['x-api-key'] = meter._key
                        for field in ('anthropic-version', 'anthropic-beta'):
                            if self.headers.get(field):
                                headers[field] = self.headers[field]
                    suffix = '/chat/completions' if meter.protocol == 'openai' else '/v1/messages'
                    with httpx.Client(timeout=httpx.Timeout(None, connect=20), trust_env=False, follow_redirects=False) as client:
                        with client.stream('POST', meter.upstream + suffix, headers=headers, json=body) as response:
                            record['http_status'] = response.status_code
                            try:
                                self.send_response(response.status_code)
                                self.send_header('Content-Type', response.headers.get('Content-Type', 'application/json'))
                                self.send_header('Connection', 'close')
                                self.end_headers()
                                headers_sent = True
                            except OSError:
                                connected = False
                                record['client_disconnected'] = True
                            pending = bytearray()
                            for chunk in response.iter_bytes():
                                if connected:
                                    try:
                                        self.wfile.write(chunk)
                                        self.wfile.flush()
                                    except OSError:
                                        connected = False
                                        record['client_disconnected'] = True
                                pending.extend(chunk)
                                if body.get('stream'):
                                    while b'\n' in pending:
                                        line, _, remainder = pending.partition(b'\n')
                                        pending[:] = remainder
                                        meter.observe_line(line, record)
                                meter.save()
                            if body.get('stream') and pending:
                                meter.observe_line(pending, record)
                            elif not body.get('stream') and pending:
                                meter.observe(json.loads(pending), record)
                except Exception as error:
                    record['transport_error'] = type(error).__name__
                    if not headers_sent:
                        try:
                            self.reply(502, 'Provider forwarding failed; see meter metadata')
                        except OSError:
                            pass
                finally:
                    self.close_connection = True
                    with meter.lock:
                        record.update(finished=True, seconds=round(time.monotonic() - started, 4))
                        meter.active -= 1
                        meter.save()
                    usage = record.get('usage', {})
                    print(json.dumps({'meter': record['index'], 'protocol': meter.protocol,
                                      'http': record.get('http_status'), 'input': usage.get('prompt_tokens'),
                                      'output': usage.get('completion_tokens')}), flush=True)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        suffix = '/v1' if self.protocol == 'openai' else ''
        return f'http://127.0.0.1:{self.server.server_port}' + suffix

    def finish(self):
        self.closed = True
        while self.active:
            time.sleep(.1)  # Drain billed responses even after downstream cancellation.
        self.server.shutdown()
        self.server.server_close()
        self.save()
        return self.totals()
