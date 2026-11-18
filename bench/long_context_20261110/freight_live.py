"""Explicit, bounded live three-release comparison, independently graded after each process exits."""
import argparse
import asyncio
from collections import Counter
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
from bench.efficiency_20261011.meter import Meter
from bench.efficiency_20261011.run import configuration, pi_package, save, sha, stop_child
from bench.efficiency_20261011.verified_edit_live import ENDPOINT, MODEL
from bench.long_context_20261110.freight_task import grade, prompts, seed

REQUESTS, OUTPUT, SECONDS = 36, 16384, 540
OBSERVED_TOKEN_LIMIT, REQUEST_BYTES = 380_000, 350_000


class FreightMeter(Meter):
    def __init__(self, key, scratch, harness):
        super().__init__(key, scratch / 'meter.json', upstream=ENDPOINT, model=MODEL,
                         harness=harness, remove_sdk_cap=harness == 'pi')
        self.aliases = [(str(ROOT), 'C:/benchmark/driver'),
                        (str(Path.home()), 'C:/benchmark/home')]

    def save(self):
        # The driver exports the in-memory request records once the stream drains.
        # Per-chunk file replacement can interrupt an active HTTP stream on Windows.
        pass

    def prepare(self, body):
        if (len(self.requests) > REQUESTS or time.monotonic() - self.started > SECONDS
                or self.totals()['total_tokens'] >= OBSERVED_TOKEN_LIMIT):
            raise TimeoutError('run budget')
        incoming, forwarded = super().prepare(body)
        body['max_tokens'] = forwarded['max_tokens'] = OUTPUT

        def anonymize(value):
            if isinstance(value, str):
                for origin, alias in self.aliases:
                    for old in [origin, origin.replace('\\', '/'), json.dumps(origin,ensure_ascii=False)[1:-1]]:
                        value = value.replace(old, alias)
                return value
            if isinstance(value, dict):
                return {k:anonymize(v) for k,v in value.items()}
            if isinstance(value, list):
                return [anonymize(v) for v in value]
            return value

        body['messages'] = anonymize(body['messages'])
        self.requests[-1]['role_chars'] = dict(sum_roles(body['messages']))
        user_history = '\n'.join(str(m.get('content','')) for m in body['messages'] if m['role'] in ('user','tool'))
        self.requests[-1]['error_feedback_markers'] = [s for s in
            ('read_required','revision_conflict','status="failed"','file not found','not permitted',
             'No such file','does not exist','unknown effect') if s in user_history]
        if len(json.dumps(body, ensure_ascii=False).encode()) > REQUEST_BYTES:
            raise ValueError('single request body budget')
        return incoming, forwarded


def sum_roles(messages):
    sizes = Counter()
    for message in messages:
        sizes[message['role']] += len(json.dumps(message,ensure_ascii=False))
    return sizes


def tool_summary(event):
    summary = {k:event[k] for k in ('tool','success','flow') if k in event}
    cmd = event.get('cmd') or ''
    if cmd:
        summary['cmd_hash'] = hashid(cmd)
        summary['looks_like_verification'] = bool(re.search(
            r'assert|unittest|pytest|shipping\.py (quote|export)|freight\.(quote|manifest)\(',cmd,re.I))
    if event.get('path'):
        target = Path(event['path'])
        summary['target'] = target.name if target.name in ('freight.py','rates.json','shipping.py','README.md') else 'other'
        summary['path_form'] = 'absolute' if target.is_absolute() else 'relative'
    if not event.get('success',True) and event['tool'] in ('read','write','edit'):
        summary['error'] = public_excerpt(event.get('error',''))[:350]
    return summary


def public_excerpt(text):
    text = text.replace(str(Path.cwd()),'[workspace]').replace(str(ROOT),'[driver]').replace(str(Path.home()),'[home]')
    return re.sub(r'[A-Za-z]:[\\/]Users[\\/][^\\/ ]+', '[home]',text).strip()[:700]


def wire_usage(row):
    raw = row.get('usage') or {}
    cached = raw.get('prompt_cache_hit_tokens', (raw.get('prompt_tokens_details') or {}).get('cached_tokens'))
    return {'prompt_tokens':raw.get('prompt_tokens'),'completion_tokens':raw.get('completion_tokens'),
            'prompt_cache_hit_tokens':cached,
            'prompt_cache_miss_tokens':raw['prompt_tokens']-cached if isinstance(cached,int) else None,
            'completion_tokens_details':raw.get('completion_tokens_details'),'provider_cost':raw.get('cost'),
            'provider_list_cost':(raw.get('opencode') or {}).get('list_cost')}


