"""Layered configuration shared by command-line tools and embedded hosts."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import tempfile

from .model_registry import merge_active_provider
from .runtime_context import thinkflow_home


ENV_DEFAULTS = {
    'api_key': ('THINKFLOW_API_KEY', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY'),
    'base_url': ('THINKFLOW_BASE_URL', 'OPENAI_BASE_URL', 'ANTHROPIC_BASE_URL'),
    'api_path': ('THINKFLOW_API_PATH', 'OPENAI_API_PATH', 'ANTHROPIC_API_PATH'),
    'model': ('THINKFLOW_MODEL', 'OPENAI_MODEL', 'ANTHROPIC_MODEL'),
    'provider': ('THINKFLOW_PROVIDER',),
    'active_provider': ('THINKFLOW_ACTIVE_PROVIDER',),
}
SECRET_FIELDS = {'api_key', 'password', 'secret', 'token', 'access_token', 'refresh_token', 'authorization'}
RUNTIME_CONFIG_FIELDS = (
    'compaction', 'context', 'interfaces', 'tool_protocol', 'max_read_chars', 'max_retries',
    'retry_backoff_seconds', 'max_auto_continues', 'max_consecutive_failures', 'delivery_verify',
    'auto_verify_runnable_artifacts', 'max_delivery_fix_attempts', 'native_tools', 'disabled_native_tools',
)


def validate_runtime_settings(config: dict) -> None:
    if 'max_read_chars' in config and type(config['max_read_chars']) is not int:
        raise ValueError('max_read_chars must be an integer')
    for key in ('compaction', 'context', 'interfaces', 'tool_protocol'):
        if key in config and config[key] is not None and not isinstance(config[key], dict):
            raise ValueError(f'{key} must be a configuration object or null')
    for key in ('native_tools', 'disabled_native_tools'):
        if key in config:
            value = config[key]
            if value is not None and not isinstance(value, str) and not (
                    isinstance(value, list) and all(isinstance(item, str) for item in value)):
                raise ValueError(f'{key} must be a tool name or a list of tool names')
    for key in ('enable_native_tools', 'use_builtin_system_prompt', 'disable_system_prompt',
                'delivery_verify', 'auto_verify_runnable_artifacts', 'stream_options_include_usage', 'verbose'):
        if key in config and type(config[key]) is not bool:
            raise ValueError(f'{key} must be boolean')
    context = config.get('context') or {}
    files = context.get('files')
    if files is not None and not isinstance(files, str) and not (
            isinstance(files, list) and all(isinstance(item, str) for item in files)):
        raise ValueError('context.files must be a path or a list of paths')
    if 'max_chars' in context:
        int(context['max_chars'])
    for section in ('context', 'compaction'):
        value = config.get(section) or {}
        if 'enabled' in value and type(value['enabled']) is not bool:
            raise ValueError(f'{section}.enabled must be boolean')
    for key in ('max_input_tokens', 'keep_recent_tokens'):
        value = (config.get('compaction') or {}).get(key, 0)
        if type(value) is not int or value < 0:
            raise ValueError(f'compaction.{key} must be a non-negative integer')


def merge_settings(base: dict, override: dict) -> dict:
    """Objects merge recursively; arrays and scalar values replace, including null."""
    result = deepcopy(base)
    for key, value in override.items():
        result[key] = (merge_settings(result[key], value)
                       if isinstance(value, dict) and isinstance(result.get(key), dict)
                       else deepcopy(value))
    return result


def read_config(path: str | Path) -> dict:
    path = Path(path).expanduser()
    with path.open(encoding='utf-8-sig') as stream:
        data = json.load(stream)
    if not isinstance(data, dict):
        raise ValueError(f'{path}: configuration must be a JSON object')
    return data


def looks_like_thinkflow_config(data) -> bool:
    return isinstance(data, dict) and bool(set(data) & {
        'api_key', 'base_url', 'model', 'provider', 'providers', 'active_provider',
        'thinking_budget', 'max_tokens', 'security', 'compaction',
    })


def apply_env_defaults(config: dict) -> dict:
    result = deepcopy(config)
    for key, names in ENV_DEFAULTS.items():
        if not result.get(key):
            for name in names:
                if value := os.environ.get(name):
                    result[key] = value
                    break
    return result


def leaf_paths(data: dict, prefix: str = ''):
    for key, value in data.items():
        path = f'{prefix}.{key}' if prefix else key
        if isinstance(value, dict) and value:
            yield from leaf_paths(value, path)
        else:
            yield path, value


def get_setting(data: dict, key: str):
    value = data
    for part in key.split('.'):
        value = value[part]
    return value


def set_setting(data: dict, key: str, value) -> None:
    parts = key.split('.')
    if any(not part for part in parts):
        raise ValueError('setting path cannot contain empty components')
    target = data
    for part in parts[:-1]:
        if part not in target:
            target[part] = {}
        if not isinstance(target[part], dict):
            raise ValueError(f'{part} is not a configuration object')
        target = target[part]
    target[parts[-1]] = value


def unset_setting(data: dict, key: str) -> None:
    parts = key.split('.')
    target = data
    parents = []
    for part in parts[:-1]:
        parents.append((target, part))
        target = target[part]
    del target[parts[-1]]
    for parent, part in reversed(parents):
        if parent[part]:
            break
        del parent[part]


def parse_setting_value(text: str):
    """CLI literals use JSON; ordinary unquoted words remain strings."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        if text.startswith(('[', '{', '"')):
            raise ValueError('invalid JSON setting value') from None
        return text


