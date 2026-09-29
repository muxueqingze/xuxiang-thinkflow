# durable_inbox 首样本定向诊断

2026-09-29。范围仅 ThinkFlow `formal/durable_inbox-thinkflow-r1` 的 `harness.json`、`meter.json`、公开生成代码，以及当前 harness 实现。未读取 stdout 中的内部思考正文，未调用模型，未修改生产代码、任务或运行参数。诊断写于正式运行期间；整体结论现见[完整报告](reports/summary.md)。下列本地harness证据已提取到公开的[事件与工具回执元数据](reports/thinkflow-events.json)，用量见[逐请求记录](reports/requests.json)，没有模型思考正文。

## 已核实事实

- 17 次 API 均 HTTP 200、均报告 usage；没有 abandoned、failed API 或未知 usage。共有 23 条工具回执，最终 `stopped_reason=completed`、`last_error` 为空。主会话给出的评分是 8/8、125.562 秒；本诊断未独立重跑评分。
- 总 token 472,836，其中输入 444,891（94.09%）、输出 27,945，输出内 reasoning 18,093。输入中缓存命中 399,360（89.77%）、未缓存 45,531。**总 token 是全轮重复发送总量，不能直接称为同额现金成本；reasoning 已包含在输出内，不能再加一次。** 证据：`artifacts/benchmark-20260929/formal/durable_inbox-thinkflow-r1/meter.json:501`。
- 单次请求输入由 2,327 增长到 40,180 token，请求字节由 8,912 增长到 163,966，message_count 由 3 增长到 41；所有请求 system_sha256 相同。本样本不支持“每轮改变 system 使缓存失效”的解释。
- 两次 delayed 写入在第 7 轮结束前完成，流式执行机制确实工作；这不能单独证明整项任务更省时间或 token。生成的 `inbox.py` 为 11,882 字符，额外测试为 16,570 字符，生成和验证内容本身也有必要成本。

## 改进优先级（四项，均留待正式运行完成后）

1. **优先诊断纯思考达到上限后的自动续写与历史放大。** 第 3 次 API 的 completion=reasoning=8,192、finish_reason=length，耗时 36.078 秒且该轮没有工具事件；第 4 次输出 5,908，其中 reasoning 5,861，耗时 24.032 秒。这两次 API 合计 60.110 秒，约占主会话所报总耗时的 47.87%。第 4 次输入从第 3 次的 5,735 上升至 14,005，第 5 次又到 20,020。实现会保存 LENGTH 轮的 reasoning_content，追加续写消息，再把完整历史送回；原生工具轮也保留 reasoning。证据：`meter.json:66`、`:95`；`src/agent_loop.py:1058`、`:1109`、`:1700`。这是模型实际选择长思考，叠加 harness 自动续写/保留历史的可证实路径；无法从单样本判定是哪条提示造成长思考。后续应单独观测“纯 reasoning、无工具、length”并做限定消融，判断是否应采用不同的续写/无进展策略。不要仅抬高上限，也不要盲删 DeepSeek 工具续轮需要的 reasoning_content。

2. **优先减少计划完成证据的重复纠错。** 第 14、15 轮 update_plan 均因“完成项引用不存在或未成功的工具回执”失败，第 16 轮才成功，第 17 轮结束。第 14、15 次 API 共计 76,762 token、8.219 秒；这是两次无效请求的观测量，不等于修改后可精确节省同量。证据：`harness.json:1092`、`:1099`；`meter.json:385`、`:414`；`src/task_plan.py:50`、`:56`。模型提供错误引用是直接触发点；harness 要求精确原生长 ID，失败时又列出多达 20 条成功回执，增加纠错和重复历史的负担。后续考虑统一短别名/结构化 evidence 候选，并只反馈当前无效引用及可用对应项，仍须保留“完成必须有成功证据”的约束。

