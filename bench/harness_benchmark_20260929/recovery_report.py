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
    'debug-native-pricing': '开放原生文件工具', 'debug-native-inbox': '开放原生文件工具',
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
    usage = summarize([request for row in runs for request in row['requests']])
    return {'scope': 'Targeted debugging, not a replacement for the original 36-run benchmark',
            'experiments': experiments, 'runs': runs,
            'usage': usage, 'total_requests': usage['api_requests'],
            'total_tokens': usage['total_tokens']}


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
    lines += ['', '## 同题同次数比较', '',
              '仅比较pricing_refactor、durable_inbox各两次，frame/receipt不混入。正常交付沿用原口径：产物全过、公共测试未修改、进程成功、无超时、completed且无last_error。它不额外保证模型主动执行了要求的测试。', '',
              '| 方案 | 正常交付 | 请求 | token | 流关闭前成功文件操作 |',
              '|---|---:|---:|---:|---:|']
    for stage in ('协议修复', '状态位置消融', '开放原生文件工具'):
        rows = [row for row in data['runs'] if row['stage'] == stage and
                row['result']['task'] in ('pricing_refactor', 'durable_inbox')]
        usage = summarize([request for row in rows for request in row['requests']])
        writes = sum(row['result'].get('writes_before_stream_finished', 0) for row in rows)
        lines.append(f"| {stage} | {sum(row['normal_delivery'] for row in rows)}/{len(rows)} | "
                     f"{usage['api_requests']} | {usage['total_tokens']:,} | {writes} |")
    lines += ['', '状态位置消融保留相同状态文本、工具结果、全部reasoning及原提示，只在末条为字符串tool/user时附入该消息；其他形状回退为新增user消息。它没有改善正常收尾，未进入生产，不能认定额外user状态是主要原因。', '',
              '原生文件工具可见实验只改变首轮schemas：流式仍由原提示优先引导，原生保底始终可用，显式enable/allow/deny不变。对应四次总token比协议修复组少45.0%，这是观察值，不能当作稳定收益或三harness新排名。样本数少、运行时段/缓存不同，且thinking模式的temperature=0不保证确定性。该可用性改动已采纳。', '',
              '**验证缺口：** 开放原生工具的pricing第1次虽然外部grader 8/8、正常结束，却没有bash调用，也就没有模型主动执行测试的证据。第2次仍花19请求、564,981 token。四次保留14次与输出重叠的流式文件操作（含edit），不能据此声称整体效率问题全部解决。', '',
              '## 完整用量与来源', '',
              f"本轮诊断合计{len(data['runs'])}次、{data['total_requests']}请求、{data['total_tokens']:,} token。"]
    usage = data['usage']
    lines += [f"输入{usage['prompt_tokens']:,}（缓存{usage['cached_tokens']:,}、非缓存{usage['uncached_tokens']:,}），"
              f"输出{usage['completion_tokens']:,}，其中推理{usage['reasoning_tokens']:,}已计入输出；"
              f"{usage['usage_reported_requests']}/{usage['api_requests']}请求有usage、{usage['failed_requests']}个HTTP失败，"
              f"{usage['abandoned_requests']}次客户端断流通过有限读取取得usage。", '',
              '统一Flash high、请求8K、300秒/24请求；任务/隐藏验收未改。总token包含缓存输入，不等于现金费用。逐请求记录为权威，运行中的活动字符数不是token估算。', '',
              '原版核心为e49b717；协议修复与消融的源码指纹在完整记录的experiments/manifest内，消融另含脚本指纹。0f02aaa保留采纳可见性改动之前的修复核心；当前HEAD已采纳可见性，直接在当前HEAD运行message/attached不再等同于历史隐藏schema对照。复现实验必须先恢复对应源码与脚本指纹，使用新的实验目录；报告导出不调用模型。', '',
              '[诊断与修复说明](../../../docs/benchmark-debug-20260929.md) · [完整逐请求/逐轮记录](recovery-results.json)', '']
    (target / 'recovery-summary.md').write_text('\n'.join(lines), encoding='utf8')
    print(json.dumps({'runs': len(data['runs']), 'requests': data['total_requests'], 'tokens': data['total_tokens']}))


if __name__ == '__main__':
    main()