async def tf_worker(stage):
    sys.path.insert(0, os.environ['BENCH_SOURCE'])
    from bench.efficiency_20261011.thinkflow_worker import create_agent, resolve_system_prompt
    from src.session import SessionStore
    workspace = str(Path.cwd())
    config = dict(provider='openai', base_url=os.environ['BENCH_URL'], api_key=os.environ['BENCH_API_KEY'],
                  model=MODEL, thinking_mode='enabled', reasoning_effort='high', stream_options_include_usage=True,
                  max_tokens=None, max_run_turns=None, max_run_seconds=None, max_auto_continues=None,
                  context={'enabled':False}, interfaces={'skills':{'enabled':False},'web':{'enabled':False}},
                  disabled_native_tools=['web_search','fetch_url','image_generate','list_skills'],
                  security={'profile':'balanced','bash_policy':'unrestricted','approval_mode':'approve_all',
                            'allowed_roots':[workspace]})
    path = Path(os.environ['BENCH_RECORD'])
    store = SessionStore(str(path.parent / 'session.json'), cwd=workspace)
    tools, issues, request_reads, final_chunks = [], [], [], []

    def observe(event):
        if event['type'] == 'turn_started':
            final_chunks.clear()
        if event['type'] == 'text_delta' and event['channel'] == 'text':
            final_chunks.append(event['text'])
        if event['type'] == 'tool_completed':
            tools.append(tool_summary(event))
        if event['type'] in ('error','parser_error','provider_protocol_error'):
            issues.append([event['type'],event.get('kind')])

    agent = create_agent(config,resolve_system_prompt(config,cwd=workspace),workspace,
                         event_sink=observe,session_store=store)
    try:
        if stage > 1:
            agent.load_snapshot(store.load())
        resolve_ref = getattr(agent.executor,'request_text',None)
        if resolve_ref:
            def observed_read(ref):
                text = resolve_ref(ref)
                request_reads.append({'ref':ref,'chars':len(text)})
                return text
            agent.executor.request_text = observed_read
        previous = len(agent.conversation.requests)
        before = agent.compaction_count
        await agent.run(sys.stdin.read())
        save(path, {'prior_user_requests_restored':previous,'session_id_hash':hashid(agent.session_id),
                    'compactions':agent.compaction_count - before,'tools':tools,'issues':issues,
                    'python_stdin_resolved':request_reads,
                    'visible_final_excerpt':public_excerpt(agent.view.strip_command_blocks(''.join(final_chunks))),
                    'stop':agent.stopped_reason,'has_error':bool(agent.last_error)})
    finally:
        await agent.close()


def hashid(text):
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def pi_session(folder):
    paths = list(folder.glob('**/*.jsonl'))
    if not paths:
        return {'user_requests':0,'compactions':0}
    if len(paths) != 1:
        raise RuntimeError('ambiguous isolated Pi session')
    rows = [json.loads(s) for s in paths[0].read_text(encoding='utf8').split('\n') if s]
    last = next((row['message'] for row in reversed(rows) if row.get('message',{}).get('role')=='assistant'),{})
    answer = ''.join(block.get('text','') for block in last.get('content',[]) if block.get('type')=='text')
    return {'user_requests':sum(row.get('message',{}).get('role') == 'user' for row in rows),
            'compactions':sum(row.get('type') == 'compaction' for row in rows),
            'session_id_hash':hashid(rows[0]['id']),'visible_final_excerpt':public_excerpt(answer)}


def freeze_source(ref, folder):
    revision = subprocess.check_output(['git','rev-parse',ref],cwd=ROOT,text=True).strip()
    raw = subprocess.check_output(['git','archive',revision,'src'],cwd=ROOT)
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        archive.extractall(folder, filter='data')
    return revision


