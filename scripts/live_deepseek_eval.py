"""Explicit finite real-provider eval. Run only with --run; inert in normal CI."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.live_deepseek import load_key
from src.cli import create_agent, resolve_system_prompt


async def scenario(root, name, key, prompt, *, thinking=False, native=True, setup=None, check=None, inject=None):
    directory = root / name
    directory.mkdir(parents=True)
    if setup:
        setup(directory)
    events = []
    started = time.monotonic()
    def observe(event):
        if inject:
            inject(event, directory)
        allowed = ('type', 'turn', 'id', 'tool', 'channel', 'flow', 'status', 'success', 'reason', 'finish_reason')
        if event['type'] not in ('text_delta', 'reasoning_activity'):
            events.append({**{k: event[k] for k in allowed if k in event}, 'seconds': round(time.monotonic() - started, 4)})
    config = {'provider': 'openai', 'base_url': 'https://api.deepseek.com', 'model': 'deepseek-flash',
              'api_key': key, 'thinking_mode': 'enabled' if thinking else 'disabled', 'reasoning_effort': 'high',
              'max_tokens': 4096, 'stream_options_include_usage': True, 'max_run_turns': 10,
              'max_run_seconds': 180, 'max_retries': 1, 'enable_native_tools': native,
              'native_tools': ['read', 'bash', 'update_plan'], 'security': {'profile': 'balanced'}}
    agent = create_agent(config, resolve_system_prompt(config, cwd=str(directory)), str(directory), event_sink=observe)
    try:
        await agent.run(prompt)
        success = bool(check(directory)) if check else True
        if name == 'mixed':
            success = success and any(record.tool == 'bash' and record.status == 'success'
                                      and record.exit_code == 0 and 'CHECK_OK' in record.stdout
                                      for record in agent.context.records)
            success = success and bool(agent.task_plan['steps']) and all(step['status'] == 'completed' for step in agent.task_plan['steps'])
        if name == 'conflict':
            success = success and any(record.status == 'conflict' for record in agent.context.records)
        completions = [e for e in events if e['type'] == 'tool_completed' and e.get('success') and e.get('channel') == 'text' and e.get('flow') == 'delayed']
        overlapped = [e for e in completions if any(end['type'] == 'stream_finished' and end['turn'] == e['turn'] and end['seconds'] > e['seconds'] for end in events)]
        errors = [{'tool': r.tool, 'status': r.status, 'error': r.error[:600].replace(key, '[REDACTED]')} for r in agent.context.records if r.status != 'success']
        result = {'name': name, 'model': 'deepseek-flash', 'thinking': thinking,
                  'artifact_check': success, 'runtime_error': agent.last_error.replace(key, '[REDACTED]'),
                  'duration_seconds': round(time.monotonic() - started, 3),
                  'successful_stream_writes': len(completions), 'writes_before_stream_closed': len(overlapped),
                  'usage': agent.usage.to_dict(), 'events': events, 'errors': errors,
                  'reasoning_fields_preserved': sum('reasoning_content' in m for m in agent.messages),
                  'plan': agent.task_plan}
        return result
    finally:
        await agent.close()


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--case', choices=['all', 'predictable', 'mixed', 'conflict'], default='all')
    args = parser.parse_args()
    if not args.run:
        parser.print_help()
        return
    root = Path(__file__).resolve().parents[1] / 'temp/live-deepseek' / str(time.time_ns())
    root.mkdir(parents=True)
    key, results = load_key(), []
    if args.case in ('all', 'predictable'):
        results.append(await scenario(root, 'predictable', key,
            '这是隔离验收任务。不要读skill或联网。用3个独立tf-write标签创建alpha.txt、beta.txt、gamma.txt，'
            '内容分别严格为ALPHA、BETA、GAMMA，可末尾换行。无需回读，无需native工具。'
            '三个标签都关闭后用中文写约200字说明这三个文件的分工；不要把标签放在Markdown代码围栏里。',
            native=False, check=lambda d: all((d / f'{n}.txt').exists() and (d / f'{n}.txt').read_text().strip() == n.upper() for n in ['alpha','beta','gamma'])))
    if args.case in ('all', 'mixed'):
        def setup(d):
            (d / 'calc.py').write_text('def add(a, b):\n    return a - b\n', encoding='utf-8')
        results.append(await scenario(root, 'mixed', key,
            '隔离代码修复任务：calc.py的add应做加法但当前有bug。不要联网或读skill。先update_plan列2步和验收条件，'
            '然后必须用原生read读取calc.py，再用tf-edit或tf-write修复。随后用原生bash运行'
            ' python -c "from calc import add; assert add(2,3)==5; print(\'CHECK_OK\')" 验证。'
            '只有收到真实结果后才标记计划完成并引用成功工具回执id，最后简短交付。',
            thinking=True, setup=setup, check=lambda d: 'a + b' in (d / 'calc.py').read_text()))
    if args.case in ('all', 'conflict'):
        injected = [False]
        def setup(d):
            (d / 'notes.txt').write_text('用户记录：第一行\n', encoding='utf-8')
        def inject(event, d):
            if not injected[0] and event['type'] == 'tool_started' and event.get('tool') in ('write','edit','append'):
                with (d / 'notes.txt').open('a', encoding='utf-8') as f:
                    f.write('EXTERNAL_CHANGE_MUST_SURVIVE\n')
                injected[0] = True
        results.append(await scenario(root, 'conflict', key,
            '隔离任务：读取notes.txt完整内容后追加一行DONE，保留原有全部内容。不要读skill或联网。'
            '读用native read，写用tf-append。若执行器提示文件冲突，重新读取当前文件再追加；绝不能覆盖用户新内容。最后简短汇报。',
            thinking=True, setup=setup, inject=inject,
            check=lambda d: injected[0] and 'EXTERNAL_CHANGE_MUST_SURVIVE' in (d / 'notes.txt').read_text(encoding='utf-8') and (d / 'notes.txt').read_text(encoding='utf-8').count('DONE') == 1))
    report = {'endpoint': 'https://api.deepseek.com', 'results': results,
              'passed': all(item['artifact_check'] and not item['runtime_error'] for item in results)}
    destination = root / 'report.json'
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'report': str(destination), 'passed': report['passed'], 'cases': [
        {k: item[k] for k in ('name','artifact_check','duration_seconds','successful_stream_writes','writes_before_stream_closed','runtime_error')}
        for item in results]}, ensure_ascii=False), flush=True)
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    asyncio.run(main())
