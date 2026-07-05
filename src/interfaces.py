"""Open interface adapters for network and generation tools."""

from __future__ import annotations

import asyncio
import html
import ipaddress
import json
import os
import re
import socket
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass
class WebInterfaceConfig:
    enabled: bool = True
    allowed_domains: list[str] = field(default_factory=lambda: ["*"])
    allow_private_hosts: bool = False
    timeout_seconds: float = 20.0
    max_chars: int = 12000
    search_max_results: int = 6

    @classmethod
    def from_config(cls, data: dict) -> "WebInterfaceConfig":
        web = ((data.get("interfaces", {}) or {}).get("web", {}) or {})
        return cls(
            enabled=bool(web.get("enabled", True)),
            allowed_domains=_as_list(web.get("allowed_domains"), ["*"]),
            allow_private_hosts=bool(web.get("allow_private_hosts", False)),
            timeout_seconds=float(web.get("timeout_seconds", 20.0)),
            max_chars=int(web.get("max_chars", 12000)),
            search_max_results=int(web.get("search_max_results", 6)),
        )


@dataclass
class ImageGenerationConfig:
    enabled: bool = False
    provider: str = "disabled"
    command: list[str] = field(default_factory=list)
    webhook_url: str = ""
    bearer_token_env: str = ""
    output_dir: str = ".thinkflow/images"
    timeout_seconds: float = 180.0
    allow_outside_cwd: bool = False

    @classmethod
    def from_config(cls, data: dict) -> "ImageGenerationConfig":
        image = ((data.get("interfaces", {}) or {}).get("image_generation", {}) or {})
        command = image.get("command", []) or []
        if isinstance(command, str):
            command = [command]
        return cls(
            enabled=bool(image.get("enabled", False)),
            provider=str(image.get("provider", "disabled") or "disabled"),
            command=list(command),
            webhook_url=str(image.get("webhook_url", "") or ""),
            bearer_token_env=str(image.get("bearer_token_env", "") or ""),
            output_dir=str(image.get("output_dir", ".thinkflow/images") or ".thinkflow/images"),
            timeout_seconds=float(image.get("timeout_seconds", 180.0)),
            allow_outside_cwd=bool(image.get("allow_outside_cwd", False)),
        )


@dataclass
class CustomToolConfig:
    name: str
    description: str
    parameters: dict
    command: list[str]
    enabled: bool = True
    timeout_seconds: float = 120.0

    @classmethod
    def from_config(cls, data: dict) -> "CustomToolConfig":
        command = data.get("command", []) or []
        if isinstance(command, str):
            command = [command]
        parameters = data.get("parameters") or {"type": "object", "properties": {}}
        if not isinstance(parameters, dict):
            parameters = {"type": "object", "properties": {}}
        return cls(
            name=str(data.get("name", "") or ""),
            description=str(data.get("description", "") or "Configured ThinkFlow tool"),
            parameters=parameters,
            command=list(command),
            enabled=bool(data.get("enabled", True)),
            timeout_seconds=float(data.get("timeout_seconds", 120.0)),
        )

    def valid_name(self) -> bool:
        return _valid_tool_name(self.name)


@dataclass
class InterfaceConfig:
    web: WebInterfaceConfig = field(default_factory=WebInterfaceConfig)
    image_generation: ImageGenerationConfig = field(default_factory=ImageGenerationConfig)
    custom_tools: list[CustomToolConfig] = field(default_factory=list)

    @classmethod
    def from_config(cls, data: dict) -> "InterfaceConfig":
        interfaces = data.get("interfaces", {}) or {}
        return cls(
            web=WebInterfaceConfig.from_config(data),
            image_generation=ImageGenerationConfig.from_config(data),
            custom_tools=[
                CustomToolConfig.from_config(item)
                for item in (interfaces.get("custom_tools", []) or [])
                if isinstance(item, dict)
            ],
        )


