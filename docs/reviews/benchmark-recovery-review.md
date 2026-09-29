# 2026-09-29 基准恢复定向独立验收

范围：本轮 `agent_loop/context/task_plan/cli/skills/thinkflow` 补丁及 benchmark `monitor/run/thinkflow_worker`；不做全项目或版本终审。审查者读取实际实现，所有模型 fixture 使用 httpx.MockTransport 或纯内存替身；没有读取凭据、调用真实 API、运行 Claude/Pro 或停止进程。

## 发现的问题

### P2：最终监控写入失败会覆盖已完成的运行结果（已修复并定向复核）

- 位置：`bench/harness_benchmark_20260929/thinkflow_worker.py` 的 `finally` 中直接调用 `monitor.close()`（审查时第82行）；其内部 `monitor.py` 第98行强制 `flush(final=True)`，第91行 `os.replace` 未被隔离。
- 复现：替换模型为立即 completed 的离线 Agent；初始化 live.json 正常；仅第二次原子替换注入 PermissionError。结果是 worker.main 抛出异常，后面的 harness.json 写入根本没有执行。这使可选监控改变进程结果，并抹去真实 stopped_reason。
- 长期 fixture：`tests/test_benchmark_monitor.py::MonitorBoundaryTests.test_final_monitor_write_failure_cannot_erase_successful_worker_result`，修复前真实失败。
- 建议：隔离可选监控的初始化及收尾 I/O 故障，继续保存真实 harness 结果；不要把监控故障伪装成模型或工具执行故障。修复后只重跑该定向测试文件即可。
- 修复复核：已亲读 worker 的初始化、observe、heartbeat、close 四处 OSError 隔离，以及 Monitor 初始化失败关闭 trace 的实现。独立重跑该测试文件4项全过；原故障及新增初始化故障均保留 completed 和 harness.json，并记录非空 monitor_errors。该P2关闭；没有重跑全套。

## 已亲验的边界

- 新增恢复测试6项、回执测试11项、既有 native fallback 测试5项均通过。使用 unittest discover，未采用不适用于本项目 tests 目录的模块导入命令。
- 额外独立混合流 fixture：同一轮先成功 text write，再包含一个参数完整和一个参数截断的 native write，结束 length。两个 native 均不落盘、没有失败假回执；text 文件和成功回执保留。下一轮原 id 原内容重放 text write 后仍只有原成功回执，最终 completed。
- 额外独立 deny fixture：误通道 `tf-write id=` 触发 fallback 后，明确禁用的 write/bash 仍不在下一轮 tools schema，其他允许的 edit 被提供。此结论针对当前配置的 schema 过滤语义，不额外宣称本轮实现了新的执行层权限机制。
- 错误 `tf-write` 与 `tf-write id=` 只产生失败反馈，原函数名不会被翻译后自动执行；显式 native allowlist 没有被 fallback 扩展。
- 截断 native 不执行，续写额度耗尽为 max_auto_continues，历史 unknown 回执重放不会再次执行，也不会继续请求模型。
- 短 evidence_ref 指向特定回执：失败/unknown/cancelled/skipped 不借同 id 的成功记录通关；重复 alias、alias 与其他真实 id 冲突被拒；压缩后计数、混合旧快照迁移及再次保存稳定。既有完成步骤可保留已压缩的完整 id，但改变验收条件后必须重新验证。
- 新监控 fixture 检查了事件对象前后相等、private reasoning/text/content 哨兵不出现在文件、最终快照越过节流保存实际状态、同一轮 usage 不重复输出；meter_progress 只计量已结束且数值有效的 meter 请求，忽略伪 total_tokens，pending/非法数据不计入完成总量。

## 真实证据核对

只读既有运行产物，不在审查代理内发起运行。