def run(harness, ref, destination):
    if destination.exists():
        raise ValueError('choose an unused evidence path')
    task = prompts()
    pi = pi_package() if harness == 'pi' else None
    if pi and pi['version'] != '1.0.1':
        raise ValueError('expected Pi 1.0.1')
    # Use a real, anonymous location so absolute paths printed by native tools remain executable.
    temp_root = Path(Path(os.environ['SystemRoot']).anchor) / 'Temp' if os.name=='nt' else Path('/tmp')
    temp_root.mkdir(exist_ok=True)
    facts = {'fixture_sha256':sha(HERE / 'freight_task.py'),'driver_sha256':sha(Path(__file__)),
             'model':MODEL,'harness':harness,'automatic_compaction':'native defaults; no manual compaction',
             'caps':{'requests':REQUESTS,'per_response_tokens':OUTPUT,'run_seconds':SECONDS,
                     'stop_before_next_request_at_total_observed_tokens':OBSERVED_TOKEN_LIMIT,
                     'max_request_bytes':REQUEST_BYTES}, 'stages':[]}
    with tempfile.TemporaryDirectory(prefix='thinkflow-freight-',dir=temp_root) as folder:
        scratch = Path(folder)
        workspace = scratch / 'workspace'
        workspace.mkdir()
        seed(workspace)
        meter = FreightMeter(os.environ.pop('OPENCODE_GO_API_KEY'),scratch,harness)
        endpoint = meter.start()
        try:
            command, env = configuration(harness, scratch, endpoint, meter.token,
                                          {'generation':{'model':MODEL},'pi':pi})
            profile = scratch/'profile'
            sessions = profile/'sessions'
            record = Path(env['BENCH_RECORD'])
            if pi:
                facts['pi_version'] = pi['version']
                facts['pi_cli_sha256'] = pi['sha256']
                command[command.index('--no-session'):command.index('--no-session')+1] = [
                    '--session-id','freight-release','--session-dir',str(sessions)]
                settings = Path(env['PI_CODING_AGENT_DIR'])/'settings.json'
                data = json.loads(settings.read_text(encoding='utf8'))
                del data['compaction']
                save(settings,data)
            else:
                source = scratch/'source'
                facts['thinkflow_revision'] = freeze_source(ref,source)
                facts['src_git_tree'] = subprocess.check_output(
                    ['git','rev-parse',facts['thinkflow_revision']+':src'],cwd=ROOT,text=True).strip()
                env['BENCH_SOURCE'] = str(source)
                command = [sys.executable,'-B',str(Path(__file__))]
            for stage, prompt in enumerate(task,1):
                start = time.monotonic()
                prior = pi_session(sessions) if pi else {}
                prior_requests = len(meter.requests)
                child_command = command if pi else [*command,'--worker',str(stage)]
                if record.exists():
                    record.unlink()
                child = subprocess.Popen(child_command, cwd=workspace, env=env, stdin=subprocess.PIPE,
                                         stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,
                                         start_new_session=os.name!='nt',
                                         creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                try:
                    _,err = child.communicate(prompt.encode('utf8'),timeout=max(1,SECONDS-(start-meter.started)))
                except subprocess.TimeoutExpired:
                    stop_child(child)
                    err = b''
                # Interrupted downstream streams are drained by the meter; keep each request in its stage.
                while meter.active:
                    time.sleep(.1)
                observed = json.loads(record.read_text(encoding='utf8')) if record.exists() else {}
                if pi:
                    after = pi_session(sessions)
                    starts = {e['id']:e for e in observed.get('events',[]) if e['type']=='tool_started'}
                    observed = {'prior_user_requests_restored':prior['user_requests'],
                                'session_id_hash':after.get('session_id_hash'),
                                'visible_final_excerpt':after.get('visible_final_excerpt',''),
                                'compactions':after['compactions']-prior['compactions'],
                                'tools':[tool_summary({**starts.get(e['id'],{}),**e})
                                         for e in observed.get('events',[]) if e['type']=='tool_completed'],
                                'stop':observed.get('stopped_reason'),'has_error':bool(observed.get('last_error'))}
                checks = grade(workspace,stage)
                rows = [{**{k:r.get(k) for k in ('index','http_status','finished','finish_reason','role_chars',
                                                 'client_disconnected','transport_error','forwarded_generation',
                                                 'error_feedback_markers')},
                         'usage':wire_usage(r)}
                        for r in meter.requests[prior_requests:]]
                facts['stages'].append({'stage':stage,'prompt_sha256':hashid(prompt),'user_input_chars':len(prompt),
                                        'wall_seconds':round(time.monotonic()-start,3),'exit_code':child.returncode,
                                        'stderr_bytes':len(err),'requests':rows,'observed':observed,'checks':checks})
                print(json.dumps({'stage':stage,'harness':harness,'exit':child.returncode,
                                  'checks':sum(checks.values()),'possible':len(checks),
                                  'requests':len(rows),'observed_stop':observed.get('stop')}),flush=True)
                if child.returncode or not observed.get('stop') or observed.get('has_error'):
                    if not rows and err:
                        # Startup diagnostics only, never retain model output.
                        print(err.decode('utf8',errors='replace')[:900],file=sys.stderr)
                    break
        finally:
            facts['usage'] = meter.finish()
            facts['seconds'] = round(time.monotonic() - meter.started,3)
    facts['temporary_workspace_removed'] = not scratch.exists()
    save(destination,facts)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--worker',type=int)
    p.add_argument('--run',choices=['thinkflow','pi'])
    p.add_argument('--ref',default='d99216d')
    p.add_argument('--output',type=Path)
    a = p.parse_args()
    if a.worker:
        asyncio.run(tf_worker(a.worker))
    elif a.run and a.output:
        run(a.run,a.ref,a.output)
    else:
        p.error('paid runs require --run HARNESS --output NEW_PATH')