class ExternalInterfaces:
    """Network/search/image adapters exposed as optional tools."""

    def __init__(self, config: InterfaceConfig, cwd: str):
        self.config = config
        self.cwd = os.path.abspath(os.path.expanduser(cwd))

    async def web_search(self, tool_input: dict) -> str:
        web = self.config.web
        if not web.enabled:
            return "web_search 未启用。请在 config.interfaces.web.enabled 打开。"

        query = str(tool_input.get("query", "") or "").strip()
        if not query:
            return "缺少 query"
        max_results = _bounded_int(tool_input.get("max_results"), 1, 10, web.search_max_results)
        url = "https://lite.duckduckgo.com/lite/?q=" + urllib.parse.quote(query)

        try:
            text = await self._get_text(url, max_chars=200000, web=web)
        except Exception as exc:
            return f"web_search 失败: {exc}"

        results = _parse_duckduckgo_lite(text, max_results=max_results)
        if not results:
            return "没有解析到搜索结果。"

        lines = [f"query: {query}", "results:"]
        for index, item in enumerate(results, start=1):
            title = item["title"]
            href = item["url"]
            snippet = item.get("snippet", "")
            lines.append(f"{index}. {title}\n   {href}")
            if snippet:
                lines.append(f"   {snippet}")
        return "\n".join(lines)

    async def fetch_url(self, tool_input: dict) -> str:
        web = self.config.web
        if not web.enabled:
            return "fetch_url 未启用。请在 config.interfaces.web.enabled 打开。"

        url = str(tool_input.get("url", "") or "").strip()
        if not url:
            return "缺少 url"
        max_chars = _bounded_int(tool_input.get("max_chars"), 1000, 50000, web.max_chars)

        try:
            raw = await self._get_text(url, max_chars=max_chars, web=web)
        except Exception as exc:
            return f"fetch_url 失败: {exc}"

        text = _html_to_text(raw)
        if len(text) > max_chars:
            text = text[:max_chars] + "\n\n[THINKFLOW TRUNCATED] 内容已截断。"
        return f"url: {url}\n\n{text}"

    async def image_generate(self, tool_input: dict) -> str:
        image = self.config.image_generation
        prompt = str(tool_input.get("prompt", "") or "").strip()
        if not prompt:
            return "缺少 prompt"
        if not image.enabled or image.provider == "disabled":
            return (
                "image_generate 接口已存在，但尚未配置生成器。"
                "可在 config.interfaces.image_generation 中启用 provider=command 或 provider=webhook。"
            )

        output_path = str(tool_input.get("output_path", "") or "").strip()
        if not output_path:
            output_path = os.path.join(image.output_dir, "image.png")
        if not os.path.isabs(output_path):
            output_path = os.path.abspath(os.path.join(self.cwd, output_path))
        if not image.allow_outside_cwd and not _is_relative_to(output_path, self.cwd):
            return f"image_generate 拒绝写入 cwd 外路径: {output_path}"
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        payload = {
            "prompt": prompt,
            "output_path": output_path,
            "size": str(tool_input.get("size", "") or ""),
        }

        if image.provider == "command":
            return await self._run_image_command(image, payload)
        if image.provider == "webhook":
            return await self._call_image_webhook(image, payload)
        return f"未知 image_generation provider: {image.provider}"

    def custom_tool_handler(self, tool: CustomToolConfig):
        async def handler(tool_input: dict) -> str:
            return await self.run_custom_tool(tool, tool_input)

        return handler

    async def run_custom_tool(self, tool: CustomToolConfig, tool_input: dict) -> str:
        if not tool.enabled:
            return f"{tool.name} 已禁用"
        if not _valid_tool_name(tool.name):
            return f"custom tool 名称非法: {tool.name}"
        if not tool.command:
            return f"custom tool {tool.name} 缺少 command"
        payload = json.dumps(tool_input, ensure_ascii=False).encode("utf-8")
        try:
            proc = await asyncio.create_subprocess_exec(
                *tool.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.cwd,
                env=_safe_command_env(),
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(payload),
                timeout=tool.timeout_seconds,
            )
        except asyncio.TimeoutError:
            return f"{tool.name} 超时"
        except Exception as exc:
            return f"{tool.name} 启动失败: {exc}"

        out = stdout.decode("utf-8", errors="replace").strip()
        err = stderr.decode("utf-8", errors="replace").strip()
        if proc.returncode != 0:
            return f"{tool.name} failed(exit_code={proc.returncode})\n{err or out}"
        return out or f"{tool.name} ok"

    async def _get_text(self, url: str, *, max_chars: int, web: WebInterfaceConfig) -> str:
        async with httpx.AsyncClient(
            follow_redirects=False,
            timeout=httpx.Timeout(web.timeout_seconds, connect=10.0),
            headers={"user-agent": "ThinkFlow/0.4.9"},
        ) as client:
            current_url = url
            for _ in range(6):
                _validate_url(current_url, web)
                response = await client.get(current_url)
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("redirect 缺少 Location")
                    current_url = urllib.parse.urljoin(str(response.url), location)
                    continue
                response.raise_for_status()
                return response.text[:max_chars]
            raise ValueError("redirect 次数过多")

    async def _run_image_command(self, image: ImageGenerationConfig, payload: dict[str, Any]) -> str:
        if not image.command:
            return "image_generation.command 未配置"
        try:
            proc = await asyncio.create_subprocess_exec(
                *image.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.cwd,
                env=_safe_command_env(),
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(json.dumps(payload, ensure_ascii=False).encode("utf-8")),
                timeout=image.timeout_seconds,
            )
        except asyncio.TimeoutError:
            return "image_generate 命令超时"
        except Exception as exc:
            return f"image_generate 命令启动失败: {exc}"

        out = stdout.decode("utf-8", errors="replace").strip()
        err = stderr.decode("utf-8", errors="replace").strip()
        if proc.returncode != 0:
            return f"image_generate 失败(exit_code={proc.returncode})\n{err or out}"
        exists = os.path.exists(payload["output_path"])
        suffix = f"\noutput_path: {payload['output_path']}" if exists else ""
        return (out or "image_generate ok") + suffix

    async def _call_image_webhook(self, image: ImageGenerationConfig, payload: dict[str, Any]) -> str:
        if not image.webhook_url:
            return "image_generation.webhook_url 未配置"
        headers = {"content-type": "application/json"}
        if image.bearer_token_env:
            token = os.environ.get(image.bearer_token_env, "")
            if token:
                headers["authorization"] = f"Bearer {token}"
        try:
            async with httpx.AsyncClient(timeout=image.timeout_seconds) as client:
                response = await client.post(image.webhook_url, json=payload, headers=headers)
            if response.status_code >= 400:
                return f"image_generate webhook HTTP {response.status_code}: {response.text[:500]}"
            return response.text[:4000] or "image_generate webhook ok"
        except Exception as exc:
            return f"image_generate webhook 失败: {exc}"


