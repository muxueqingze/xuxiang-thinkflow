# 生产基准最终定向数据验收 · 2026-09-29

## 问题与验收判断

**数据验收通过。** 原始36次尝试中有一次观察器装置事故；指定补测一次后，得到36项有效评测。没有将事故8/8产物评分冒充正常交付，没有漏记原尝试消耗。这里不是“首次36次全部成功”，也不是版本全面终审。

**已关闭的P2：断流统计范围文案曾不明确。** 原summary的ThinkFlow=0指有效样本，但未在该句写明；全部实际请求还包含事故的一次断流、完整输出338 token。主会话修复后已定向回读确认：开头明确36有效样本/37实际尝试，Token表和零断流句标明“有效样本”，事故段另列1次/338；results新增 `all_attempts_abandoned_requests=1` 与 `all_attempts_abandoned_output_tokens=338`，数值匹配原meter。338已包含在47,031中，未重复计费，也未全部归为断流后的额外输出。当前没有未关闭的数据验收问题。

本次只审指定原实验、唯一补测及其发布报告，未请求模型、未读取模型思考正文、未重新运行harness；仅执行三份发布代码的离线隐藏评分。

## 冻结与补测唯一性

审查开始时、主会话永久修复观察器之前，独立比较：原 `production-91e4911` 与补测 `production-91e4911-observer-recovery` 的manifest **完全相同**，source_commit均为 `91e491167564f625334b812d6f0a422d2315b23e`，当前38个冻结文件逐一SHA256匹配，零差异。该确认已经先发给主会话。

原计划恰为六题×三harness×两重复，36个唯一身份；磁盘有原36个result和补测1个result。补测plan仅包含原第19项 `repair_routes-thinkflow-r2`，发布结果的唯一替代映射正确，原失败以 `__observer_interrupted` 单独保存在requests，并完整进入observer_interrupted_attempts。

补测prompt SHA256与原项一致；全部37份prompt按实际请求的UTF-8文本哈希重算匹配，同题各harness prompt相同。磁盘prompt.txt使用Windows CRLF，需经通用换行读取后计算逻辑prompt哈希；不能把磁盘原始字节哈希与发送的LF字符串哈希直接比较。

后置永久补丁已另行读diff：monitor改ensure_ascii=True，runner只包住可选print_progress异常并保存observer_errors，meter.finish异常仍向上传播。它们在全部生成结束和上述指纹确认之后修改，**不是91e4911被测版本的实现**。本审查不重复主会话的后置回归测试，也不将其通过声明算作本审查者亲跑。

## 独立重算结果

| Harness | 有效正常终态且8/8 | 有效请求 | 有效总token | 平均秒 | 计入事故的实际请求 / token |
|---|---:|---:|---:|---:|---:|
| ThinkFlow | 12/12 | 155 | 3,845,627 | 105.384 | 159 / 3,892,658 |
| Pi | 12/12 | 123 | 1,397,532 | 54.620 | 123 / 1,397,532 |
| OpenCode | 12/12 | 115 | 1,732,556 | 59.900 | 115 / 1,732,556 |
| 合计 | 36/36 | 393 | 6,975,715 | — | **397 / 7,022,746** |

每个raw result的8项明细均通过、exit0、无timeout/基础设施错误，36份公共测试与任务seed逐一字节一致。终态独立读取：ThinkFlow的harness与trace.run_finished为completed；Pi最后assistant message_end为stop且无error；OpenCode最后step_finish为stop。未用进程exit0替代模型终态。

全部397条原始请求逐条检查并独立重算输入、输出、缓存、非缓存、推理与总量，与raw meter/result和发布requests一致；发布results各原始字段、分harness汇总和均值/中位数一致。输入与输出都完整，推理是输出子集，不能重复计入总token。

- 有效ThinkFlow：输入3,554,722，输出290,905，缓存3,119,360，非缓存435,362，推理172,318。
- Pi：输入1,257,379，输出140,153，缓存1,202,560，非缓存54,819，推理84,267。
- OpenCode：输入1,623,474，输出109,082，缓存1,512,064，非缓存111,410，推理63,743。
- 原事故另外输入32,201、输出14,830、4请求，合计47,031；四次usage均完整，第四次下游断开仍收到了完整上游usage。补测本身为16请求、304,630 token、80.016秒，不能把原事故47,031误写为补测消耗。

## 请求条件、异常与流式重叠

**397/397**请求均记录HTTP200、finished、完整usage、模型deepseek-flash，无transport_error；客户端与转发体的三种输出cap字典均为空，前后messages SHA256均存在且相同。未发现length/max_tokens结束。有效36项没有下游断流，全部37尝试只有上述事故一次。实际请求没有发生静默预算补回或代理改写历史。

12个有效ThinkFlow的execution_config均明确四类预算为null、compaction.enabled=false。消息哈希证明代理未修改客户端提交的历史；并不单独证明每个harness内部历史策略，后者依据已完成的生产预检及冻结配置，不能夸大哈希证据。

有效样本仍有工具失败/跳过和3次明确的流式解析错误事件（inbox-r1、补测routes-r2、receipt-r1），但最终均恢复completed。正常终态不等于过程无错误。frame/inbox的恢复消耗和receipt-r2低价值探测另见 `production-cost-cases.md`，不在这里重复归因。

从ThinkFlow trace重新逐事件计算：**25次成功delayed工具操作，在同轮stream_finished之前完成，分布于7/12个有效样本**。分布为pricing-r2=3、routes-r1=2、补测routes-r2=2、receipt-r1=3、interval-r1=6、receipt-r2=4、frame-r2=5，其余0；与harness、result及汇总全部相等。这里统计的是工具操作，不应写成25次API请求或保证每轮都流式执行；原中断尝试不混入有效样本的25次。

## 发布产物与过程证据

发布solutions的36个身份完整，所发布Python文件文本与所选raw workspace逐一一致；事故身份对应的是唯一补测目录，未误取原中断产物。独立从 **solutions.json** 恢复以下三份到专用临时目录，使用任务seed及同一隐藏grader评分，全部8/8：

- `repair_routes-thinkflow-r2`：补测路由实现。
- `durable_inbox-pi-r1`：持久队列实现。
- `receipt_package-opencode-r1`：包及CLI实现。

这是三份抽样的实际离线重评分；其他33份核对保存的逐项评分和发布源码一致性，没有冒称重新执行全部36份。

`verification-evidence.json` 当前列36份confirmed_success，并对五份特殊shell写入/验证脚本另有manual_audit。本审查按其中具体日志位置核对了五份补充证据的实际输出和成功回执：Pi receipt-r2第8405/9478行、frame-r1第12366行、routes-r1第9486/9878行；ThinkFlow补测routes-r2 events[51/53/88]、receipt-r2 events[30/47/112/122]。测试数量及CLI边界检查记录均存在。完整逐项测试过程审读由该证据表的专项审查负责；本审查不以评分通过替代过程证据，也不保证模型自编测试覆盖质量。

## 收尾与范围

本报告没有改被测实现或正式产物，没有新生成调用。临时还原产物和独立核算中间数据仅放在 `temp/production-final-review`，报告完成后送回收站。正式raw实验、事故记录、唯一补测、发布JSON和审查报告均保留。

该六题两重复只支持固定生产配置下的本地比较，不支持行业排名或流式机制的单变量因果结论。当前数据可以交付；报告中必须保留装置事故、实际成本和测试证据范围。