def redact_config(value):
    if isinstance(value, dict):
        return {key: ('[configured]' if key.lower() in SECRET_FIELDS and item else redact_config(item))
                for key, item in value.items()}
    if isinstance(value, list):
        return [redact_config(item) for item in value]
    return value


def write_config(path: str | Path, data: dict) -> None:
    """Replace the selected configuration after the caller has validated it."""
    path = Path(path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f'.{path.name}.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@dataclass
class Configuration:
    cwd: Path
    layers: list[tuple[str, dict]] = field(default_factory=list)

    def raw(self) -> dict:
        merged = {}
        for _, layer in self.layers:
            merged = merge_settings(merged, layer)
        return merged

    def resolve(self, overrides: dict | None = None, *, use_env: bool = True) -> tuple[dict, dict[str, str]]:
        """Resolve the chosen provider at each layer, then apply invocation overrides."""
        raw = merge_settings(self.raw(), overrides or {})
        selected = (overrides or {}).get('active_provider', raw.get('active_provider'))
        selected_from_env = False
        if not selected and use_env and 'active_provider' not in (overrides or {}):
            selected = os.environ.get('THINKFLOW_ACTIVE_PROVIDER')
            selected_from_env = bool(selected)
        merged, sources = {}, {}
        layers = [*self.layers, *([('command line', overrides)] if overrides else [])]
        for origin, layer in layers:
            resolved_layer = merge_active_provider(layer, selected)
            merged = merge_settings(merged, resolved_layer)
            for key, _ in leaf_paths(resolved_layer):
                if key == 'active_provider' and key not in layer:
                    continue
                for previous in list(sources):
                    if previous.startswith(key + '.'):
                        del sources[previous]
                sources[key] = origin
        if selected is not None:
            merged['active_provider'] = selected
        if selected_from_env:
            sources['active_provider'] = 'env:THINKFLOW_ACTIVE_PROVIDER'
        if use_env:
            filled = apply_env_defaults(merged)
            for key, names in ENV_DEFAULTS.items():
                if filled.get(key) != merged.get(key):
                    sources[key] = 'env:' + next(name for name in names if os.environ.get(name))
            merged = filled
        if overrides:
            merged = merge_settings(merged, overrides)
            for key, _ in leaf_paths(overrides):
                sources[key] = 'command line'
        return merged, sources


def load_configuration(cwd: str | Path = '.', *, config_path: str | Path | None = None,
                       home: str | Path | None = None) -> Configuration:
    """Explicit files are isolated; otherwise load user, legacy project, then project settings."""
    cwd = Path(cwd).expanduser().resolve()
    result = Configuration(cwd)
    if config_path is not None:
        path = Path(config_path).expanduser().resolve()
        result.layers.append((str(path), read_config(path)))
        return result
    global_path = Path(home) / 'config.json' if home is not None else thinkflow_home() / 'config.json'
    if global_path.exists():
        result.layers.append((str(global_path.resolve()), read_config(global_path)))
    legacy = cwd / 'config.json'
    if legacy.exists() and legacy.resolve() != global_path.resolve():
        # Root config.json may belong to the user's application. Keep its existing
        # opt-in detection; the dedicated .thinkflow file is always strict.
        try:
            data = read_config(legacy)
        except (json.JSONDecodeError, ValueError):
            data = {}
        if looks_like_thinkflow_config(data):
            result.layers.append((str(legacy), data))
    project = cwd / '.thinkflow' / 'config.json'
    if project.exists() and project.resolve() != global_path.resolve():
        result.layers.append((str(project), read_config(project)))
    return result