def _bounded_int(value: Any, minimum: int, maximum: int, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def _as_list(value: Any, default: list[str]) -> list[str]:
    if value is None:
        return list(default)
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value]
    return list(default)


def _validate_url(url: str, web: WebInterfaceConfig) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("只允许 http/https URL")

    host = parsed.hostname or ""
    if not web.allow_private_hosts and _is_private_host(host):
        raise ValueError(f"默认拒绝访问本机或内网地址: {host}")

    allowed = web.allowed_domains or ["*"]
    if "*" in allowed:
        return
    host_l = host.lower()
    for domain in allowed:
        d = domain.lower().lstrip(".")
        if host_l == d or host_l.endswith("." + d):
            return
    raise ValueError(f"域名不在 allowlist: {host}")


def _is_private_host(host: str) -> bool:
    lower = host.lower()
    if lower in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        ip = ipaddress.ip_address(lower)
    except ValueError:
        try:
            infos = socket.getaddrinfo(lower, None)
        except socket.gaierror:
            return False
        for info in infos:
            address = info[4][0]
            try:
                resolved_ip = ipaddress.ip_address(address)
            except ValueError:
                continue
            if resolved_ip.is_private or resolved_ip.is_loopback or resolved_ip.is_link_local:
                return True
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local


def _parse_duckduckgo_lite(text: str, *, max_results: int) -> list[dict[str, str]]:
    results = []
    link_re = re.compile(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.I | re.S)
    for href, title_html in link_re.findall(text):
        title = _clean_html(title_html)
        href = html.unescape(href)
        if not title or "duckduckgo" in title.lower():
            continue
        url = _unwrap_duckduckgo_url(href)
        if not url.startswith(("http://", "https://")):
            continue
        if any(item["url"] == url for item in results):
            continue
        results.append({"title": title, "url": url})
        if len(results) >= max_results:
            break
    return results


def _unwrap_duckduckgo_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query)
    if "uddg" in query and query["uddg"]:
        return query["uddg"][0]
    if url.startswith("//"):
        return "https:" + url
    return url


def _html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<script.*?</script>", " ", raw)
    raw = re.sub(r"(?is)<style.*?</style>", " ", raw)
    raw = re.sub(r"(?is)<br\s*/?>", "\n", raw)
    raw = re.sub(r"(?is)</p\s*>", "\n\n", raw)
    raw = re.sub(r"(?is)<[^>]+>", " ", raw)
    raw = html.unescape(raw)
    raw = re.sub(r"[ \t\r\f\v]+", " ", raw)
    raw = re.sub(r"\n\s+", "\n", raw)
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    return raw.strip()


def _clean_html(value: str) -> str:
    return _html_to_text(value).replace("\n", " ").strip()


def _safe_command_env() -> dict[str, str]:
    keep = {
        "PATH",
        "PATHEXT",
        "SystemRoot",
        "WINDIR",
        "COMSPEC",
        "HOME",
        "USERPROFILE",
        "TEMP",
        "TMP",
        "SHELL",
        "TERM",
    }
    return {key: value for key, value in os.environ.items() if key in keep}


def _valid_tool_name(name: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", name))


def _is_relative_to(path: str, root: str) -> bool:
    try:
        common = os.path.commonpath([os.path.abspath(path), os.path.abspath(root)])
    except ValueError:
        return False
    return common == os.path.abspath(root)
