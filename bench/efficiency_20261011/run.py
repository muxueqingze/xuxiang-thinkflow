"""Explicitly opt-in fixed-task efficiency comparison; no paid calls during preparation."""
from __future__ import annotations
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import random
import shutil
import subprocess
import sys
import tarfile
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
from bench.efficiency_20261011.meter import Meter
from bench.efficiency_20261011.observations import claude_metrics, thinkflow_metrics, pi_metrics
from bench.harness_benchmark_20260929.tasks import TASKS, VERSION, materialize, grade
from bench.harness_benchmark_20260929.adapters import write_configs

BASELINE_REF = '2a4f4b8'
HARNESSES = ('thinkflow', 'baseline', 'pi', 'claude')
COMMON = ('\n\nEnvironment: Windows; Python is available as python. Use only the Python standard library. '
          'Work only in the current project directory; do not inspect parent directories, personal configuration, '
          'credentials or external services, and do not access the network. Preserve the public APIs and public_tests.py. '
          'Run the supplied public tests and additional checks when needed. Do not request human input. '
          'Finish after verification; no repeated checking without new changes.')


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    temporary.replace(path)


def sha(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def prepare_baseline():
    commit = subprocess.check_output(['git', 'rev-parse', BASELINE_REF], cwd=ROOT, text=True).strip()
    destination = HERE / 'baseline'
    archive = subprocess.check_output(['git', 'archive', '--format=tar', commit], cwd=ROOT)
    with tarfile.open(fileobj=io.BytesIO(archive)) as source:
        original = {member.name: hashlib.sha256(source.extractfile(member).read()).hexdigest()
                    for member in source.getmembers() if member.isfile()
                    and (member.name.startswith('src/') or member.name in ('requirements.txt', 'pyproject.toml', 'package.json'))}
    if destination.exists():
        marker = destination / '.benchmark-revision'
        if not marker.is_file() or marker.read_text(encoding='utf8').strip() != commit:
            raise RuntimeError('Existing baseline has no matching archive marker; preserve it for inspection')
        actual = {str(p.relative_to(destination)).replace('\\', '/'): sha(p) for p in source_files(destination)}
        if actual != original:
            raise RuntimeError('Archived baseline source differs from the original Git archive; preserve it for inspection')
        return destination, commit
    destination.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive)) as source:
        source.extractall(destination, filter='data')
    (destination / '.benchmark-revision').write_text(commit + '\n', encoding='utf8')
    return destination, commit


def claude_executable():
    shim = shutil.which('claude')
    if not shim:
        raise RuntimeError('Installed Claude Code CLI was not found')
    cli = Path(shim).resolve()
    if os.name == 'nt' and cli.suffix.lower() != '.exe':
        cli = cli.parent / 'node_modules/@anthropic-ai/claude-code/bin/claude.exe'
    if not cli.is_file():
        raise RuntimeError('Cannot resolve the installed native Claude executable from its npm shim')
    return cli


def pi_package():
    shim = Path(shutil.which('pi')).resolve()
    root = shim.parent / 'node_modules/@earendil-works/pi-coding-agent'
    package = json.loads((root / 'package.json').read_text(encoding='utf8'))
    return {'path': str(root / 'dist/cli.js'), 'version': package['version'],
            'sha256': sha(root / 'dist/cli.js')}


def source_files(root):
    return sorted([p for p in (root / 'src').rglob('*') if p.is_file() and '__pycache__' not in p.parts]
                  + [root / name for name in ('requirements.txt', 'pyproject.toml', 'package.json')])


def frozen_files(baseline):
    files = {str(p.relative_to(ROOT)).replace('\\', '/'): sha(p) for p in source_files(ROOT)}
    files.update({'baseline/' + str(p.relative_to(baseline)).replace('\\', '/'): sha(p) for p in source_files(baseline)})
    for p in [*HERE.glob('*.py'), *HERE.glob('*.mjs'), HERE / 'README.md', ROOT / 'bench/harness_benchmark_20260929/tasks.py',
              ROOT / 'bench/harness_benchmark_20260929/meter.py', ROOT / 'scripts/live_deepseek.py']:
        files[str(p.relative_to(ROOT)).replace('\\', '/')] = sha(p)
    return files