| 样本 | 亲验 trace 中的故障/变化 | 结果与 meter |
|---|---|---|
| debug-baseline-pricing / pricing_refactor-thinkflow-r1 | 第3轮 length；第4轮测试模块发现命令失败；第7/9轮 tf-edit id=、第15/16/18/21轮 tf-write id= 未知工具；第10轮 bash heredoc 在 cmd 失败 | 产物8/8，max_run_turns；24请求，625,988 token；live 与 run_finished 一致 |
| debug-baseline-inbox / durable_inbox-thinkflow-r1 | 第3/5轮 length；第5轮仍执行截断 tf-write 导致参数解析失败；第7/8/9轮 tf-write 未知工具 | 产物8/8，max_consecutive_failures；9请求，153,695 token；live 与 run_finished 一致 |
| debug-fixed-pricing / pricing_refactor-thinkflow-r1 | 第3轮仍 length；第4轮4次 write 和 public_tests 成功；第5轮新增测试成功；第6轮额外具体边界命令成功；第7轮 stop 正常结束，所有 tool_completed 成功 | 产物8/8，completed；7请求，83,705 token；meter/result 一致，run_finished 无错误 |

第一份修复后 pricing 样本支持“本样本执行恢复并正常收尾”，不能据此把全部 token 降幅归因某个补丁，也不能推广为完整基准成绩。后续已启动样本由主会话继续观察。

## 交接

本审查只新增上述监控测试与本报告，未改生产代码。已核对监控测试注册到 tests/run_all.py。原P2已关闭，已通过边界无需重复全量检查；后续新样本由主会话继续核对。

## 单变量状态位置实验的补充核对

只读检查 `bench/harness_benchmark_20260929/status_ablation.py`，另用纯本地构造的4种消息形状验证；没有读取真实思考正文或发起模型请求。

- attached 模式只在实验 worker 进程 monkeypatch `_messages_with_runtime_status`，没有修改生产源文件；message 模式保留原逻辑。实际 worker 配置、meter、任务 materialize/grade 均复用原入口。请求中的工具 schemas、generation/thinking 参数与预算不受该补丁改变。
- 本地实际构造 AgentLoop 请求体进行前后对比：非 messages 字段完全相等；原 history 深拷贝后保持原值；构造的 reasoning_content、tool_calls、tool_call_id 和原始任务正文完整保留。仅末尾 status 消息移入最后一个 string user/tool content。
- **推广前限制**：末条若为 assistant，或 user/tool 的 content 为结构化列表，代码会回退到新增 user status 消息。因此“始终不新建 user 轮”不是这一实现的普遍性质；当前两项文本编码 benchmark 的常规续轮满足附加条件。
- **推广前消息语义风险**：status 附在 tool content 后面时，不再是独立运行时消息，而是工具结果字符串的一部分；如果某个工具输出要求机器可解析 JSON，附加文本会破坏其纯 JSON 形式。多工具结果中状态只归到最后一个工具结果，不能在未验证下泛化到其他 provider 的原生内容块协议。
- 实验保留原工具结果与任务内容，但改变消息角色边界与模型对状态的归属判断，这正是实验变量；不能仅凭本地请求体相等部分或小样本结果宣称生产推广安全，也不能把可能的token变化解释成删除了reasoning。

## 默认文件 schema 可见实验的补充核对

仅审查 `status_ablation.py::files_available` 及其 worker 分流，未改生产源码或发起 API 请求。

