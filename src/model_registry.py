"""Provider profile and model discovery helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx


MODEL_PREFERENCE = [
    "glm-5.2",
    "glm-5.2-air",
    "glm-5.2-thinking",
    "kimi-k2",
    "kimi-latest",
    "kimi-k2-0711-preview",
    "deepseek-v4-flash",
    "deepseek-v3.1",
    "deepseek-chat",
]


@dataclass
class ModelCatalog:
    provider_name: str
    base_url: str
    models: list[str]
    source: str = "configured"
    error: str = ""


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if item]
    return [str(value)]


def openai_models_path(base_url: str, configured_path: str = "") -> str:
    if configured_path:
        return configured_path
    base_path = urlparse(base_url).path.rstrip("/")
    if base_path.endswith("/v1"):
        return "/models"
    return "/v1/models"


def extract_model_ids(payload: Any) -> list[str]:
    if isinstance(payload, dict):
        data = payload.get("data", payload.get("models", []))
    else:
        data = payload

    ids: list[str] = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, str):
                ids.append(item)
            elif isinstance(item, dict):
                model_id = item.get("id") or item.get("name") or item.get("model")
                if model_id:
                    ids.append(str(model_id))
    return sorted(dict.fromkeys(ids), key=str.lower)


def discover_openai_models(
    *,
    base_url: str,
    api_key: str,
    models_path: str = "",
    timeout_seconds: float = 20.0,
) -> ModelCatalog:
    path = openai_models_path(base_url, models_path)
    try:
        with httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(timeout_seconds, connect=15.0),
        ) as client:
            response = client.get(path)
            response.raise_for_status()
            models = extract_model_ids(response.json())
            return ModelCatalog("", base_url, models, source="discovered")
    except Exception as exc:
        return ModelCatalog("", base_url, [], source="discovered", error=str(exc))


def choose_model(models: list[str], preferred: list[str] | None = None) -> str:
    if not models:
        return ""
    normalized = {model.lower(): model for model in models}
    for candidate in preferred or MODEL_PREFERENCE:
        exact = normalized.get(candidate.lower())
        if exact:
            return exact
    for needle in ("glm", "kimi", "deepseek"):
        for model in models:
            if needle in model.lower():
                return model
    return models[0]


def merge_active_provider(config: dict, active_provider: str | None = None) -> dict:
    """Merge a named provider profile into the legacy flat config shape."""
    merged = dict(config)
    providers = _as_dict(config.get("providers"))
    selected = active_provider or config.get("active_provider")
    if not providers or not selected:
        return merged
    profile = _as_dict(providers.get(str(selected)))
    if not profile:
        return merged

    for key in (
        "provider",
        "base_url",
        "api_path",
        "api_key",
        "model",
        "thinking_budget",
        "max_tokens",
        "stream_options_include_usage",
        "enable_native_tools",
        "native_tools",
        "disabled_native_tools",
        "models",
        "model_preference",
        "model_discovery",
    ):
        if key in profile:
            value = profile[key]
            if value not in ("", None, [], {}) or not merged.get(key):
                merged[key] = value
    merged["active_provider"] = str(selected)
    return merged


def provider_catalog(config: dict, provider_name: str | None = None) -> ModelCatalog:
    providers = _as_dict(config.get("providers"))
    selected = provider_name or config.get("active_provider")
    if selected and selected in providers:
        profile = _as_dict(providers[selected])
        effective = dict(profile)
        if str(selected) == str(config.get("active_provider", "")):
            for key in ("base_url", "api_key", "models", "model_discovery"):
                value = config.get(key)
                if value not in ("", None, [], {}):
                    effective[key] = value
        base_url = str(effective.get("base_url", ""))
        models = _as_list(effective.get("models"))
        catalog = ModelCatalog(selected, base_url, models, source="configured")
        discovery = _as_dict(effective.get("model_discovery"))
        if bool(discovery.get("enabled", False)) and effective.get("api_key") and base_url:
            discovered = discover_openai_models(
                base_url=base_url,
                api_key=str(effective.get("api_key", "")),
                models_path=str(discovery.get("path", "")),
                timeout_seconds=float(discovery.get("timeout_seconds", 20.0)),
            )
            discovered.provider_name = selected
            return discovered if discovered.models else ModelCatalog(
                selected,
                base_url,
                models,
                source="configured",
                error=discovered.error,
            )
        return catalog

    models = _as_list(config.get("models"))
    return ModelCatalog(
        str(selected or "default"),
        str(config.get("base_url", "")),
        models,
        source="configured",
    )


def resolve_auto_model(config: dict) -> dict:
    merged = dict(config)
    model = str(merged.get("model", "") or "")
    if model and model.lower() != "auto":
        return merged

    catalog = provider_catalog(merged, merged.get("active_provider"))
    preferred = _as_list(merged.get("model_preference"))
    selected = choose_model(catalog.models, preferred or None)
    if selected:
        merged["model"] = selected
        merged["_resolved_models"] = catalog.models
        merged["_resolved_model_source"] = catalog.source
        if catalog.error:
            merged["_resolved_model_error"] = catalog.error
    return merged
