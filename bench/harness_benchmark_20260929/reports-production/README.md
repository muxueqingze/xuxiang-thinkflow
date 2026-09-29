# 不额外限额的生产配置实测 · 2026-09-29

**移除8K后，三个harness都完成了12/12有效样本；ThinkFlow仍没有效率优势。** 它的有效样本总token为Pi的2.752倍、OpenCode的2.220倍，平均用时为1.929倍和1.759倍。本轮没有Claude Code或Pro评审。

| Harness | 有效样本正常交付 | 有效样本总token | 平均秒 | API请求 |
|---|---:|---:|---:|---:|
| ThinkFlow | 12/12 | 3,845,627 | 105.38 | 155 |
| Pi | 12/12 | 1,397,532 | 54.62 | 123 |
| OpenCode | 12/12 | 1,732,556 | 59.90 | 115 |

这是原六道Python工程题各两次的新配置实测；不覆盖原GitHub的前端/长篇小说场景，不据此宣布理念成败或行业排名。三个harness都达到这组六题的正确性上限，主要差异在时间、资源和执行过程。

## 原始尝试与实际消耗

原36次中35次正常交付，ThinkFlow的 `repair_routes-thinkflow-r2` 虽产物8/8，却因父进程GBK进度日志遇到U+FFFD而被启动器终止，不能算正常交付。到第22项结束的任务边界暂停后，以UTF-8控制台恢复同一冻结源码，已完成样本全数跳过。只有该观察器事故在独立目录补测一次；没有普通模型失败后择优重跑，也没有覆盖原数据。

补测是同一源码、模型、题目、prompt哈希、重复编号和生成条件，已正常完成8/8。原失败4请求、47,031 token和56.344秒完整保留。

**实际共37次尝试、397请求、7,022,746 token**：ThinkFlow含中断尝试3,892,658，Pi 1,397,532，OpenCode 1,732,556。有效36项为393请求、6,975,715 token。全部输入/输出usage完整；实际有1次下游断流，其完整输出338 token包括断流前生成，不能全算作观察器额外开销。计量代理收取上游最终usage的行为已计入，不能冒充立即断开上游连接的成本。

[事故记录](observer-incident.json) · [独立事故复核](../../../docs/reviews/production-observer-incident.md) · [完整结果和token分解](summary.md)

## 已移除的条件

