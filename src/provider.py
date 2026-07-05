"""
ThinkFlow Provider — API Provider 适配

P0 支持 Anthropic 和 OpenAI 兼容格式。
"""

import json
from dataclasses import dataclass, field
from typing import Optional

import httpx


@dataclass
class ProviderProfileConfig:
    """Named provider profile summary used by runtime model switching."""
    name: str = ""
    format: str = "openai"
    base_url: str = ""
    api_path: str = ""
    api_key: str = ""
    model: str = ""
    thinking_budget: int = 0
    max_tokens: int = 100000
    stream_options_include_usage: bool = False
    enable_native_tools: bool = True
    native_tools: list[str] = field(default_factory=list)
    disabled_native_tools: list[str] = field(default_factory=list)
    models: list[str] = field(default_factory=list)
    model_source: str = ""
    model_discovery_enabled: bool = False
    model_discovery_path: str = ""


@dataclass
class ProviderConfig:
    """Provider 配置"""
    profile_name: str = ""
    base_url: str = ""
    api_path: str = ""
    api_key: str = ""
    model: str = ""
    format: str = "openai"  # "anthropic" or "openai"
    thinking_budget: int = 0  # provider-specific thinking budget, disabled by default
    max_tokens: int = 100000
    stream_options_include_usage: bool = False
    enable_native_tools: bool = True
    native_tools: list[str] = field(default_factory=list)
    disabled_native_tools: list[str] = field(default_factory=list)
    available_models: list[str] = field(default_factory=list)
    model_source: str = ""
    model_discovery_enabled: bool = False
    model_discovery_path: str = ""
    provider_profiles: list[ProviderProfileConfig] = field(default_factory=list)


class AnthropicProvider:
    """Anthropic API 适配"""

    def __init__(self, config: ProviderConfig):
        self.config = config
        self.client = httpx.AsyncClient(
            base_url=config.base_url,
            headers={
                "x-api-key": config.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            timeout=httpx.Timeout(300.0, connect=30.0),
        )

    async def stream_create(
        self,
        messages: list[dict],
        system: str,
        tools: Optional[list[dict]] = None,
    ) -> httpx.Response:
        """创建流式请求，返回 httpx Response（用于流式读取）。"""
        body = {
            "model": self.config.model,
            "max_tokens": self.config.max_tokens,
            "messages": messages,
            "stream": True,
        }

        if self.config.thinking_budget > 0:
            body["thinking"] = {
                "type": "enabled",
                "budget_tokens": self.config.thinking_budget,
            }

        if system:
            body["system"] = system

        if tools:
            body["tools"] = tools

        return await self.client.send(
            self.client.build_request(
                "POST",
                self.config.api_path or "/v1/messages",
                json=body,
            ),
            stream=True,
        )

    async def close(self):
        await self.client.aclose()


class OpenAIProvider:
    """OpenAI 兼容 API 适配（也适用于 fake-z 等代理）"""

    def __init__(self, config: ProviderConfig):
        self.config = config
        self.client = httpx.AsyncClient(
            base_url=config.base_url,
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "content-type": "application/json",
            },
            timeout=httpx.Timeout(300.0, connect=30.0),
        )

    async def stream_create(
        self,
        messages: list[dict],
        system: str,
        tools: Optional[list[dict]] = None,
    ) -> httpx.Response:
        """创建流式请求。"""
        # OpenAI 格式：system 放 messages 开头
        full_messages = []
        if system:
            full_messages.append({"role": "system", "content": system})
        full_messages.extend(messages)

        body = {
            "model": self.config.model,
            "max_tokens": self.config.max_tokens,
            "messages": full_messages,
            "stream": True,
        }

        if self.config.stream_options_include_usage:
            body["stream_options"] = {"include_usage": True}

        if tools and self.config.enable_native_tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        return await self.client.send(
            self.client.build_request(
                "POST",
                self.config.api_path or "/v1/chat/completions",
                json=body,
            ),
            stream=True,
        )

    async def close(self):
        await self.client.aclose()


def create_provider(config: ProviderConfig):
    """根据 format 创建 provider。"""
    if config.format == "anthropic":
        return AnthropicProvider(config)
    elif config.format == "openai":
        return OpenAIProvider(config)
    else:
        raise ValueError(f"未知 format: {config.format}")
