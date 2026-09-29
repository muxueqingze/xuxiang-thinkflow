# 2026-09-29 同模型 harness 实测

状态：36次正式运行完成。源码基线 `e49b717`，未运行 Claude Code。

后续[16次受监控的诊断](reports/recovery-summary.md)与[恢复修复说明](../../docs/benchmark-debug-20260929.md)单独记录，不替换本报告。当前源码已修复恢复链并让原生文件工具首轮可用；下述“冻结生产核心”指原36次阶段，复现该基准须还原e49b717核心及记录的装置指纹，不能把当前HEAD结果混入原样本。

**本组结果：OpenCode交付最稳定，Pi用量最低；ThinkFlow尚未表现出整体优势。** ThinkFlow正常交付7/12、产物通过10/12；Pi为10/12与11/12；OpenCode均为12/12。总token分别为2,540,386、978,364、1,916,744。即使只比较双方正常完成的同题配对，ThinkFlow对Pi的总token为2.513倍，对OpenCode为1.545倍。

这些结论只适用于本次同模型、单agent、8K请求参数和共同本地预算，不是默认桌面配置或行业排行榜。详细口径与全部失败见[结果表](reports/summary.md)，逐请求数据见[requests.json](reports/requests.json)，定向复核见[review.md](review.md)，已有证据支持的改进方向见[diagnosis.md](diagnosis.md)。

目标是比较当前 ThinkFlow、Pi、OpenCode 在同一 DeepSeek Flash 模型上的交付质量、耗时与完整 API 用量。这是本地定向软件工程套题，不是 SWE-bench，也不代表行业综合排名。

## 预先固定的方法

- 六道工具中立的 Python 工程题，覆盖多文件交付、已有代码修复、边界数据处理、持久状态、增量协议解析、跨文件重构。题意和隐藏验收在正式运行前冻结。
- 每个 harness、每道题运行两次全新会话；每次独立工作目录，不人工修复模型产物。保留失败和超时。
- 版本固定为 ThinkFlow 0.8.0、Pi `@earendil-works/pi-coding-agent` 0.87.1、OpenCode 1.18.33；不是旧命名空间中停留的Pi 0.73.1，也不覆盖本机已有Pi 0.80.2。
- 相同用户题目、模型、思考参数、请求的单次输出上限及运行预算；保留各 harness 自带系统提示和工具语义。比较单agent模式，屏蔽联网与委派工具，禁用用户扩展、用户技能和外部 MCP，不读取个人聊天历史；ThinkFlow内置harness使用指南作为原生机制保留。
- 统一请求 `deepseek-flash`、thinking enabled/high、temperature 0、max_tokens=8192，每次运行300秒/24次上游请求。实际观察到提供商usage超出请求max_tokens的情况，因此该参数不冒称为被强制执行的实际输出硬上限；报告保留全部真实用量并统计超限分布。每个harness自己的额外模型请求也计入预算。各自原生上下文压缩策略保留；模型缓存不能主动清空。
- 工作目录相互独立，但共用父Git仓库；不是独立Git根或操作系统隔离，也不声称完全无父项目元信息。
- 本机统一用量代理只连接官方 DeepSeek，真实密钥仅在代理内存；被测进程使用无价值的本地访问令牌。每个 API 请求单独计数，不只统计最后一轮。
- 记录输入总 token、缓存命中、非缓存输入、输出及其中的推理 token。缓存不假定免费，不把推理 token 再加到输出上。使用提供商真实 usage，缺失明确标记，禁止估算填零。
- 流式响应立即透传；若客户端提早断流，计量代理继续有限地读取该请求以取得末尾 usage，记录 abandoned 状态和额外观测时长。因此报告 token 是此观测模式实际消耗，不能冒充直接取消连接的生产成本。
- 不接入正式桌面配置，不自动运行网络测试。必须显式 `--run` 才会产生模型请求。

## 接力

已完成：现场确认；旧基准的 Pi 仅末轮用量问题已识别，因此不复用旧结果。三个harness真实连通/写文件/执行验证通过。独立审查的断流漏计、缺失usage、混合版本恢复、公共测试保护等原问题已修并经7项无网络回归复核。

题目已冻结为`2026-09-29.1`，6题48检查；`tasks.py` SHA256=`19129130bcbd9c9518ba849376fe32c4f636d1bca668f0960d060d299dfd44f6`。参考解与错误解、原子保存故障、CLI启动隔离均有验证。

正式36次、348个上游请求全部结束，输入/输出/缓存/推理细分均完整；新增15项离线装置与判题回归通过。原生产执行器保持冻结。最终汇总复核与GitHub状态见本目录review及PR当前head。

文件边界：`tasks.py` 由出题代理负责，`meter.py`/`run.py`/`adapters.py` 由主会话负责；所有运行产物放仓库忽略的 `artifacts/benchmark-20260929/`，整理后的脱敏记录和报告归本目录。

回退：不修改生产执行器或既有测试断言，不覆盖旧基准；测试入口仅注册新增离线基准回归。若适配或验收器失败，先修实验装置并单独标注 pilot，正式结果不可择优替换。

## 复现（Windows）

先准备Python项目依赖与Node22.19以上，在仓库根执行：

```powershell
npm --prefix bench/harness_benchmark_20260929 ci
npm install --global opencode-ai@1.18.33
python -B bench/harness_benchmark_20260929/run.py --run --pilot --name connectivity
python -B bench/harness_benchmark_20260929/run.py --run --name formal
python -B bench/harness_benchmark_20260929/analyze.py artifacts/benchmark-20260929/formal
python -B bench/harness_benchmark_20260929/regrade.py repair_routes-thinkflow-r1
```

提供`DEEPSEEK_API_KEY`环境变量，或本机已配置的用户级DPAPI凭据；不要把key写入配置或命令参数。不带`--run`只显示帮助。新实验使用不同`--name`；同名恢复要求源码、任务、配置、版本与预算指纹完全一致，任何变化都拒绝混入旧结果。

`regrade.py`从已发布的非执行JSON产物恢复指定样本，在临时目录离线重验，不调用模型。所有正式样本已保留；隐藏断言通过率不等于业务完成百分比，以整题通过及正常终态作为主要判断。

## 实际消耗与本机依赖

- 正式阶段：348请求、5,435,494总token（4,981,712输入，453,782输出）；4,460,288输入token命中缓存，推理285,573已包含在输出中。
- 连通准备阶段：12个真实上游请求、51,913总token；Pi首个适配失败在本机被拒，未请求提供商。连通样本不混入正式成绩。
- 本轮真实测试合计360个上游请求、5,487,407总token。离线fixture的合成usage不计入。未用未经核实的单价换算金额。
- 新增OpenCode 1.18.33于用户npm目录；Pi0.87.1与锁定依赖只装在本基准目录node_modules，原全局Pi0.80.2保留。npm命令目录原已在用户PATH，本轮未修改PATH。
- 本地完整日志与工作目录保留于忽略的`artifacts/benchmark-20260929/`；发布内容只含合成任务、评分、provider用量和JSON代码快照，不含真实密钥、个人配置或完整模型思考。

官方参考：[Pi模型配置](https://raw.githubusercontent.com/earendil-works/pi/v0.87.1/packages/coding-agent/docs/models.md)、[Pi CLI](https://pi.dev/docs/latest/cli)、[OpenCode CLI](https://opencode.ai/docs/cli/)、[OpenCode自定义provider](https://opencode.ai/docs/providers/)。API用量取自实际响应，不使用客户端填入的价格估计。