3. **优先消除 cmd.exe 与 bash 工具名之间的易错边界。** 第 10 轮 bash exit_code=1，回执错误包含 `<<` 语法失败；第 11 轮进入修复/验证。证据：`harness.json:1050`、第 10/11 轮工具事件；`src/agent_loop.py:594`、`:600`；`src/skills/thinkflow/SKILL.md:93`。运行时和 skill 已明确 Windows 使用 cmd.exe，所以不能说完全没告知；实际模型仍选了不兼容语法。后续应考虑把具体 shell 放进工具描述/参数契约，或给文件脚本验证提供少引号路径。保留 benchmark 原协议；Pi 使用 Git Bash、ThinkFlow 使用 cmd.exe 的差异应在报告中注明。第 7 轮另一次测试 exit_code=1 属于生成代码/测试反馈，不能把所有失败都算成 shell 或 harness 无效工作。

4. **次优先压缩固定入门指南与小任务收尾开销。** 系统指令要求首次 read_skill("thinkflow")，第 1 轮确实执行成功，即便个人 skills 已关闭；内置指南为 4,337 字符。第 2 次输入从 2,327 增到 4,728 token，但该轮还包含前轮 bash 结果，不能把增量全部归于 skill。证据：`src/cli.py:54`；`src/skills.py:79`；`src/skills/thinkflow/SKILL.md`；第 1 轮事件和 `meter.json` 第 1/2 次请求。模型把 read_skill 与 bash 放在同一轮，因此不能宣称它独占了额外一轮。后续可评估短协议核心与按需工作方法，减少所有后续请求重复携带完整教程。本轮还出现额外测试和计划收尾；这是 correctness/可审计性取舍，不能直接删除后宣称等质量加速。

## 归因边界与计量注意

完整历史重发是明确的累计输入机制（`src/agent_loop.py:607`）；默认压缩门槛是 80 条消息/200,000 字符（`src/compaction.py:20`）。本样本最大请求 41 条/163,966 字节，不能据此指控“压缩实现没工作”，也不能认为全量 input 必须付未缓存价。

补查 `frame_decoder-thinkflow-r1` 第 8/9/10 次请求：system_sha256 均相同，第 9/10 次输入为 34,987/36,908，缓存命中为 32,768/1,408。**代码不支持“stamp/ledger动态字段写进system前缀”这个具体机制**：system 返回固定配置（`src/agent_loop.py:589`），stamp、执行环境和当前计划作为当前请求末尾 user 消息追加（`:592`、`:607`），ledger 作为新增历史 user 消息追加（`:818`）。尾部运行消息下一轮被新 assistant/工具历史替换，可能影响当时尾部附近的最长共有前缀，但这不能解释或证明第 10 次为何早至 1,408 token 就失去命中。确定性压缩确实能改写更早的历史前缀，不过此处 29 条消息/142,391 请求字节不支持触发默认门槛。单样本缓存骤降也可能涉及上游缓存状态等未观测条件；不据此给缓存下降作因果归属，不调整正在运行的 benchmark。

文本文件回执可携带最多 4,000 字符正文，need_result/auto_result 又可带详细输出（`src/context.py:58`、`:241`、`:263`）；这是值得后续按字段长度审计的机制。原生回执会 mark_injected（`src/agent_loop.py:1750`），**当前允许读取的元数据不足以证明本样本把同一原生输出重复注入 ledger**，因此没有列为已证实原因。生成代码的重复读取同样只知道发生了两次 read，不掌握其参数，不能断言没有必要。

主会话提供 Pi 同题 14 API/169,494 token/58.453 秒、OpenCode 8 API/115,419 token/58.125 秒；统一 deepseek-flash 使差异不能解释成换了模型。但不同 system、工具契约、shell、历史策略及模型随机行为均可能影响路径。本样本只证明 ThinkFlow 此次路径更长，不足以给某一设计分配全部差额，也不构成其它题目/重复轮次的整体优劣结论。

## Pi 适配隔离复核

`bench/harness_benchmark_20260929/adapters.py:25` 的白名单环境不继承 provider key；Pi 专用 `PI_CODING_AGENT_DIR=run/profile/pi`，模型凭据为 loopback token。启动参数包含 no-approve、no-context-files、no-extensions、no-skills、no-prompt-templates、no-themes、no-session、offline；version check/telemetry 关闭、cacheWarming=off。没有发现读取个人 auth 或上下文配置的缺项；未打开任何私人 auth 文件。配置隔离不等于操作系统文件沙箱，工具的默认文件访问能力仍属于各 harness 的比较边界。