def freeze(directory, task_ids, harnesses, repeats, provider=None):
    baseline, commit = prepare_baseline()
    cli = claude_executable() if 'claude' in harnesses else None
    version = subprocess.check_output([str(cli), '--version'], text=True, encoding='utf8').strip() if cli else None
    if cli and version != '2.1.294 (Claude Code)':
        raise RuntimeError('This experiment requires installed Claude Code 2.1.294; observed ' + version)
    help_text = subprocess.check_output([str(cli), '--help'], text=True, encoding='utf8') if cli else ''
    required = ('--setting-sources', '--safe-mode', '--effort', '--strict-mcp-config', '--include-partial-messages')
    if cli and any(flag not in help_text for flag in required):
        raise RuntimeError('Required isolation/output CLI flags are missing')
    manifest = {'baseline_commit': commit,
                'current_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                'files': frozen_files(baseline), 'tasks': task_ids, 'task_version': VERSION,
                'harnesses': harnesses, 'repeats': repeats, 'order_seed': 20261011,
                'python': platform.python_version(), 'platform': platform.platform(),
                'provider': provider or {}, 'pi': pi_package() if 'pi' in harnesses else None,
                'claude': {'path': str(cli), 'version': version, 'sha256': sha(cli),
                           'size': cli.stat().st_size, 'mtime_ns': cli.stat().st_mtime_ns} if cli else None,
                'generation': {'model': (provider or {}).get('model', 'deepseek-flash'), 'thinking': 'enabled', 'effort': 'high', 'temperature': 0,
                               'output_cap': None, 'request_cap': None, 'run_seconds_cap': None},
                'protocols': {'thinkflow': 'OpenAI chat completions', 'baseline': 'OpenAI chat completions',
                              'pi': 'OpenAI chat completions; native Pi prompt and read/bash/edit/write retained',
                              'claude': 'Anthropic messages; native Claude system prompt retained'},
                'claude_normalization': 'Remove SDK max_tokens; preserve original/forwarded generation fields. '
                                        'Force thinking.type=enabled and output_config.effort=high. No numeric output cap is imposed.',
                'task_prompt_common_sha256': hashlib.sha256(COMMON.encode()).hexdigest()}
    manifest_path = directory / 'manifest.json'
    if manifest_path.exists():
        if json.loads(manifest_path.read_text(encoding='utf8')) != manifest:
            raise RuntimeError('Frozen source/configuration/plan changed; preserve this experiment and use a new name')
    else:
        save(manifest_path, manifest)
    return manifest


def check_frozen(manifest):
    if frozen_files(HERE / 'baseline') != manifest['files']:
        raise RuntimeError('Frozen source changed during the experiment; refusing mixed-source runs')
    cli = manifest.get('claude')
    if cli:
        state = Path(cli['path']).stat()
        if (state.st_size, state.st_mtime_ns) != (cli['size'], cli['mtime_ns']):
            raise RuntimeError('Installed Claude executable changed during the experiment')
    if manifest.get('pi') and sha(manifest['pi']['path']) != manifest['pi']['sha256']:
        raise RuntimeError('Installed Pi executable changed during the experiment')


