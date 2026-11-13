"""Public runtime assembly: shared by CLI, desktop and embedded hosts."""
from copy import deepcopy
from dataclasses import asdict
from .agent_loop import AgentConfig, AgentLoop
from .compaction import CompactionConfig
from .configuration import validate_runtime_settings
from .interfaces import InterfaceConfig
from .model_registry import choose_model
from .prompts import BUILTIN_SYSTEM_PROMPT
from .provider import ProviderConfig, ProviderProfileConfig
from .runtime_context import build_context_prompt
from .security import SecurityPolicy, normalize_security_profile
from .skills import SkillConfig


def resolve_system_prompt(
    config: dict,
    system_prompt_path: str = None,
    use_builtin: bool = False,
    disable_system_prompt: bool = False,
    cwd: str = ".",
) -> str:
    """Resolve optional engineering/user guidance and workspace context.

    AgentLoop injects its own protocol independently. Custom content replaces
    only the recommended engineering guidance, never the execution contract.
    """
    disabled = disable_system_prompt or bool(config.get("disable_system_prompt", False))
    parts = []
    if not disabled:
        if system_prompt_path:
            with open(system_prompt_path, "r", encoding="utf-8") as f:
                parts.append(f.read())
        elif config.get("system_prompt"):
            parts.append(str(config["system_prompt"]))
        elif use_builtin or config.get("use_builtin_system_prompt", True):
            parts.append(BUILTIN_SYSTEM_PROMPT)

    context_config = config.get("context", {}) or {}
    if bool(context_config.get("enabled", True)):
        context_text = build_context_prompt(
            cwd,
            extra_paths=_config_list(context_config.get("files")),
            max_chars=int(context_config.get("max_chars", 80_000)),
        )
        if context_text:
            parts.append(context_text)

    return "\n\n".join(part for part in parts if part and part.strip())


def _as_config_dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _config_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    return bool(value)


def _config_list(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if item]
    return [str(value)]


def build_provider_profiles(config: dict) -> list[ProviderProfileConfig]:
    providers = _as_config_dict(config.get("providers"))
    active = str(config.get("active_provider", "") or "")
    profiles: list[ProviderProfileConfig] = []

    if providers:
        for name, raw_profile in providers.items():
            profile = _as_config_dict(raw_profile)
            effective = dict(profile)
            if str(name) == active:
                for key in (
                    "provider",
                    "base_url",
                    "api_path",
                    "api_key",
                    "model",
                    "thinking_budget",
                    "thinking_mode",
                    "reasoning_effort",
                    "max_tokens",
                    "stream_options_include_usage",
                    "enable_native_tools",
                    "native_tools",
                    "disabled_native_tools",
                    "model_discovery",
                ):
                    value = config.get(key)
                    if (key in ("max_tokens", "native_tools", "disabled_native_tools") and key in config) or value not in ("", None, [], {}):
                        effective[key] = value
                models = _config_list(config.get("_resolved_models") or config.get("models") or profile.get("models"))
                source = str(config.get("_resolved_model_source", "") or "configured")
            else:
                if not effective.get("api_key") and config.get("api_key"):
                    effective["api_key"] = config.get("api_key")
                models = _config_list(profile.get("models"))
                source = "configured"

            discovery = _as_config_dict(effective.get("model_discovery"))
            model = str(effective.get("model", "") or "")
            if model.lower() == "auto":
                model = choose_model(models)
            profiles.append(
                ProviderProfileConfig(
                    name=str(name),
                    format=str(effective.get("provider", "openai") or "openai"),
                    base_url=str(effective.get("base_url", "") or ""),
                    api_path=str(effective.get("api_path", "") or ""),
                    api_key=str(effective.get("api_key", "") or ""),
                    model=model,
                    thinking_budget=int(effective.get("thinking_budget", 0) or 0),
                    thinking_mode=effective.get('thinking_mode', 'disabled'),
                    reasoning_effort=effective.get('reasoning_effort', 'high'),
                    max_tokens=effective.get("max_tokens"),
                    stream_options_include_usage=_config_bool(effective.get("stream_options_include_usage"), False),
                    enable_native_tools=_config_bool(effective.get("enable_native_tools"), False),
                    native_tools=_config_list(effective.get("native_tools")),
                    disabled_native_tools=_config_list(effective.get("disabled_native_tools")),
                    models=models,
                    model_source=source,
                    model_discovery_enabled=bool(discovery.get("enabled", False)),
                    model_discovery_path=str(discovery.get("path", "") or ""),
                )
            )

    if not profiles:
        profiles.append(
            ProviderProfileConfig(
                name=active or "default",
                format=str(config.get("provider", "openai") or "openai"),
                base_url=str(config.get("base_url", "") or ""),
                api_path=str(config.get("api_path", "") or ""),
                api_key=str(config.get("api_key", "") or ""),
                model=str(config.get("model", "") or ""),
                thinking_budget=int(config.get("thinking_budget", 0) or 0),
                thinking_mode=config.get('thinking_mode', 'disabled'),
                reasoning_effort=config.get('reasoning_effort', 'high'),
                max_tokens=config.get("max_tokens"),
                stream_options_include_usage=bool(config.get("stream_options_include_usage", False)),
                enable_native_tools=bool(config.get("enable_native_tools", False)),
                native_tools=_config_list(config.get("native_tools")),
                disabled_native_tools=_config_list(config.get("disabled_native_tools")),
                models=_config_list(config.get("_resolved_models") or config.get("models")),
                model_source=str(config.get("_resolved_model_source", "") or "configured"),
                model_discovery_enabled=bool((config.get("model_discovery", {}) or {}).get("enabled", False)),
                model_discovery_path=str((config.get("model_discovery", {}) or {}).get("path", "") or ""),
            )
        )
    return profiles