- 结论：对于当前 registry 和调用方，实验函数等价于从生产 `_enabled_native_tool_names` 删除默认隐藏 write/append/edit/mkdir/touch/copy 的两行。enable_native_tools=False 返回空集；非空 allowlist 优先并扣除 deny；无 allowlist 时返回 registry 全集扣除 deny。显式 allow/deny 没有被扩展或撤销。
- 一个内部返回值差别：无 allow/deny 时，删除两行的生产函数返回 None（表示全部），实验函数返回全部名称集合。实际 OpenAI/Anthropic schema构造输出相同；已检查的错误名称纠正调用方对当前内置工具也等价。
- 独立本地验证直接读取生产方法，仅在内存删除指定两行作为比较基准。组合 OpenAI/Anthropic × enable开关 × 3种allow × 4种deny × fallback开关，共96种配置，两种实现的完整 build_request_body 均相等；不只是比较名称集合。原 runtime role、系统prompt与其他请求配置保持一致。
- 另在 MockTransport 下执行真实文本 write→append，实际文件为 `first second`、回执顺序1→2、均为delayed、仅1次请求、正常completed。files_available只替换schema过滤函数，没有改parser、FIFO、executor、授权与读取基线机制。
- 不从单份或4份样本承诺性能收益。“grader 8/8”与“模型主动执行要求的测试”是不同证据，正常收尾但没有运行要求测试的样本应继续明确记录，不能只用更少请求数判为完整改善。

## 最终实测证据收口

核对范围限于 `bench/harness_benchmark_20260929/reports/recovery-results.json` 中协议修复 pricing/inbox 各2次与 files_available pricing/inbox 各2次。已与8个原始运行目录的 result.json、meter.json、trace.jsonl、live.json逐项交叉核对；请求数、token 从原 meter.requests 重算，流式重叠从 trace 的同轮完成时间早于 stream_finished 重算。未重新调用模型或重跑测试。

| files_available 样本 | 请求 | meter总token | 停止 / 外部grader | 流式重叠 | 模型执行测试的事实 |
|---|---:|---:|---|---:|---|
| pricing r1 | 4 | 43,136 | completed / 8/8 | 4 | 没有任何 bash 调用。第4轮写入3个生产文件与 tests/test_pricing.py，但未观察到运行 public 或新增测试；不能宣称模型完成了要求的测试流程 |
| pricing r2 | 19 | 564,981 | completed / 8/8 | 5 | 第5轮 public+新增测试命令失败，第7轮两者成功；第12轮 edge_check 成功；第16轮 random_check 失败，第17轮修改后成功 |
| inbox r1 | 12 | 251,615 | completed / 8/8 | 0 | 第6轮测试组合失败；第8、11轮 public+新增测试组合成功 |
| inbox r2 | 9 | 154,324 | completed / 8/8 | 5 | 第4轮public成功；第5轮extra失败，第7轮修复后成功；第8轮public+extra组合成功 |

四份 files_available 均为退出码0、未超时、无基础设施错误；meter usage_complete 均为true，各请求已结束且HTTP 200、无transport_error。run_finished、live.json与result的停止原因一致，live.run_active为false。

| 同题同重复数的阶段 | completed且外部验收通过 | 请求总数 | meter总token | 流结束前成功的delayed文件操作 |
|---|---:|---:|---:|---:|
| 协议修复，pricing/inbox各2次 | 2/4 | 75 | 1,844,477 | 15 |
| 默认提供文件schema，pricing/inbox各2次 | 4/4 | 44 | 1,014,056 | 14 |

协议修复四份各自为 pricing r1 7请求/83,705/completed、pricing r2 24请求/625,332/max_run_turns、inbox r1 24请求/503,538/max_run_turns、inbox r2 20请求/631,902/completed；四份外部grader均8/8。因此2/4与4/4表示“正常停止且外部验收通过”的观测，不是“逐项满足了模型执行测试要求”的比例。

14次流式重叠均来自text通道的成功delayed操作，包含write和edit，不等于14次write。inbox r1没有重叠；开放原生文件schema没有保证每次运行都会采用流式写入。pricing r2仍出现打印cwd、print(1)、仅import等低价值调用，不能声称循环或过度验证问题已全部消失。

主会话已将默认隐藏六个文件schema的两行删除合入当前生产工作树；该变化与前述96配置等价性核对一致。以上4份对照支持本轮采用此小改动，但样本仅覆盖两项任务、每项两次，生成仍有波动，且pricing r1缺少实际执行测试的证据；不承诺稳定性能提升，不替换原36次基准成绩，不冒称完成全版本验收。