def configuration(harness, destination, endpoint, token, manifest):
    profile = destination / 'profile'
    profile.mkdir()
    names = ('PATH', 'PATHEXT', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'SYSTEMDRIVE', 'PROGRAMFILES',
             'PROGRAMFILES(X86)', 'PROGRAMDATA', 'NUMBER_OF_PROCESSORS', 'PROCESSOR_ARCHITECTURE')
    env = {k: v for k, v in os.environ.items() if k.upper() in names}
    for variable, folder in (('TEMP', 'tmp'), ('TMP', 'tmp'), ('APPDATA', 'appdata'), ('LOCALAPPDATA', 'localappdata'),
                             ('USERPROFILE', 'home'), ('HOME', 'home'), ('XDG_CONFIG_HOME', 'config'),
                             ('XDG_DATA_HOME', 'data'), ('XDG_STATE_HOME', 'state'), ('XDG_CACHE_HOME', 'cache')):
        path = profile / folder
        path.mkdir(exist_ok=True)
        env[variable] = str(path)
    env.update(PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1', CI='1', NO_COLOR='1')
    model_id = manifest.get('generation', {}).get('model', 'deepseek-flash')
    env.update(BENCH_API_KEY=token, BENCH_MODEL=model_id, BENCH_RECORD=str(destination / 'harness.json'))
    if harness == 'pi':
        agent_dir = profile / 'pi'
        env.update(PI_CODING_AGENT_DIR=str(agent_dir), PI_OFFLINE='1', PI_TELEMETRY='0')
        model = {'id': model_id, 'name': model_id, 'reasoning': True, 'input': ['text'],
                 'contextWindow': 1048576, 'maxTokens': 393216,
                 'cost': {'input': 0, 'output': 0, 'cacheRead': 0, 'cacheWrite': 0},
                 'compat': {'thinkingFormat': 'deepseek', 'supportsStore': False,
                            'supportsDeveloperRole': False, 'requiresReasoningContentOnAssistantMessages': True,
                            'supportsReasoningEffort': True, 'supportsUsageInStreaming': True,
                            'maxTokensField': 'max_tokens', 'supportsStrictMode': False}}
        write_configs({str(agent_dir / 'models.json'): {'providers': {'benchmark': {
            'baseUrl': endpoint, 'api': 'openai-completions', 'apiKey': '${BENCH_API_KEY}', 'models': [model]}}},
            str(agent_dir / 'settings.json'): {'quietStartup': True, 'cacheWarming': 'off', 'compaction': {'enabled': False}}})
        return [shutil.which('node'), manifest['pi']['path'], '--provider', 'benchmark', '--model', model_id,
                '--thinking', 'high', '--no-approve', '--no-context-files', '--no-extensions', '--no-skills',
                '--no-prompt-templates', '--no-themes', '--offline', '--no-session', '--mode', 'json', '-p',
                '-e', str(HERE / 'pi_worker.mjs')], env
    if harness != 'claude':
        env.update(BENCH_SOURCE=str(ROOT if harness == 'thinkflow' else HERE / 'baseline'),
                   BENCH_URL=endpoint, BENCH_API_KEY=token, BENCH_RECORD=str(destination / 'harness.json'),
                   THINKFLOW_HOME=str(profile / 'thinkflow'))
        return [sys.executable, '-B', str(HERE / 'thinkflow_worker.py')], env
    env.update(CLAUDE_CONFIG_DIR=str(profile / 'claude'), ANTHROPIC_BASE_URL=endpoint, ANTHROPIC_API_KEY=token,
               ANTHROPIC_MODEL=model_id, ANTHROPIC_DEFAULT_OPUS_MODEL=model_id,
               ANTHROPIC_DEFAULT_SONNET_MODEL=model_id, ANTHROPIC_DEFAULT_HAIKU_MODEL=model_id,
               CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC='1', CLAUDE_CODE_DISABLE_AUTO_UPDATE='1')
    bash = Path(os.environ.get('PROGRAMFILES', 'C:/Program Files')) / 'Git/bin/bash.exe'
    if bash.is_file():
        env['CLAUDE_CODE_GIT_BASH_PATH'] = str(bash)
    command = [manifest['claude']['path'], '--print', '--output-format', 'stream-json', '--verbose',
               '--include-partial-messages', '--model', model_id, '--effort', 'high',
               '--settings', '{"alwaysThinkingEnabled":true}', '--setting-sources', '', '--safe-mode',
               '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}', '--no-session-persistence',
               '--dangerously-skip-permissions', '--no-chrome', '--disallowed-tools', 'WebFetch,WebSearch']
    return command, env


def stop_child(process):
    if process.poll() is not None:
        return
    if os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True, timeout=15)
    else:
        import signal
        os.killpg(process.pid, signal.SIGKILL)
    process.wait()


