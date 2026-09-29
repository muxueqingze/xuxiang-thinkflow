"""Opt-in diagnostics: runtime-state placement or native file-tool availability.

This experiment does not change the production AgentLoop or the frozen suite.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))


def attached_status(agent):
    messages = copy.deepcopy(agent.messages)
    status = agent.build_runtime_status_message()['content']
    if messages and messages[-1].get('role') in ('tool', 'user') and isinstance(messages[-1].get('content'), str):
        messages[-1]['content'] += '\n\n' + status
        return messages
    return [*messages, {'role': 'user', 'content': status}]


def files_available(agent):
    provider = agent.config.provider
    if not provider.enable_native_tools:
        return set()
    allow = {name for name in provider.native_tools if name}
    deny = {name for name in provider.disabled_native_tools if name}
    return (allow or {schema['name'] for schema in agent.tool_registry.schemas()}) - deny


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--mode', choices=('message', 'attached', 'files_available'), default='attached')
    parser.add_argument('--name', default='debug-attached-status')
    parser.add_argument('--task', choices=('pricing_refactor', 'durable_inbox'), required=True)
    parser.add_argument('--repetitions', type=int, choices=(1, 2), default=2)
    args = parser.parse_args()
    if args.worker:
        import asyncio
        from src.agent_loop import AgentLoop
        from thinkflow_worker import main as worker
        if args.mode == 'attached':
            AgentLoop._messages_with_runtime_status = attached_status
        elif args.mode == 'files_available':
            AgentLoop._enabled_native_tool_names = files_available
        asyncio.run(worker())
        return
    if not args.run:
        parser.print_help()
        return
    if not args.name.replace('-', '').replace('_', '').isalnum():
        parser.error('Use a simple experiment name')
    import run as runner
    directory = ROOT / 'artifacts/benchmark-20260929' / args.name
    directory.mkdir(parents=True, exist_ok=True)
    runner.freeze_manifest(directory, False)
    experiment = {'mode': args.mode, 'task': args.task, 'repetitions': args.repetitions,
                  'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  'only_variable': ('native file schemas visible from first request; status, generation, prompt and tasks unchanged'
                                    if args.mode == 'files_available' else
                                    'runtime state placement; generation, tools and tasks unchanged')}
    experiment_path = directory / 'ablation.json'
    if experiment_path.exists() and json.loads(experiment_path.read_text(encoding='utf8')) != experiment:
        raise RuntimeError('Ablation changed; preserve data and use a new name')
    runner.save(experiment_path, experiment)
    original = runner.configuration

    def configure(harness, destination, endpoint, token):
        command, env = original(harness, destination, endpoint, token)
        if Path(command[-1]).name != 'thinkflow_worker.py':
            raise RuntimeError('Unexpected worker command')
        return [*command[:-1], str(Path(__file__).resolve()), '--worker', '--mode', args.mode,
                '--task', args.task], env

    runner.configuration = configure
    key = runner.load_key()
    results = []
    for repeat in range(1, args.repetitions + 1):
        job = {'id': f'{args.task}-thinkflow-r{repeat}', 'harness': 'thinkflow',
               'task': args.task, 'repeat': repeat}
        results.append(runner.run_one(job, directory, key))
        runner.save(directory / 'results.json', results)


if __name__ == '__main__':
    main()
