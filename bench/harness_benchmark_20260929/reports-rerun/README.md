# 修复后的完整三方复测 · 2026-09-29

ThinkFlow恢复稳定性在本轮改善，整体效率仍未领先。六道原题各两次，三个harness重新运行，共36次，没有用旧对手数据拼接新排名。原轮与16次诊断均保留。

| Harness | 产物全过且正常结束 | 最终代码上测试成功有证据 | 平均秒 | 中位秒 | 请求 | 总token |
|---|---:|---:|---:|---:|---:|---:|
| ThinkFlow | 12/12 | 6/12 | 76.89 | 74.25 | 108 | 2,123,456 |
| Pi | 10/12 | 10/12 | 49.33 | 43.20 | 114 | 1,264,618 |
| OpenCode | 12/12 | 12/12 | 60.37 | 55.87 | 120 | 1,898,716 |

本表采用原基准的判定：隐藏验收全过、公共测试未修改、进程成功、无超时且原生终态正常。**它不额外保证模型主动执行了任务要求的测试，也不是生产就绪认证。** 过程缺口见下文与[独立复核](../../../docs/reviews/benchmark-rerun-review.md)。

测试证据列是额外过程核查，要求测试发生在最后实现修改之后，依据真实工具回执/输出；未知不重判为隐藏验收失败，也不保证模型自编测试的覆盖质量。ThinkFlow三份不可靠壳命令又缺少成功输出，不能确认测试通过；Pi/OpenCode的相关管道命令有保留的测试通过输出，因此不只依赖shell退出码。

## 与上一轮相比

ThinkFlow核心从e49b717更新为34119c6。已修复误通道恢复、截断/停止、unknown回执重放、短证据引用，并恢复原生文件工具首轮可用。真实执行期间所有冻结文件指纹保持一致，没有修改任务、核心或按成绩重试。

| ThinkFlow指标 | 原轮 | 本轮 |
|---|---:|---:|
| 产物全过且正常结束 | 7/12 | 12/12 |
| 仅产物全过 | 10/12 | 12/12 |
| 总token | 2,540,386 | 2,123,456 |
| 请求 | 122 | 108 |
| 平均秒 | 82.00 | 76.89 |
| 中位秒 | 64.05 | 74.25 |

总token观察值减少16.4%，平均时间减少6.2%，但中位时间增加15.9%。只看新旧均正常成功的同题同重复7对，总token为旧轮的0.907倍（1,287,165 / 1,419,067），总用时为0.998倍，基本持平。不能只用全体均值宣称提速。

同题配对为durable_inbox-r1、repair_routes-r1/r2、interval_coverage-r1/r2、pricing_refactor-r1、frame_decoder-r2。两轮存在生成波动、缓存和监控观测差异；这是重复实测，不能把全部变化严格归因于某个补丁。

## 与本轮对手相比

- 对OpenCode，双方12对均正常成功：ThinkFlow总token多11.8%，输出token多74.7%，总用时多27.4%。
- 对Pi，只比较双方正常成功的10对：ThinkFlow总token多20.2%，输出token多44.8%，总用时多37.9%。Pi两次失败仍留在主表，不能用提前失败的低用量包装效率。
- ThinkFlow观察到30次成功delayed文件操作在同轮流关闭前完成，包含write/edit等文件操作。这证明重叠仍发生，不证明整体更省或更快。

## 工作流程与失败

- Pi的durable_inbox-r1为0/8，pricing_refactor-r1为3/8；两次原生终态均为length，核心代码未完成。进程exit 0不能当成模型正常结束。8K请求预算对这组结果有明显影响，不外推为其他预算或框架默认能力。
- ThinkFlow的repair_routes-r2和receipt_package-r2没有任何bash调用，只读取/写文件，没有主动运行要求测试的证据。后者仅3请求、20,997 token，不能把漏测当成完整流程提速。
- ThinkFlow的frame_decoder-r1主动发起过测试、附加测试曾明确失败；最终管道/echo组合命令可掩盖测试退出码，成功输出未保存，现有证据不足以证明最终测试通过。外部grader 8/8仍单独保留。
- ThinkFlow的durable_inbox-r2、repair_routes-r1也缺少最终测试可靠成功的证据；interval_coverage-r2曾通过公共测试、附加测试失败后又修改实现与测试，之后没有复验。独立复核将12份分为：6份最终实现上测试成功有可靠证据、3份仅壳命令成功、1份修改后未复验、2份未执行测试。证据不足不改写为隐藏验收失败，但不能声称全部完成验证流程。
- 附加测试由模型自行生成和修改，执行成功不额外证明测试质量。完整测试执行证据与未决项以独立复核为准；本轮不为获得更好成绩临时改评分或补跑样本。

## 用量与可比性

本轮342个真实请求、5,286,790总token。输入4,839,235，其中缓存命中4,381,952、非缓存457,283；输出447,555，其中推理270,046已包含在输出。全部usage完整，HTTP失败、客户端断流均为0。没有连通pilot或其他额外模型调用；离线复核不计入API用量。总token不等于现金费用。

固定官方DeepSeek Flash、thinking enabled/high、请求max_tokens 8192、每次24请求/300秒。ThinkFlow0.8.0（34119c6）、Pi0.87.1、OpenCode1.18.33，Python3.12.10、Node24.15.0、Windows；没有使用Claude Code或Pro。300秒约束执行及新请求，计量代理收取末尾usage的观测时间另列。

任务、meter、adapters与原轮指纹相同；提示完全相同的同题输入、固定交错顺序、原生系统提示/工具及shell差异保留。相较原轮，run/thinkflow_worker增加了过程监控，因此不是只改变一个核心变量的实验。源码和配置指纹见results.json的manifest。

OpenCode有2条服务商completion_tokens超过请求8192，分别为frame_decoder-r1请求3的9,303和pricing_refactor-r2请求3的9,579。未剔除或截断，原因未确认；实验统一请求参数，不声称服务商实际计算预算被严格强制相同。提供商缓存不能清空，thinking模式下temperature=0也不保证确定性。

六道本地标准库工程题、每题两次，不是SWE-bench或行业排行榜。独立工作目录仍在同一父Git仓库内，未做OS隔离；隐藏验收不等于防恶意作弊的沙箱。较高交付率仍需更大任务和不同预算验证。

## 数据与复核

- [完整结果、逐题失败与成功配对](summary.md)
- [评分与manifest](results.json)、[逐请求服务商usage](requests.json)
- [非执行格式的产物源码](solutions.json)、[ThinkFlow执行回执与工具事件](thinkflow-events.json)
- [逐样本测试执行证据](verification-evidence.json)
- [原轮成绩](../reports/summary.md)、[16次恢复诊断](../reports/recovery-summary.md)
- [本轮接力](../../../docs/benchmark-rerun-20260929.md)、[独立复核](../../../docs/reviews/benchmark-rerun-review.md)

完整原始工作目录保留在artifacts/benchmark-20260929/formal-recovery-34119c6，不发布凭据、个人配置或完整模型思考。

独立审查从原始请求重新计算全部用量、公开终态、同题prompt/公共测试完整性和配对，并抽验三份JSON产物离线评分（8/8、8/8、3/8）与原记录逐项一致。没有重新运行模型或用补测覆盖原来的流程缺口。

离线重验已发布产物（不调用模型）：

```powershell
python -B bench/harness_benchmark_20260929/regrade.py receipt_package-thinkflow-r2 --reports bench/harness_benchmark_20260929/reports-rerun
```

后续优先补强可核对的测试执行、退出码与结果保留、无进展收尾及长思考效率。本轮只复测与报告，没有继续修改被测核心或重打桌面包。