def build_agent_config(config: dict, system_prompt: str | None = None, cwd: str = None) -> AgentConfig:
    validate_runtime_settings(config)
    effective_cwd = cwd or config.get("cwd", ".")
    if system_prompt is None:
        system_prompt = resolve_system_prompt(config, cwd=effective_cwd)
    provider_config = ProviderConfig(
        profile_name=str(config.get("active_provider", "") or ""),
        base_url=config.get("base_url", ""),
        api_path=config.get("api_path", ""),
        api_key=config.get("api_key", ""),
        model=config.get("model", ""),
        format=config.get("provider", "openai"),
        thinking_budget=config.get("thinking_budget", 0),
        thinking_mode=config.get("thinking_mode", "disabled"),
        reasoning_effort=config.get("reasoning_effort", "high"),
        max_tokens=config.get("max_tokens"),
        stream_options_include_usage=bool(config.get("stream_options_include_usage", False)),
        enable_native_tools=bool(config.get("enable_native_tools", False)),
        native_tools=_config_list(config.get("native_tools")),
        disabled_native_tools=_config_list(config.get("disabled_native_tools")),
        available_models=_config_list(config.get("_resolved_models") or config.get("models")),
        model_source=config.get("_resolved_model_source", ""),
        model_discovery_enabled=bool((config.get("model_discovery", {}) or {}).get("enabled", False)),
        model_discovery_path=str((config.get("model_discovery", {}) or {}).get("path", "")),
        provider_profiles=build_provider_profiles(config),
    )

    agent_config = AgentConfig(
        provider=provider_config,
        system_prompt=system_prompt,
        cwd=effective_cwd,
        verbose=config.get("verbose", False),
        max_read_chars=config.get("max_read_chars", 200_000),
        security=SecurityPolicy.from_config(config, effective_cwd),
        compaction=CompactionConfig.from_config(config),
        interfaces=InterfaceConfig.from_config(config),
        skills=SkillConfig.from_config(config),
        allow_legacy_tool_tags=bool((config.get("tool_protocol", {}) or {}).get("allow_legacy_tags", False)),
        max_retries=int(config.get("max_retries", 2)),
        retry_backoff_seconds=float(config.get("retry_backoff_seconds", 1.0)),
        max_auto_continues=(None if config.get("max_auto_continues", 8) is None else int(config.get("max_auto_continues", 8))),
        delivery_verify=bool(config.get("delivery_verify", False)),
        auto_verify_runnable_artifacts=bool(config.get("auto_verify_runnable_artifacts", False)),
        max_delivery_fix_attempts=int(config.get("max_delivery_fix_attempts", 3)),
        max_run_turns=(None if config.get("max_run_turns", 40) is None else int(config.get("max_run_turns", 40))),
        max_run_seconds=(None if config.get("max_run_seconds", 1800) is None else float(config.get("max_run_seconds", 1800))),
        max_consecutive_failures=config.get("max_consecutive_failures"),
    )

    return agent_config


def create_agent(config: dict, system_prompt: str | None = None, cwd: str = None, *,
                 event_sink=None, approval_handler=None, session_store=None, steering_source=None) -> AgentLoop:
    agent = AgentLoop(build_agent_config(config, system_prompt, cwd), event_sink=event_sink,
                      approval_handler=approval_handler, steering_source=steering_source)
    if session_store is not None:
        session_store.bind(agent)
    return agent


def effective_config(config: dict, cwd: str = None) -> dict:
    """Preview defaults through actual runtime construction, without loading context or opening HTTP."""
    runtime = build_agent_config(config, system_prompt='', cwd=cwd)
    result = deepcopy(config)
    provider = asdict(runtime.provider)
    for key in ('base_url', 'api_path', 'api_key', 'model', 'thinking_budget', 'thinking_mode',
                'reasoning_effort', 'max_tokens', 'stream_options_include_usage',
                'enable_native_tools', 'native_tools', 'disabled_native_tools'):
        result[key] = provider[key]
    result['provider'] = provider['format']
    result['active_provider'] = provider['profile_name']
    for key in ('max_read_chars', 'verbose', 'max_retries', 'retry_backoff_seconds', 'max_auto_continues',
                'delivery_verify', 'auto_verify_runnable_artifacts', 'max_delivery_fix_attempts',
                'max_run_turns', 'max_run_seconds', 'max_consecutive_failures'):
        result[key] = getattr(runtime, key)
    result['security'] = {**asdict(runtime.security),
                          'profile': normalize_security_profile((config.get('security') or {}).get('profile', 'balanced'))}
    result['compaction'] = asdict(runtime.compaction)
    result['interfaces'] = {**asdict(runtime.interfaces), 'skills': asdict(runtime.skills)}
    result['tool_protocol'] = {'allow_legacy_tags': runtime.allow_legacy_tool_tags}
    result['context'] = {'enabled': True, 'files': [], 'max_chars': 80000, **(config.get('context') or {})}
    result.setdefault('system_prompt', '')
    result.setdefault('use_builtin_system_prompt', True)
    result.setdefault('disable_system_prompt', False)
    return result
