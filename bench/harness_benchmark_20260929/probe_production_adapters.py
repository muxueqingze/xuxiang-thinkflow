"""Offline real-CLI wire probe. Uses only a loopback fake SSE endpoint and dummy key.

The timeout here bounds a synthetic startup test, never a production task.
All generated files go under temp/production-adapters for recoverable cleanup.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import threading
import time

from adapters import configuration, PRODUCTION_CAPABILITIES


def probe(harness, root, timeout):
    run_dir = root / harness
    work = run_dir / 'work'
    work.mkdir(parents=True, exist_ok=True)
    captured = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            captured.append(payload)
            chunk = {'id':'offline-probe','object':'chat.completion.chunk',
                     'created':int(time.time()),'model':'deepseek-flash',
                     'choices':[{'index':0,'delta':{'role':'assistant','content':'probe-ok'},
                                 'finish_reason':None}]}
            final = {**chunk, 'choices':[{'index':0,'delta':{},'finish_reason':'stop'}],
                     'usage':{'prompt_tokens':10,'completion_tokens':2,'total_tokens':12,
                              'prompt_tokens_details':{'cached_tokens':0}}}
            body = ''.join('data: '+json.dumps(x)+'\n\n' for x in (chunk, final))+'data: [DONE]\n\n'
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(body.encode())))
            self.end_headers()
            self.wfile.write(body.encode())

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        command, env = configuration(harness, run_dir,
                                    f'http://127.0.0.1:{server.server_port}/v1',
                                    'offline-probe-token', mode='production')
        process = subprocess.Popen(command+['Reply probe-ok. Do not use any tools.'],
                                   cwd=work, env=env, text=True, encoding='utf-8',
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        timed_out = False
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],
                           capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
            stdout, stderr = process.communicate(timeout=10)
        (run_dir/'stdout.log').write_text(stdout, encoding='utf-8')
        (run_dir/'stderr.log').write_text(stderr, encoding='utf-8')
        (run_dir/'wire.json').write_text(json.dumps(captured, indent=2), encoding='utf-8')
        request_info = [{'keys':sorted(p), 'output_caps':{k:p[k] for k in
                        ('max_tokens','max_completion_tokens','max_output_tokens') if k in p},
                        'message_count':len(p.get('messages', [])),
                        'tool_count':len(p.get('tools', []))} for p in captured]
        return {'harness':harness, 'exit_code':process.returncode, 'timed_out':timed_out,
                'requests':request_info,
                'passed':bool(captured) and not timed_out and process.returncode == 0
                         and all(not r['output_caps'] for r in request_info)}
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--harness', choices=['pi','opencode','both'], default='both')
    parser.add_argument('--timeout', type=int, default=60)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]/'temp/production-adapters'/str(time.time_ns())
    names = ['pi','opencode'] if args.harness == 'both' else [args.harness]
    report = {'capabilities':PRODUCTION_CAPABILITIES,
              'results':[probe(h, root, args.timeout) for h in names]}
    (root/'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({'directory':str(root), **report}, indent=2))
    return 0 if all(r['passed'] for r in report['results']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
