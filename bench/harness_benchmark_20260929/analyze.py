"""Offline report from immutable results and provider usage; never calls a model."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import sys

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from meter import summarize
from tasks import TASKS


def terminal_state(run_directory, result):
    """Normalize public terminal events, without copying model text or thinking."""
    if result['harness']=='thinkflow':
        reason=result.get('stopped_reason')
        return {'observed':reason is not None,'reason':reason,'normal':reason=='completed','source':'harness.json'}
    last=None
    with (run_directory/'stdout.jsonl').open(encoding='utf-8') as stream:
        for number,line in enumerate(stream,1):
            try:event=json.loads(line)
            except ValueError:continue
            if result['harness']=='pi' and event.get('type')=='message_end' and event.get('message',{}).get('role')=='assistant':
                message=event['message']
                last={'reason':message.get('stopReason'),'error':bool(message.get('errorMessage')),'event':'message_end','line':number}
            elif result['harness']=='opencode' and event.get('type')=='step_finish':
                last={'reason':event.get('part',{}).get('reason'),'error':False,'event':'step_finish','line':number}
            elif event.get('type')=='error':
                last={'reason':'error','error':True,'event':'error','line':number}
    return {'observed':last is not None,'normal':bool(last and last['reason']=='stop' and not last['error']),
            'source':'stdout.jsonl',**(last or {'reason':'unknown'})}


def analyze(directory):
    plan=json.loads((directory/'plan.json').read_text(encoding='utf-8'))
    manifest=json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
    if len({j['id'] for j in plan}) != len(plan):
        raise ValueError('Duplicate run identity in experiment plan')
    actual_jobs={(j['task'],j['harness'],j['repeat']) for j in plan}
    if len(actual_jobs)!=len(plan):
        raise ValueError('Duplicate task/harness/repeat in experiment plan')
    expected_jobs={(task,harness,repeat) for task in TASKS for harness in ('thinkflow','pi','opencode') for repeat in (1,2)}
    complete_suite=actual_jobs==expected_jobs and not manifest.get('pilot')
    results=[]
    meters={}
    limit_observations=[]
    for job in plan:
        result=json.loads((directory/job['id']/'result.json').read_text(encoding='utf-8'))
        meter=json.loads((directory/job['id']/'meter.json').read_text(encoding='utf-8'))
        if any(result.get(k)!=v for k,v in job.items()):
            raise ValueError('Run identity mismatch: '+job['id'])
        actual=summarize(meter['requests'])
        if actual!=result['usage']:
            raise ValueError('Saved result differs from final meter: '+job['id'])
        if result.get('infrastructure_error'):
            raise ValueError('Experiment infrastructure failed: '+job['id'])
        if not actual['usage_complete']:
            raise ValueError('Incomplete billing observation; do not rank tokens: '+job['id'])
        if not all(r.get('response_model')==manifest['generation']['model'] for r in meter['requests']):
            raise ValueError('Unexpected provider-reported model: '+job['id'])
        result['terminal']=terminal_state(directory/job['id'],result)
        result['delivery_passed']=bool(result['grade']['passed'] and result['exit_code']==0 and not result['timed_out']
                                      and result['terminal']['normal'])
        results.append(result)
        meters[job['id']]=meter
        for request in meter['requests']:
            if request['usage']['completion_tokens']>manifest['generation']['max_tokens']:
                limit_observations.append({'run_id':job['id'],'harness':job['harness'],'request':request['index'],
                                          'requested_max_tokens':manifest['generation']['max_tokens'],
                                          'reported_completion_tokens':request['usage']['completion_tokens'],
                                          'reported_reasoning_tokens':(request['usage'].get('completion_tokens_details') or {}).get('reasoning_tokens')})
    # All participants must have received the same exact user prompt for a task.
    tasks=sorted({r['task'] for r in results})
    for task in tasks:
        if len({r['prompt_sha256'] for r in results if r['task']==task})!=1:
            raise ValueError('Task prompts differ: '+task)
    aggregates={}
    for harness in ('thinkflow','pi','opencode'):
        rows=[r for r in results if r['harness']==harness]
        if not rows:
            continue
        aggregates[harness]={'runs':len(rows),'passed':sum(r['delivery_passed'] for r in rows),
            'artifact_passed':sum(r['grade']['passed'] for r in rows),
            'output_tokens_on_abandoned_requests':sum(req['usage']['completion_tokens'] for row in rows
                for req in meters[row['id']]['requests'] if req.get('client_disconnected')),
            'cache_complete':all(r['usage']['cache_usage_complete'] for r in rows),
            'reasoning_complete':all(r['usage']['reasoning_reported_requests']==r['usage']['api_requests'] for r in rows),
            'mean_check_score':statistics.mean(r['grade']['checks_passed']/r['grade']['checks_total'] for r in rows),
            'mean_seconds':statistics.mean(r['seconds'] for r in rows),
            'median_seconds':statistics.median(r['seconds'] for r in rows),
            'mean_api_requests':statistics.mean(r['usage']['api_requests'] for r in rows),
            'writes_before_stream_finished':sum(r.get('writes_before_stream_finished',0) for r in rows),
            'tokens':{k:sum(r['usage'][k] for r in rows) for k in
                      ('prompt_tokens','cached_tokens','uncached_tokens','completion_tokens','reasoning_tokens',
                       'total_tokens','api_requests','abandoned_requests','failed_requests')}}
    paired={}
    for opponent in ('pi','opencode'):
        pairs=[]
        for left in (r for r in results if r['harness']=='thinkflow' and r['delivery_passed']):
            right=next((r for r in results if r['harness']==opponent and r['task']==left['task'] and r['repeat']==left['repeat'] and r['delivery_passed']),None)
            if right:
                pairs.append((left,right))
        if pairs:
            paired[opponent]={'successful_pairs':len(pairs),
                'thinkflow_total_token_ratio':sum(a['usage']['total_tokens'] for a,b in pairs)/sum(b['usage']['total_tokens'] for a,b in pairs),
                'thinkflow_output_token_ratio':sum(a['usage']['completion_tokens'] for a,b in pairs)/sum(b['usage']['completion_tokens'] for a,b in pairs),
                'thinkflow_wall_time_ratio':sum(a['seconds'] for a,b in pairs)/sum(b['seconds'] for a,b in pairs)}
    return {'complete_suite':complete_suite,'manifest':manifest,'limit_observations':limit_observations,
            'aggregates':aggregates,'paired_success_only':paired,'results':results},meters


def render(data):
    lines=['# ThinkFlow / Pi / OpenCode 同模型基准 · 2026-09-29','',
           f"同一官方 DeepSeek Flash，高思考；本次{len({r['task'] for r in data['results']})}题、{len(data['aggregates'])}个harness，共{len(data['results'])}次独立运行。没有使用 Claude Code。",
           '正式套题完整：六题各两次、三个harness。' if data['complete_suite'] else '**仅部分数据，未覆盖完整正式套题，不可作为完整比较结论。**',
           '先看完成质量，再看资源消耗。此小规模本地套题不能推出行业综合排名。','',
           '| Harness | 正常交付 | 产物通过 | 检查平均分 | 平均秒 | 中位秒 | 平均API请求 | 总token |',
           '|---|---:|---:|---:|---:|---:|---:|---:|']
    for name,a in data['aggregates'].items():
        lines.append(f"| {name} | {a['passed']}/{a['runs']} | {a['artifact_passed']}/{a['runs']} | {a['mean_check_score']:.1%} | {a['mean_seconds']:.2f} | {a['median_seconds']:.2f} | {a['mean_api_requests']:.2f} | {a['tokens']['total_tokens']:,} |")
    lines+=['','产物通过要求隐藏检查全部通过且公共测试未被修改；正常交付还要求无超时、退出码0，以及观察到正常终态：ThinkFlow completed、Pi最后assistant stop、OpenCode最后step_finish stop。',
            '检查分数仅为断言通过率，不是业务完成百分比。例如缺失CLI也可能通过“非零退出且未覆盖输出”的错误路径检查，因此以整题通过为主判。']
    lines += ['', '## Token 分解', '', '| Harness | 输入（含缓存） | 缓存命中 | 非缓存输入 | 输出（含推理） | 其中推理 | 提早断流请求 |',
              '|---|---:|---:|---:|---:|---:|---:|']
    for name,a in data['aggregates'].items():
        t=a['tokens']
        cache=f"{t['cached_tokens']:,}" if a['cache_complete'] else f"未知（已报告部分 {t['cached_tokens']:,}）"
        uncached=f"{t['uncached_tokens']:,}" if a['cache_complete'] else f"未知（已报告部分 {t['uncached_tokens']:,}）"
        reasoning=f"{t['reasoning_tokens']:,}" if a['reasoning_complete'] else f"未知（已报告部分 {t['reasoning_tokens']:,}）"
        lines.append(f"| {name} | {t['prompt_tokens']:,} | {cache} | {uncached} | {t['completion_tokens']:,} | {reasoning} | {t['abandoned_requests']} |")
    lines += ['', '总token = 输入 + 输出；推理已经包含在输出中。非缓存输入不是账单价格，缓存也不假定免费。',
              '全部实际请求均有完整输入/输出usage，包括重试和附加模型请求；细分字段缺失时标为未知。用量代理在下游断流后有限drain上游以收取末尾usage；这里是该观测模式的实际消耗，并非直接取消连接的生产成本。','',
              '断流请求涉及的完整输出token：'+ '、'.join(f"{name} {a['output_tokens_on_abandoned_requests']:,}" for name,a in data['aggregates'].items())+'。这些包含断流前已生成的部分，不能全部当作观测额外开销；当前无法精确切分。','',
              '## 每次运行','', '| 题目 | Harness | 重复 | 检查 | 正常交付 | 执行状态 | 秒 | API | token |',
              '|---|---|---:|---:|---|---|---:|---:|---:|']
    for r in sorted(data['results'],key=lambda r:(r['task'],r['harness'],r['repeat'])):
        g=r['grade']
        state='timeout' if r['timed_out'] else f"exit={r['exit_code']}/{r['terminal']['reason']}"
        lines.append(f"| {r['task']} | {r['harness']} | {r['repeat']} | {g['checks_passed']}/{g['checks_total']} | {'是' if r['delivery_passed'] else '否'} | {state} | {r['seconds']:.2f} | {r['usage']['api_requests']} | {r['usage']['total_tokens']:,} |")
    lines += ['', '## 同题同重复且双方都成功的配对', '',
              '| 对手 | 配对数 | ThinkFlow/对手 总token | 输出token | 用时 |',
              '|---|---:|---:|---:|---:|']
    for name,p in data['paired_success_only'].items():
        lines.append(f"| {name} | {p['successful_pairs']} | {p['thinkflow_total_token_ratio']:.3f} | {p['thinkflow_output_token_ratio']:.3f} | {p['thinkflow_wall_time_ratio']:.3f} |")
    lines += ['', '比值是成功配对集合总量之比，不是逐对比值的平均；小于1表示ThinkFlow消耗更少。失败样本仍保留在主表，不能用失败的低消耗包装效率。','',
              '## 失败项','']
    failures=[r for r in data['results'] if not r['delivery_passed']]
    for r in failures:
        reasons=[x['check']+': '+x.get('error','') for x in r['grade']['details'] if not x['passed']]
        if r.get('modified_public_tests'):reasons.append('修改公共测试：'+', '.join(r['modified_public_tests']))
        if r['timed_out']:reasons.append('运行超时')
        if r['exit_code']!=0:reasons.append('进程退出码 '+str(r['exit_code']))
        if not r['terminal']['normal']:reasons.append('观察到的终态 '+str(r['terminal']['reason']))
        lines.append(f"- `{r['id']}`："+'；'.join(reasons or ['未满足交付条件，见逐次记录']))
    if not failures:lines.append('本组完整通过；题目仍可能存在难度上限，不能据此宣称真实大仓库任务全部可靠。')
    lines+=['','## 提供商输出上限观测','',
            f"统一请求max_tokens={data['manifest']['generation']['max_tokens']}，实际有{len(data['limit_observations'])}个请求的提供商completion_tokens报告超过该值。"]
    for name in data['aggregates']:
        found=[r for r in data['limit_observations'] if r['harness']==name]
        lines.append(f"- {name}：{len(found)}个请求，涉及{len({r['run_id'] for r in found})}次运行。")
    lines+=['','原始usage完整保留，未截断或剔除。原因未确认，不归咎于特定harness；实验统一的是请求参数和本地时间/请求次数预算，不能声称提供商实际输出预算被严格强制相同。']
    lines+=['','## 范围与复现','',
            '- 保留各自原生系统提示、工具协议与压缩策略。ThinkFlow使用cmd.exe；Pi/OpenCode使用Git Bash。比较的是这些固定配置的整体harness，不单独归因于流式执行。',
            '- 每次新会话、固定种子交错顺序，题目、代码、版本与参数均在正式执行前冻结；没有按成绩重试或人工修改产物。提供商缓存不能强制清空，缓存量单列。',
            '- 各次运行使用独立工作目录，但位于同一父Git仓库的artifacts下，未做独立Git根或操作系统隔离。已关闭个人配置/自动上下文入口并要求限当前目录；原生项目元信息可能受父Git项目识别影响，不声称完全无父项目上下文。',
            '- 六题、两次重复样本有限；只覆盖标准库软件工程功能与边界，不覆盖前端审美、超大仓库、长期协作、MCP或安全隔离。',
            '- 隐藏验收在独立进程，未向被测模型提供答案；这不是防恶意作弊的操作系统沙箱。',
            '- `results.json`保存全部评分与汇总，`requests.json`保存逐请求原始usage，`solutions.json`以非执行数据保存Python产物，供离线复核；真实凭据与完整模型思考未发布。',
            '- 运行方法与参数见本目录README；`analyze.py`只读取已有结果，不请求模型。','']
    return '\n'.join(line.rstrip() for line in lines)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('experiment',type=Path)
    parser.add_argument('--publish',action='store_true')
    args=parser.parse_args()
    data,meters=analyze(args.experiment.resolve())
    if args.publish and not data['complete_suite']:
        raise ValueError('Only the complete predeclared suite can be published as the formal report')
    output=HERE/'reports' if args.publish else args.experiment/'report'
    output.mkdir(parents=True,exist_ok=True)
    for name,value in [('results.json',data),('requests.json',meters)]:
        (output/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (output/'summary.md').write_text(render(data),encoding='utf-8')
    if args.publish:
        solutions={}
        for result in data['results']:
            source=args.experiment/result['id']/'workspace'
            files={}
            for file in source.rglob('*'):
                if file.is_file() and file.suffix=='.py' and not file.is_symlink() and file.stat().st_size<=200_000:
                    files[str(file.relative_to(source)).replace('\\','/')]=file.read_text(encoding='utf-8')
            solutions[result['id']]={'task':result['task'],'files':files}
        (output/'solutions.json').write_text(json.dumps(solutions,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(data['aggregates'],ensure_ascii=False,indent=2))


if __name__=='__main__':main()
