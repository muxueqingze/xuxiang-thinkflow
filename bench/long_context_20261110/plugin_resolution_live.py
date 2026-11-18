"""Bounded, separately graded releases with a native compaction before the final request."""
import argparse
import asyncio
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from bench.long_context_20261110 import freight_live as live
from bench.long_context_20261110 import plugin_resolution_task as task

REVISION = 'be766c0d72e65fb306f866d517b07a6ac7640973'
REQUESTS, TAIL, SECONDS = 24, 512, 720


async def worker(stage):
    if stage == 3:
        sys.path.insert(0, os.environ['BENCH_SOURCE'])
        from bench.efficiency_20261011 import thinkflow_worker
        create = thinkflow_worker.create_agent

        def compact_before_prompt(config, *args, **kwargs):
            config['compaction'] = {'keep_recent_tokens': TAIL}
            agent = create(config, *args, **kwargs)
            load = agent.load_snapshot

            def load_and_compact(data):
                load(data)
                stats = agent.compact(force=True)
                live.save(Path(os.environ['BENCH_RECORD']).with_name('compaction.json'), asdict(stats))

            agent.load_snapshot = load_and_compact
            return agent
        thinkflow_worker.create_agent = compact_before_prompt
    await live.tf_worker(stage)


async def pi_compact(command, workspace, env):
    rpc = command.copy()
    rpc[rpc.index('--mode') + 1] = 'rpc'
    rpc.remove('-p')
    child = await asyncio.create_subprocess_exec(*rpc, cwd=workspace, env=env, stdin=-1, stdout=-1,
                                                 stderr=subprocess.DEVNULL, limit=2**20)
    try:
        child.stdin.write(b'{"id":"boundary","type":"compact"}\n')
        await child.stdin.drain()
        async with asyncio.timeout(120):
            while line := await child.stdout.readline():
                msg = json.loads(line)
                if msg.get('type') == 'response' and msg.get('id') == 'boundary':
                    return {'success': msg['success'], 'tokens_before': (msg.get('data') or {}).get('tokensBefore'),
                            'error': msg.get('error')}
        return {'success': False, 'error':'EOF before compact response'}
    finally:
        child.stdin.close()
        try:
            await asyncio.wait_for(child.wait(), 5)
        except asyncio.TimeoutError:
            child.kill()
            await child.wait()


