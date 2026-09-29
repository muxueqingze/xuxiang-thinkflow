"""Loopback-only streaming usage meter; no model messages or credentials in records."""
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


def summarize(requests):
    successful = [r for r in requests if r.get('http_status') == 200]
    def valid_usage(request):
        usage = request.get('usage')
        return isinstance(usage,dict) and all(type(usage.get(k)) is int and usage[k]>=0
                                              for k in ('prompt_tokens','completion_tokens'))
    known = [r for r in requests if valid_usage(r)]
    totals = dict(prompt_tokens=0, cached_tokens=0, uncached_tokens=0,
                  completion_tokens=0, reasoning_tokens=0, total_tokens=0)
    reasoning_reported = cache_reported = 0
    for request in known:
        usage = request['usage']
        prompt = int(usage.get('prompt_tokens', 0))
        completion = int(usage.get('completion_tokens', 0))
        cache = usage.get('prompt_cache_hit_tokens',
                         (usage.get('prompt_tokens_details') or {}).get('cached_tokens'))
        if type(cache) is int and 0 <= cache <= prompt:
            cache_reported += 1
            totals['cached_tokens'] += cache
            totals['uncached_tokens'] += prompt-cache
        reasoning = (usage.get('completion_tokens_details') or {}).get('reasoning_tokens')
        if type(reasoning) is int and 0 <= reasoning <= completion:
            reasoning_reported += 1
            totals['reasoning_tokens'] += reasoning
        totals['prompt_tokens'] += prompt
        totals['completion_tokens'] += completion
        totals['total_tokens'] += prompt + completion
    return {**totals, 'api_requests': len(requests), 'successful_requests': len(successful),
            'usage_reported_requests': len(known),
            'usage_complete': bool(requests) and len(known) == len(requests) and all(r.get('finished') and not r.get('transport_error') for r in requests),
            'unknown_usage_requests': len(requests)-len(known),
            'cache_usage_complete': bool(requests) and cache_reported==len(requests),
            'cache_reported_requests': cache_reported,
            'reasoning_reported_requests': reasoning_reported,
            'abandoned_requests': sum(bool(r.get('client_disconnected')) for r in requests),
            'failed_requests': sum(r.get('http_status', 0) != 200 for r in requests)}


