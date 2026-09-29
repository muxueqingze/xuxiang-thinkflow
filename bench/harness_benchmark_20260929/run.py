"""Finite opt-in same-model benchmark. Outputs stay under ignored artifacts/."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import platform
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))
from adapters import configuration
from adapters import PI_ROOT
from meter import Meter
from monitor import print_progress
from scripts.live_deepseek import load_key

HARNESSES = ('thinkflow','pi','opencode')
PILOT = "This is an isolated connectivity check. Create hello.py containing a function add(a, b) returning a+b. Run python to verify add(2,3)==5. Work only in the current directory, do not access the network or inspect parent directories. Finish with a brief summary."


def freeze_manifest(directory, pilot):
    files=[HERE/name for name in ('meter.py','run.py','adapters.py','thinkflow_worker.py','monitor.py','package-lock.json')]
    if not pilot:
        files.append(HERE/'tasks.py')
    files.extend(p for p in (ROOT/'src').rglob('*') if p.is_file() and p.suffix in ('.py','.md'))
    opencode_package=Path(os.environ.get('APPDATA',''))/'npm/node_modules/opencode-ai/package.json'
    manifest={'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
              'versions':{'thinkflow':'0.8.0','pi':json.loads((PI_ROOT/'package.json').read_text(encoding='utf-8'))['version'],
                          'opencode':json.loads(opencode_package.read_text(encoding='utf-8'))['version'],
                          'python':platform.python_version(),'platform':platform.platform(),
                          'node':subprocess.check_output(['node','--version'],text=True).strip()},
              'generation':{'model':'deepseek-flash','thinking':'enabled','reasoning_effort':'high',
                            'temperature':0,'max_tokens':8192,'max_requests':24,'max_seconds':300},
              'measurement':'loopback-streaming-bounded-usage-drain', 'order_seed':20260929,'pilot':pilot,
              'files':{str(p.relative_to(ROOT)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)}}
    path=directory/'manifest.json'
    if path.exists() and json.loads(path.read_text(encoding='utf-8'))!=manifest:
        raise RuntimeError('Frozen code/configuration changed. Preserve existing results and use a new experiment name.')
    save(path,manifest)


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n',encoding='utf-8')


def kill_tree(process):
    if process.poll() is not None:
        return
    if os.name == 'nt':
        subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'], capture_output=True,
                       timeout=15,creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        import signal
        os.killpg(process.pid, signal.SIGKILL)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


def run_one(job, directory, key, pilot=False):
    destination = directory/job['id']
    if (destination/'result.json').exists():
        print(json.dumps({'skip_saved':job['id']}),flush=True)
        return json.loads((destination/'result.json').read_text(encoding='utf-8'))
    if destination.exists():
        raise RuntimeError(f'Incomplete run preserved for inspection: {destination}')
    workspace = destination/'workspace'
    workspace.mkdir(parents=True)
    if pilot:
        prompt = PILOT
    else:
        from tasks import materialize
        prompt = materialize(job['task'],workspace)
    common = ('\n\nEnvironment: Windows, Python 3.12 is available as python. '
              'Use only the standard library. Work only in the current project directory; '
              'do not inspect parent directories, personal configuration or external services. '
              'Implement the complete contract in README.md when present, preserve public APIs, '
              'run the supplied public tests and add checks if useful. Do not change the public tests. '
              'Do not request human input. Finish after verification; no repeated checking without new changes.')
    prompt += common
    (destination/'prompt.txt').write_text(prompt,encoding='utf-8')
    public_tests = {str(p.relative_to(workspace)):hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in workspace.rglob('*.py') if p.name.startswith('test') or p.name=='public_tests.py'}
    meter = Meter(key,destination/'meter.json',max_seconds=300,max_requests=24,max_tokens=8192)
    endpoint = meter.start()
    started = time.monotonic()
    timed_out = False
    process = None
    infrastructure_error = None
    print(json.dumps({'start':job['id'],'harness':job['harness'],'task':job['task']}),flush=True)
    try:
        command,env = configuration(job['harness'],destination,endpoint,meter.token)
        with (destination/'stdout.jsonl').open('wb') as out, (destination/'stderr.txt').open('wb') as err:
            process = subprocess.Popen(command,cwd=workspace,env=env,stdin=subprocess.PIPE,
                                       stdout=out,stderr=err,start_new_session=os.name!='nt',
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            deadline = time.monotonic() + 300
            first_wait = True
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    kill_tree(process)
                    break
                try:
                    process.communicate(prompt.encode('utf-8') if first_wait else None,
                                        timeout=min(8, remaining))
                    break
                except subprocess.TimeoutExpired:
                    first_wait = False
                    print_progress(job['id'], destination, meter)
    except Exception as exc:
        infrastructure_error = type(exc).__name__ + ': ' + str(exc)[:500].replace(key,'[REDACTED]')
        if process is not None:
            kill_tree(process)
    finally:
        run_seconds = round(time.monotonic()-started,3)
        usage = meter.finish()
    if pilot:
        check = subprocess.run([sys.executable,'-B','-c',"from hello import add; assert add(2,3)==5; assert add(-4,2)==-2"],
                               cwd=workspace,capture_output=True,timeout=15)
        grade={'passed':check.returncode==0,'checks_passed':int(check.returncode==0),'checks_total':1,
               'details':[] if check.returncode==0 else [check.stderr.decode('utf-8',errors='replace')[-1200:]]}
    else:
        from tasks import grade as evaluate
        try:
            grade = evaluate(job['task'],workspace)
        except Exception as exc:
            grade = {'passed':False,'checks_passed':0,'checks_total':0,'details':['Grader failed: '+type(exc).__name__]}
            infrastructure_error = 'grader: '+type(exc).__name__
    changed_tests=[name for name,digest in public_tests.items()
                   if not (workspace/name).is_file() or hashlib.sha256((workspace/name).read_bytes()).hexdigest()!=digest]
    if changed_tests:
        grade['passed']=False
    stderr_path=destination/'stderr.txt'
    stderr = stderr_path.read_text(encoding='utf-8',errors='replace') if stderr_path.exists() else ''
    exit_code=process.returncode if process else None
    result = {**job,'pilot':pilot,'exit_code':exit_code,'timed_out':timed_out,
              'infrastructure_error':infrastructure_error,'modified_public_tests':changed_tests,
              'seconds':run_seconds,'measurement_seconds':round(time.monotonic()-started,3),
              'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest(),'grade':grade,'usage':usage,
              'stderr_tail':stderr[-1500:].replace(key,'[REDACTED]')}
    local = destination/'harness.json'
    if local.exists():
        try:
            raw = json.loads(local.read_text(encoding='utf-8'))
            result['stopped_reason']=raw.get('stopped_reason')
            result['writes_before_stream_finished']=raw.get('writes_before_stream_finished',0)
        except (OSError, ValueError):
            result['diagnostic_error']='Unreadable optional harness record'
    save(destination/'result.json',result)
    print(json.dumps({'done':job['id'],'passed':grade['passed'],'score':f"{grade['checks_passed']}/{grade['checks_total']}",
                      'stopped_reason':result.get('stopped_reason'),
                      'seconds':run_seconds,'tokens':usage['total_tokens'],'usage_complete':usage['usage_complete'],
                      'calls':usage['api_requests'],'exit_code':exit_code}),flush=True)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',action='store_true',help='Authorize this finite real-provider experiment')
    parser.add_argument('--pilot',action='store_true')
    parser.add_argument('--harness',choices=HARNESSES)
    parser.add_argument('--task')
    parser.add_argument('--repetitions',type=int,choices=(1,2),default=2)
    parser.add_argument('--name',default='formal')
    args=parser.parse_args()
    if not args.run:
        parser.print_help()
        return
    if not args.name.replace('-','').replace('_','').isalnum():
        parser.error('name must be a simple experiment label')
    directory=ROOT/'artifacts/benchmark-20260929'/args.name
    directory.mkdir(parents=True,exist_ok=True)
    freeze_manifest(directory,args.pilot)
    harnesses=[args.harness] if args.harness else list(HARNESSES)
    if args.pilot:
        task_ids=['connectivity']
        repetitions=1
    else:
        from tasks import TASKS
        task_ids=[args.task] if args.task else list(TASKS)
        if any(t not in TASKS for t in task_ids):
            parser.error('Unknown task')
        repetitions=args.repetitions
    jobs=[{'id':f'{task}-{harness}-r{repeat+1}','task':task,'harness':harness,'repeat':repeat+1}
          for repeat in range(repetitions) for task in task_ids for harness in harnesses]
    random.Random(20260929).shuffle(jobs)
    plan=directory/'plan.json'
    if plan.exists() and json.loads(plan.read_text(encoding='utf-8'))!=jobs:
        raise RuntimeError('Existing experiment plan differs; use a new explicit name')
    save(plan,jobs)
    key=load_key()
    results=[]
    for job in jobs:
        results.append(run_one(job,directory,key,pilot=args.pilot))
        save(directory/'results.json',results)
    print(json.dumps({'complete':str(directory),'runs':len(results),
                      'passes':sum(r['grade']['passed'] for r in results),
                      'tokens':sum(r['usage']['total_tokens'] for r in results)}),flush=True)


if __name__=='__main__':
    main()
