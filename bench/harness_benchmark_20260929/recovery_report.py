"""Offline export of monitored diagnostics; keeps every selected run, including failures."""
from __future__ import annotations

import json
from pathlib import Path

from meter import summarize

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
GROUPS = {
    'debug-baseline-pricing': '原版', 'debug-baseline-inbox': '原版',
    'debug-fixed-pricing': '协议修复', 'debug-fixed-inbox': '协议修复',
    'debug-fixed-frame': '协议修复', 'debug-fixed-receipt': '协议修复',
    'debug-attached-pricing': '状态位置消融', 'debug-attached-inbox': '状态位置消融',
}


def collect():
    runs, experiments = [], {}
    for name, stage in GROUPS.items():
        directory = ROOT / 'artifacts/benchmark-20260929' / name
        if not directory.exists():
            continue
        experiment = {'stage': stage, 'manifest': json.loads((directory / 'manifest.json').read_text(encoding='utf8'))}
        ablation = directory / 'ablation.json'
        if ablation.exists():
            experiment['ablation'] = json.loads(ablation.read_text(encoding='utf8'))
        experiments[name] = experiment
        for destination in sorted(directory.iterdir()):
            if not destination.is_dir() or not (destination / 'result.json').exists():
                continue
            result = json.loads((destination / 'result.json').read_text(encoding='utf8'))
            meter = json.loads((destination / 'meter.json').read_text(encoding='utf8'))
            harness = json.loads((destination / 'harness.json').read_text(encoding='utf8'))
            totals = summarize(meter['requests'])
            if totals != result['usage']:
                raise ValueError('Recorded usage differs from request recomputation: ' + name + '/' + destination.name)
            trace = [json.loads(line) for line in (destination / 'trace.jsonl').read_text(encoding='utf8').splitlines()]
            normal = bool(result['grade']['passed'] and not result['modified_public_tests'] and
                          not result['timed_out'] and result['exit_code'] == 0 and
                          harness['stopped_reason'] == 'completed' and not harness['last_error'])
            runs.append({'experiment': name, 'stage': stage, 'result': result,
                         'normal_delivery': normal, 'requests': meter['requests'],
                         'trace': trace, 'harness': harness})
    return {'scope': 'Targeted debugging, not a replacement for the original 36-run benchmark',
            'experiments': experiments, 'runs': runs,
            'total_requests': sum(r['result']['usage']['api_requests'] for r in runs),
            'total_tokens': sum(r['result']['usage']['total_tokens'] for r in runs)}


def main():
    data = collect()
    target = HERE / 'reports'
    (target / 'recovery-results.json').write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    lines = ['# 过程监控与定向复测 · 2026-09-29', '',
             '所有样本保留；原36次成绩不变。这是缺陷诊断与候选方案消融，不是三harness重新排名。', '',
             '| 阶段 | 题目/重复 | 正常交付 | 产物 | 停止原因 | 请求 | 秒 | token |',
             '|---|---|---:|---:|---|---:|---:|---:|']
    for row in data['runs']:
        r = row['result']; u = r['usage']; g = r['grade']
        lines.append(f"| {row['stage']} | {r['task']} / {r['repeat']} | {'是' if row['normal_delivery'] else '否'} | "
                     f"{g['checks_passed']}/{g['checks_total']} | {r['stopped_reason']} | {u['api_requests']} | "
                     f"{r['seconds']:.2f} | {u['total_tokens']:,} |")
    lines += ['', f"本轮已完成诊断样本合计{len(data['runs'])}次、{data['total_requests']}请求、{data['total_tokens']:,} token。", '',
              '统一Flash high、请求8K、300秒/24请求；任务/隐藏验收未改。总token包含缓存输入，推理已计入输出。计量代理断流后有限读取取得usage；实际完整请求记录为权威，运行中活动字符数不是token估算。', '',
              '状态位置消融只将相同运行状态附在已有最后tool/user消息内，保留工具结果、全部reasoning及原提示，避免另加一条user回合；这是小样本相关证据，不能给出稳定提升比例或归因于单个补丁。', '',
              '[诊断与修复说明](../../../docs/benchmark-debug-20260929.md) · [完整逐请求/逐轮记录](recovery-results.json)', '']
    (target / 'recovery-summary.md').write_text('\n'.join(lines), encoding='utf8')
    print(json.dumps({'runs': len(data['runs']), 'requests': data['total_requests'], 'tokens': data['total_tokens']}))


if __name__ == '__main__':
    main()