def run(harness, destination, ref=REVISION):
    if destination.exists():
        raise ValueError('choose an unused destination')
    live.REQUESTS = REQUESTS
    live.SECONDS = SECONDS
    live.OBSERVED_TOKEN_LIMIT = 240000
    pi = live.pi_package() if harness == 'pi' else None
    if pi and pi['version'] != '1.0.1':
        raise ValueError('Pi 1.0.1 required')
    tmp = Path(os.environ['SystemRoot']).anchor + 'Temp'
    facts = dict(harness=harness, model=live.MODEL, fixture_sha256=live.sha(HERE / 'plugin_resolution_task.py'),
                 driver_sha256=live.sha(Path(__file__)), base_driver_sha256=live.sha(HERE / 'freight_live.py'),
                 caps=dict(requests=REQUESTS, output_tokens=live.OUTPUT, run_seconds=SECONDS,
                           observed_tokens=live.OBSERVED_TOKEN_LIMIT, request_bytes=live.REQUEST_BYTES),
                 explicit_compaction_before_stage=3, recent_tokens_target=TAIL, stages=[])
    with tempfile.TemporaryDirectory(prefix='plugin-resolution-',dir=tmp) as folder:
        scratch = Path(folder)
        workspace = scratch / 'workspace'
        workspace.mkdir()
        task.seed(workspace)
        meter = live.FreightMeter(os.environ['OPENCODE_GO_API_KEY'], scratch, harness)
        endpoint = meter.start()
        try:
            command, env = live.configuration(harness, scratch, endpoint, meter.token,
                                              {'generation':{'model':live.MODEL},'pi':pi})
            sessions = scratch / 'profile' / 'sessions'
            record = Path(env['BENCH_RECORD'])
            if pi:
                facts['pi_version'], facts['pi_cli_sha256'] = pi['version'], pi['sha256']
                i = command.index('--no-session')
                command[i:i+1] = ['--session-id','plugin-resolution','--session-dir',str(sessions)]
                settings = Path(env['PI_CODING_AGENT_DIR']) / 'settings.json'
                config = json.loads(settings.read_text(encoding='utf8'))
                config['compaction'] = {'enabled':True, 'keepRecentTokens':TAIL}
                live.save(settings, config)
            else:
                facts['thinkflow_revision'] = live.freeze_source(ref, scratch/'source')
                facts['src_git_tree'] = subprocess.check_output(['git','rev-parse', facts['thinkflow_revision']+':src'], cwd=live.ROOT,text=True).strip()
                env['BENCH_SOURCE'] = str(scratch / 'source')
                command = [sys.executable, '-B', str(Path(__file__))]
            for stage, prompt in enumerate(task.STAGES, 1):
                start = time.monotonic()
                cursor = len(meter.requests)
                before = live.pi_session(sessions) if pi else {}
                compaction = None
                if stage == 3 and pi:
                    compaction = asyncio.run(pi_compact(command, workspace, env))
                    compaction['request_count'] = len(meter.requests)-cursor
                record.unlink(missing_ok=True)
                child = subprocess.Popen(command if pi else [*command,'--worker',str(stage)], cwd=workspace,env=env,
                                         stdin=-1,stderr=-1,stdout=subprocess.DEVNULL,
                                         creationflags=subprocess.CREATE_NO_WINDOW)
                try:
                    _, err = child.communicate(prompt.encode(), timeout=max(1,SECONDS-(start-meter.started)))
                except subprocess.TimeoutExpired:
                    live.stop_child(child)
                    err = b''
                while meter.active:
                    time.sleep(.1)
                observation = json.loads(record.read_text(encoding='utf8')) if record.exists() else {}
                if pi:
                    after = live.pi_session(sessions)
                    restored = before['user_requests']
                    session = after.get('session_id_hash')
                    compactions = after['compactions'] - before['compactions']
                    stop = observation.get('stopped_reason')
                    errors = bool(observation.get('last_error'))
                else:
                    restored = observation.get('prior_user_requests_restored')
                    session = observation.get('session_id_hash')
                    compactions = observation.get('compactions',0)
                    if stage == 3 and record.with_name('compaction.json').exists():
                        compaction = json.loads(record.with_name('compaction.json').read_text(encoding='utf8'))
                        compaction['request_count'] = 0
                        compactions += int(compaction['changed'])
                    stop = observation.get('stop')
                    errors = observation.get('has_error')
                checks = task.grade(workspace, stage)
                rows = [{**{k:r.get(k) for k in ('index','http_status','finished','finish_reason','transport_error',
                                                 'client_disconnected','forwarded_generation','response_model')},
                         'usage':r.get('raw_usage')} for r in meter.requests[cursor:]]
                stageinfo = dict(stage=stage, prompt_sha256=live.hashlib.sha256(prompt.encode()).hexdigest(),
                                 seconds=round(time.monotonic()-start,2), exit_code=child.returncode, stderr_bytes=len(err),
                                 prior_requests=restored,session=session,compactions=compactions,
                                 explicit_compaction=compaction, stop=stop, error=errors, checks=checks, requests=rows)
                facts['stages'].append(stageinfo)
                print(json.dumps({'stage':stage, 'harness':harness, 'check_count':sum(checks.values()),
                                  'possible':len(checks), 'requests':len(rows),'compactions':compactions}),flush=True)
                if child.returncode or errors or not stop:
                    break
        finally:
            facts['usage'] = meter.finish()
            facts['seconds'] = round(time.monotonic()-meter.started,2)
    facts['temporary_workspace_removed'] = not scratch.exists()
    live.save(destination, facts)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker',type=int)
    parser.add_argument('--run',choices=['pi','thinkflow'])
    parser.add_argument('--ref', default=REVISION, help='ThinkFlow Git revision to freeze (default: %(default)s)')
    parser.add_argument('--output',type=Path)
    args = parser.parse_args()
    if args.worker:
        asyncio.run(worker(args.worker))
    elif args.run and args.output:
        run(args.run, args.output, args.ref)
    else:
        parser.error('a live run requires --run and --output')