- 实际请求不携带 `max_tokens`、`max_completion_tokens` 或 `max_output_tokens`。没有换成另一大数字，也没有由代理偷偷替客户端删除。
- 不设实验总时限、请求数、自动续写次数预算；三个harness的自动上下文压缩/清理关闭。常规工具分页、权限校验、错误恢复和网络连接错误处理保留。
- Pi/OpenCode的能力元数据由人工128K换为官方模型列表的1,048,576上下文、393,216最大输出；这些数值没有作为请求预算发送。模型列表返回名称为DeepSeek-V4.1-Flash，请求/响应ID为deepseek-flash。
- 省略输出字段仍受服务商自身默认和物理容量约束。官方文档当前说明thinking/high默认64K；本轮没有触达该默认，实际最大响应23,383 token。[服务商协议](https://api-docs.deepseek.com/api/create-chat-completion/)
- 保留原生工具与系统提示。Pi/OpenCode唯一显式扩展只移除SDK输出预算；个人配置、插件、技能、联网/委派工具仍隔离。ThinkFlow/Pi恢复原生默认重试，ThinkFlow移除原先未生效的图像工具禁用拼写。

397次实际请求逐条核验，输出预算字段全缺失，转发前后messages哈希全部一致；17次响应超过旧8K，**length截断0次**。没有把长期记忆丢弃或上下文压缩包装成节省token。

## 与上一轮8K结果比较

| Harness | 正常交付，旧→新有效 | 总token，旧→新有效 | 平均时间变化 |
|---|---|---|---:|
| ThinkFlow | 12/12 → 12/12 | 2,123,456 → 3,845,627（+81.1%） | +37.1% |
| Pi | 10/12 → 12/12 | 1,264,618 → 1,397,532（+10.5%） | +10.7% |
| OpenCode | 12/12 → 12/12 | 1,898,716 → 1,732,556（−8.8%） | −0.8% |

这是两组完整观察值，不是只改变单个参数的因果实验：输出、上下文/运行预算、重试配置、一个未使用的工具schema和随机生成行为均有差别；补测也改变了该样本的执行顺序。服务商缓存不能清空，缓存量单列。此前Pi两个截断后未实现的样本，本轮均成功；不能把其旧失败的低消耗当作效率优势。

## 具体损耗与反思

8K/24请求/300秒是测试装置添加的条件，不是原项目必需设计。把这套受限实验用来解释正常生产表现，偏离了需求；在实际请求体核验之前反复付费重跑，也增加了不必要的成本。本次先离线验证三个真实客户端，再冻结运行，完整公开负面结果。

取消截断后，ThinkFlow的真实问题更清楚：流式标签与原生函数名混用带来恢复回合；部分任务在测试通过后仍修订自建测试或做多轮低价值探测。长历史反复进入请求，使这些多余回合的输入成本很高。不能把所有测试或完整历史都称作浪费，也没有证据证明native bash丢失stdout：七组实际输出的离线回放全部保留。

例如frame_decoder-r1共677,938 token，第10–19轮测试扩展/修订及收尾占472,236；receipt_package-r2共635,185 token，第8–19轮探测占344,761。阶段实际消耗不等于可以直接删掉的反事实节省量。后续应针对协议恢复和验收后收尾减少多余往返，不再靠输出/上下文额度压成绩。[独立案例拆解](../../../docs/reviews/production-cost-cases.md)

ThinkFlow有25次成功文件操作先于同轮stream结束，分布于7个有效样本，机制确实运行；整体资源效率尚未胜出。

## 验证与复现

被测源码冻结于 `91e491167564f625334b812d6f0a422d2315b23e`。原始与补测manifest完全相同，全部生成结束后、后置日志修补前38个冻结文件指纹仍匹配。之后仅给观察器加ASCII JSON日志和非致命progress异常处理，离线回归通过；这些后置修补不伪装成被测版本。

三类客户端真实离线抓包、超过旧阈值的计量回归、核心可选预算测试与桌面设置测试已通过；独立数据验收重算全部397请求，抽取三份发布JSON产物重验均8/8。36个有效样本都有最终实现上实际测试成功的保存证据、公共测试明确通过；五个保守分类误判经原日志人工复核，依据保存在JSON。公共测试每题只有一个smoke，自建测试也可能被模型修改，成功执行不等于覆盖充分；旧轮观测不完整，不能直接用证据数量变化证明测试行为改善。

[预检](../../../docs/reviews/production-budget-review.md) · [最终独立复核](../../../docs/reviews/production-benchmark-review.md) · [测试执行证据](verification-evidence.json) · [逐请求usage](requests.json) · [非执行格式产物](solutions.json)

在被测源码的独立checkout、同版本依赖下运行新实验：

```powershell
$env:PYTHONIOENCODING='utf-8'
python -B bench/harness_benchmark_20260929/run.py --run --mode production --name new-production-run
```

仅重新分析本轮，不调用模型：

```powershell
python -B bench/harness_benchmark_20260929/analyze.py artifacts/benchmark-20260929/production-91e4911 --recovery-experiment artifacts/benchmark-20260929/production-91e4911-observer-recovery --publish --output bench/harness_benchmark_20260929/reports-production
python -B bench/harness_benchmark_20260929/regrade.py repair_routes-thinkflow-r2 --reports bench/harness_benchmark_20260929/reports-production
```

原始日志/工作区在本机artifacts保留；旧8K报告未覆盖。没有将个人配置、密钥或完整模型思考发布。旧0.8桌面二进制没有重打，本轮更新的是源码、装置和基准记录。