def run_one(job, directory, key, manifest):
    destination = directory / job['id']
    saved = destination / 'result.json'
    if saved.is_file():
        print(json.dumps({'reuse_completed': job['id']}), flush=True)
        return json.loads(saved.read_text(encoding='utf8'))
    if destination.exists():
        raise RuntimeError('Incomplete job preserved; inspect it and choose a new experiment name: ' + str(destination))
    workspace = destination / 'workspace'
    workspace.mkdir(parents=True)
    prompt = materialize(job['task'], workspace) + COMMON
    public_hash = sha(workspace / 'public_tests.py')
    (destination / 'prompt.txt').write_text(prompt, encoding='utf8')
    provider = manifest.get('provider', {})
    meter = Meter(key, destination / 'meter.json', protocol='anthropic' if job['harness'] == 'claude' else 'openai',
                  upstream=provider.get('base_url'), model=manifest['generation']['model'], harness=job['harness'],
                  session_id=hashlib.sha256((str(directory.resolve()) + '/' + job['id']).encode()).hexdigest(),
                  remove_sdk_cap=job['harness'] == 'pi')
    endpoint = meter.start()
    process = None
    error = None
    interrupted = False
    started = time.monotonic()
    print(json.dumps({'start': job['id']}), flush=True)
    try:
        command, env = configuration(job['harness'], destination, endpoint, meter.token, manifest)
        # Command contains only the installed executable/flags, never credentials.
        save(destination / 'launch.json', {'command': command, 'environment_keys': sorted(env),
             'protocol': meter.protocol, 'source': env.get('BENCH_SOURCE')})
        with (destination / 'stdout.jsonl').open('wb') as out, (destination / 'stderr.txt').open('wb') as err:
            process = subprocess.Popen(command, cwd=workspace, env=env, stdin=subprocess.PIPE, stdout=out, stderr=err,
                                       start_new_session=os.name != 'nt',
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            first = True
            while True:
                try:
                    process.communicate(prompt.encode('utf8') if first else None, timeout=10)
                    break
                except subprocess.TimeoutExpired:
                    first = False
                    usage = meter.totals()
                    print(json.dumps({'running': job['id'], 'seconds': round(time.monotonic() - started),
                                      'requests': usage['api_requests'], 'tokens_observed': usage['total_tokens']}), flush=True)
    except KeyboardInterrupt:
        interrupted = True
        error = 'UserInterrupt'
        if process:
            stop_child(process)
    except Exception as exc:
        error = type(exc).__name__  # Transport exceptions may embed request credentials.
        if process:
            stop_child(process)
    finally:
        usage = meter.finish()
    try:
        evaluation = grade(job['task'], workspace)
    except Exception as exc:
        evaluation = {'passed': False, 'checks_passed': 0, 'checks_total': TASKS[job['task']]['checks_total'],
                      'details': [{'error': 'GraderInfrastructure:' + type(exc).__name__}]}
        error = error or 'GraderInfrastructure'
    changed_public = not (workspace / 'public_tests.py').is_file() or sha(workspace / 'public_tests.py') != public_hash
    if changed_public:
        evaluation['passed'] = False
    observations = {}
    try:
        if job['harness'] == 'claude':
            observations = claude_metrics(destination / 'stdout.jsonl')
        elif (destination / 'harness.json').is_file():
            metrics = pi_metrics if job['harness'] == 'pi' else thinkflow_metrics
            observations = metrics(json.loads((destination / 'harness.json').read_text(encoding='utf8')))
        else:
            observations = {'harness_error': 'MissingHarnessRecord'}
    except Exception as exc:
        observations = {'harness_error': 'ObservationInfrastructure:' + type(exc).__name__}
    result = {**job, 'seconds': round(time.monotonic() - started, 3), 'exit_code': process.returncode if process else None,
              'infrastructure_error': error, 'interrupted': interrupted, 'grade': evaluation,
              'modified_public_tests': changed_public, 'usage': usage, 'observations': observations,
              'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest()}
    result['accepted'] = evaluation['passed'] and not error and result['exit_code'] == 0 and not observations.get('harness_error')
    save(saved, result)
    print(json.dumps({'done': job['id'], 'passed': evaluation['passed'], 'accepted': result['accepted'],
                      'tokens': usage['total_tokens'], 'usage_complete': usage['usage_complete']}), flush=True)
    if interrupted:
        raise KeyboardInterrupt
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', help='Explicit real-provider opt-in')
    parser.add_argument('--prepare', action='store_true', help='Archive baseline and freeze metadata; no model calls')
    parser.add_argument('--tasks', nargs='+', choices=tuple(TASKS), default=list(TASKS))
    parser.add_argument('--harnesses', nargs='+', choices=HARNESSES, default=['thinkflow', 'baseline', 'pi'])
    parser.add_argument('--repeats', type=int, default=1)
    parser.add_argument('--name', default='formal')
    parser.add_argument('--base-url', help='OpenAI-compatible upstream, e.g. https://opencode.ai/inference/go/openai/v1')
    parser.add_argument('--model', default='deepseek-flash', help='Actual provider model id')
    parser.add_argument('--key-env', help='Read the provider key from this environment variable only')
    parser.add_argument('--read-job', help='Read one saved completed job, without credentials, freezing, or writes')
    args = parser.parse_args()
    if not args.name.replace('-', '').replace('_', '').isalnum():
        parser.error('Use a simple experiment name')
    if args.repeats < 1:
        parser.error('repeats must be positive')
    directory = HERE / 'artifacts' / args.name
    if args.read_job:
        if Path(args.read_job).name != args.read_job:
            parser.error('read-job must be a simple job id')
        print((directory / args.read_job / 'result.json').read_text(encoding='utf8'))
        return
    if not (args.run or args.prepare):
        parser.print_help()
        return
    provider = {'base_url': args.base_url, 'model': args.model, 'key_env': args.key_env}
    manifest = freeze(directory, args.tasks, args.harnesses, args.repeats, provider)
    jobs = [{'id': f'{task}-{harness}-r{repeat}', 'task': task, 'harness': harness, 'repeat': repeat}
            for repeat in range(1, args.repeats + 1) for task in args.tasks for harness in args.harnesses]
    random.Random(20261011).shuffle(jobs)
    plan = directory / 'plan.json'
    if plan.exists() and json.loads(plan.read_text(encoding='utf8')) != jobs:
        raise RuntimeError('Frozen job plan changed; use a new experiment name')
    if not plan.exists():
        save(plan, jobs)
    print(json.dumps({'prepared': str(directory), 'jobs': len(jobs), 'paid_calls': 0}), flush=True)
    if not args.run:
        return
    pending = [job for job in jobs if not (directory / job['id'] / 'result.json').exists()]
    for job in pending:
        if (directory / job['id']).exists():
            raise RuntimeError('An incomplete job is preserved: ' + job['id'])
    # Reusing completed results never needs to decrypt a provider credential.
    if pending and args.key_env:
        key = os.environ[args.key_env]
    elif pending:
        from scripts.live_deepseek import load_key
        key = load_key()
    else:
        key = None
    results = []
    for job in jobs:
        check_frozen(manifest)
        results.append(run_one(job, directory, key, manifest))
        check_frozen(manifest)
        save(directory / 'results.json', results)
    print(json.dumps({'complete': str(directory), 'jobs': len(results),
                      'accepted': sum(r['accepted'] for r in results)}), flush=True)


if __name__ == '__main__':
    main()
