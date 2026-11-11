"""Inspect and edit harness settings without starting an agent or calling a model."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .configuration import (Configuration, get_setting, leaf_paths, load_configuration,
                            parse_setting_value, read_config, redact_config, set_setting,
                            unset_setting, validate_runtime_settings, write_config)
from .runtime_context import thinkflow_home


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog='thinkflow config', description='查看、修改全局或项目配置；不调用模型。')
    parser.add_argument('action', choices=('show', 'get', 'set', 'unset', 'path'), nargs='?', default='show')
    parser.add_argument('key', nargs='?', help='点分路径，例如 compaction.max_chars')
    parser.add_argument('value', nargs='?', help='JSON值，或普通文字')
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument('--project', action='store_true', help='只读写当前项目的 .thinkflow/config.json')
    scope.add_argument('--global', dest='global_scope', action='store_true', help='只读写用户配置（修改时默认）')
    parser.add_argument('--config', help='只读写指定配置文件')
    parser.add_argument('--cwd', default='.', help='目标项目目录')
    parser.add_argument('--sources', action='store_true', help='显示每个字段的配置来源')
    args = parser.parse_args(argv)
    cwd = Path(args.cwd).expanduser().resolve()
    if args.config:
        target = Path(args.config).expanduser().resolve()
    elif args.project:
        target = cwd / '.thinkflow' / 'config.json'
    else:
        target = thinkflow_home() / 'config.json'
    try:
        if args.action == 'path':
            print(target)
            return 0
        from .runtime import build_agent_config, effective_config
        if args.action in ('set', 'unset'):
            if not args.key or (args.action == 'set' and args.value is None):
                parser.error('set需要字段和值；unset需要字段')
            data = read_config(target) if target.exists() else {}
            if args.action == 'set':
                set_setting(data, args.key, parse_setting_value(args.value))
            else:
                unset_setting(data, args.key)
            validate_runtime_settings(data)
            resolved, _ = Configuration(cwd, [(str(target), data)]).resolve(use_env=False)
            build_agent_config(resolved, system_prompt='', cwd=str(cwd))
            write_config(target, data)
            print(f'已保存 {target}\n{args.key}: ' + ('恢复继承' if args.action == 'unset' else '已更新')
                  + '；下次启动生效。')
            return 0
        if args.config or args.project or args.global_scope:
            stack = Configuration(cwd, [(str(target), read_config(target))] if target.exists() else [])
            values, sources = stack.resolve(use_env=False)
        else:
            stack = load_configuration(cwd)
            values, sources = stack.resolve()
        values = effective_config(values, cwd=str(cwd))
        for key, _ in leaf_paths(values):
            if key not in sources:
                parts = key.split('.')
                inherited = next((sources['.'.join(parts[:end])] for end in range(len(parts) - 1, 0, -1)
                                  if '.'.join(parts[:end]) in sources), 'default')
                sources[key] = inherited
        shown = redact_config(values)
        if args.action == 'get':
            if not args.key:
                parser.error('get需要字段')
            shown = get_setting(shown, args.key)
            if args.sources:
                shown = {'value': shown, 'source': sources.get(args.key, 'mixed/default')}
        elif args.sources:
            shown = {'values': shown, 'sources': sources, 'files': [origin for origin, _ in stack.layers]}
        print(json.dumps(shown, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(2, f'配置错误: {error}\n')