class Meter:
    def __init__(self, key, destination: Path, *, model='deepseek-flash', thinking='enabled',
                 max_requests=24, max_seconds=300, max_tokens=8192, upstream='https://api.deepseek.com/v1'):
        self._key, self.destination = key, destination
        self.token = secrets.token_urlsafe(24)
        self.model, self.thinking = model, thinking
        self.max_requests, self.max_seconds, self.max_tokens = max_requests, max_seconds, max_tokens
        self.upstream = upstream
        self.started = time.monotonic()
        self.requests = []
        self.lock = threading.RLock()
        self.active = 0
        self.closed = False
        self.server = None

    def save(self):
        with self.lock:
            self.destination.parent.mkdir(parents=True, exist_ok=True)
            data = {'mode': 'streaming-with-bounded-usage-drain', 'model': self.model,
                    'thinking': self.thinking, 'reasoning_effort': 'high',
                    'requests': self.requests, 'totals': summarize(self.requests)}
            temporary = self.destination.with_suffix('.tmp')
            temporary.write_text(json.dumps(data, indent=2), encoding='utf-8')
            temporary.replace(self.destination)

    def start(self):
        meter = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *_):
                pass

            def json_reply(self, status, data):
                body = json.dumps(data).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if urlsplit(self.path).path == '/v1/models':
                    self.json_reply(200, {'object': 'list', 'data': [{'id': meter.model, 'object': 'model', 'owned_by': 'deepseek'}]})
                else:
                    self.json_reply(404, {'error': {'message': 'Unknown local meter route'}})

            def do_POST(self):
                if self.headers.get('Authorization') != 'Bearer ' + meter.token:
                    self.json_reply(401, {'error': {'message': 'Local benchmark token required'}})
                    return
                if urlsplit(self.path).path != '/v1/chat/completions':
                    self.json_reply(404, {'error': {'message': 'Only chat completions are accepted'}})
                    return
                size = int(self.headers.get('Content-Length', '0'))
                if size < 1 or size > 8_000_000:
                    self.json_reply(413, {'error': {'message': 'Request exceeds benchmark limit'}})
                    return
                try:
                    body = json.loads(self.rfile.read(size))
                    if not isinstance(body.get('messages'), list):
                        raise ValueError('messages')
                except (ValueError, TypeError):
                    self.json_reply(400, {'error': {'message': 'Invalid benchmark request'}})
                    return
                with meter.lock:
                    if meter.closed or len(meter.requests) >= meter.max_requests or time.monotonic() - meter.started >= meter.max_seconds:
                        self.json_reply(429, {'error': {'message': 'Benchmark request/time budget reached'}})
                        return
                    index = len(meter.requests) + 1
                    request = {'index': index, 'started_seconds': round(time.monotonic() - meter.started, 4),
                               'requested_model': str(body.get('model', '')), 'model': meter.model,
                               'message_count': len(body['messages']), 'tool_count': len(body.get('tools', [])),
                               'request_bytes': size, 'stream': bool(body.get('stream')), 'finished': False,
                               'system_sha256': hashlib.sha256(json.dumps([m for m in body['messages'] if m.get('role') in ('system','developer')], sort_keys=True).encode()).hexdigest()}
                    meter.requests.append(request)
                    meter.active += 1
                    meter.save()
                # Explicit, shared generation conditions. Never alter task or tool messages.
                body.update(model=meter.model, max_tokens=meter.max_tokens,
                            thinking={'type': meter.thinking}, reasoning_effort='high', temperature=0)
                body.pop('max_completion_tokens', None)
                if body.get('stream'):
                    body['stream_options'] = {'include_usage': True}
                started = time.monotonic()
                output = bytearray()
                connected = True
                headers_sent = False
                try:
                    with httpx.Client(timeout=httpx.Timeout(75, connect=20), trust_env=False, follow_redirects=False) as client:
                        with client.stream('POST', meter.upstream + '/chat/completions',
                                           headers={'Authorization': 'Bearer ' + meter._key, 'Content-Type': 'application/json'}, json=body) as response:
                            request['http_status'] = response.status_code
                            headers_sent = True
                            try:
                                self.send_response(response.status_code)
                                self.send_header('Content-Type', response.headers.get('Content-Type', 'application/json'))
                                self.send_header('Connection', 'close')
                                self.end_headers()
                            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                                connected = False
                                request['client_disconnected'] = True
                                request['disconnect_seconds'] = round(time.monotonic() - started, 4)
                            for chunk in response.iter_bytes():
                                if time.monotonic() - started > meter.max_seconds:
                                    raise TimeoutError('upstream total deadline')
                                if connected:
                                    try:
                                        self.wfile.write(chunk)
                                        self.wfile.flush()
                                    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                                        connected = False
                                        request['client_disconnected'] = True
                                        request['disconnect_seconds'] = round(time.monotonic() - started, 4)
                                output.extend(chunk)
                                if body.get('stream') and response.status_code == 200:
                                    while b'\n' in output:
                                        line, _, remainder = output.partition(b'\n')
                                        output[:] = remainder
                                        self.observe_line(line, request)
                                    if len(output) > 2_000_000:
                                        raise ValueError('oversized SSE event')
                                elif len(output) > 8_000_000:
                                    raise ValueError('oversized response')
                            if not body.get('stream') and response.status_code == 200:
                                data = json.loads(output)
                                if isinstance(data.get('usage'), dict):
                                    request['usage'] = data['usage']
                                request['response_model'] = data.get('model')
                            elif output:
                                self.observe_line(output, request)
                except Exception as exc:
                    request['transport_error'] = type(exc).__name__
                    if not headers_sent:
                        try:
                            self.json_reply(502, {'error': {'message': 'Benchmark upstream transport failed'}})
                        except OSError:
                            pass
                finally:
                    self.close_connection = True
                    with meter.lock:
                        request['finished'] = True
                        request['seconds'] = round(time.monotonic() - started, 4)
                        meter.active -= 1
                        meter.save()
                    u = request.get('usage') or {}
                    print(json.dumps({'run':meter.destination.parent.name,'meter': index, 'http': request.get('http_status'),
                                      'input': u.get('prompt_tokens'), 'output': u.get('completion_tokens'),
                                      'cache': u.get('prompt_cache_hit_tokens'), 'abandoned': not connected}), flush=True)

            @staticmethod
            def observe_line(line, request):
                if not line.startswith(b'data:'):
                    return
                try:
                    data = json.loads(line[5:].strip())
                except ValueError:
                    return
                if isinstance(data.get('usage'), dict) and 'prompt_tokens' in data['usage']:
                    request['usage'] = data['usage']
                if data.get('model'):
                    request['response_model'] = data['model']
                for choice in data.get('choices', []):
                    if choice.get('finish_reason'):
                        request['finish_reason'] = choice['finish_reason']

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return f'http://127.0.0.1:{self.server.server_port}/v1'

    def finish(self):
        self.closed = True
        # A cancelled downstream may still have a provider response to account for.
        # A last byte just before the per-request deadline can leave one read
        # outstanding for the 75-second read timeout. Do not publish early totals.
        deadline = time.monotonic() + self.max_seconds + 80
        while self.active and time.monotonic() < deadline:
            time.sleep(.1)
        self.server.shutdown()
        self.server.server_close()
        self.save()
        if self.active:
            raise RuntimeError('Meter still has unfinished requests; totals are not final')
        return summarize(self.requests)
