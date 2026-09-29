"""Explicit, bounded connection checks. Never echo provider bodies or keys."""
from __future__ import annotations

import asyncio
import time
from urllib.parse import urlsplit

import httpx

from .model_registry import extract_model_ids, openai_models_path


def _settings(config):
    base = str(config.get('base_url', '')).rstrip('/')
    url = urlsplit(base)
    if url.scheme not in ('https', 'http') or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError('请先保存有效的模型端点。')
    headers = {'Content-Type': 'application/json'}
    key = config.get('api_key', '')
    if config.get('provider') == 'anthropic':
        headers.update({'anthropic-version': '2023-06-01'})
        if key:
            headers['x-api-key'] = key
    elif key:
        headers['Authorization'] = 'Bearer ' + key
    return base, headers


async def _request(config, method, path, body=None):
    base, headers = _settings(config)
    try:
        async with asyncio.timeout(22):
            async with httpx.AsyncClient(base_url=base, headers=headers,
                                         timeout=httpx.Timeout(20, connect=10), follow_redirects=False) as client:
                async with client.stream(method, path, json=body) as response:
                    if response.status_code != 200:
                        descriptions = {401: '密钥无效或已过期', 403: '没有模型访问权限',
                                        402: '账号余额不足', 404: '端点、接口路径或模型不存在',
                                        429: '服务限流，请稍后重试'}
                        hint = descriptions.get(response.status_code, '服务暂时不可用，请检查端点')
                        raise ValueError(f'{hint}（HTTP {response.status_code}）。')
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 1024 * 1024:
                            raise ValueError('诊断响应过大，已停止读取。')
                    import json
                    try:
                        return json.loads(raw)
                    except (ValueError, UnicodeError):
                        raise ValueError('服务未返回有效 JSON，请检查接口地址。') from None
    except (TimeoutError, httpx.TimeoutException):
        raise ValueError('连接超时，请检查网络和端点后重试。') from None
    except httpx.TransportError:
        raise ValueError('无法连接模型服务，请检查网络、证书和端点。') from None


async def catalog(config):
    base, _ = _settings(config)
    if config.get('provider') == 'anthropic':
        path = '/models' if urlsplit(base).path.endswith('/v1') else '/v1/models'
    else:
        path = openai_models_path(base)
    started = time.monotonic()
    payload = await _request(config, 'GET', path)
    models = extract_model_ids(payload)
    if not models:
        raise ValueError('服务没有返回模型列表；仍可手动填写模型名称。')
    return {'models': models[:500], 'latency_ms': round((time.monotonic() - started) * 1000)}


async def probe(config):
    base, _ = _settings(config)
    model = config.get('model', '')
    if not model:
        raise ValueError('请先保存模型名称。')
    anthropic = config.get('provider') == 'anthropic'
    suffix = '/messages' if anthropic else '/chat/completions'
    path = config.get('api_path') or (suffix if urlsplit(base).path.endswith('/v1') else '/v1' + suffix)
    body = {'model': model, 'max_tokens': 64, 'stream': False,
            'messages': [{'role': 'user', 'content': 'Reply with OK only.'}]}
    if not anthropic and (urlsplit(base).hostname == 'api.deepseek.com' or model.startswith('deepseek-')):
        body['thinking'] = {'type': 'disabled'}
    started = time.monotonic()
    payload = await _request(config, 'POST', path, body)
    if not isinstance(payload, dict) or not (payload.get('content') if anthropic else payload.get('choices')):
        raise ValueError('已连接，但服务没有返回有效模型回复。')
    usage = payload.get('usage', {})
    return {'ok': True, 'model': payload.get('model', model),
            'latency_ms': round((time.monotonic() - started) * 1000),
            'usage': {key: value for key, value in usage.items()
                      if isinstance(value, (int, float)) and not isinstance(value, bool)}}
