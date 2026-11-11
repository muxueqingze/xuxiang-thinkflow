# 持久化、分发与消息投影定向验收

日期：2026-11-11。基线：`558078e`。范围：`session/events/runtime/cli` 的 F01/F02、`agent_loop/tool_registry` 的统一实际分发与信息批次、`context/feedback/message_projection` 的冻结消息及精确 read 投影。本报告不重复进程单元验收，也不是版本级全面复审。

## 问题

### [已修复，原 P2] 旧 skipped 回执掩盖同 ID 后续真实执行的未结算意图

- 位置：`src/session.py:100`，`_recover_intents` 使用所有已有 record ID 构造 `known`，包括 skipped。
- 真实触发：混合原生批次前项失败，后项被 skipped；模型下一轮以相同 ID 重试该项。这是当前允许的流程，因为 skipped 不进入 executed_ids。新尝试实际开始后宿主被强杀。
- 独立实测：临时宿主创建带 SessionStore 的 agent，同一 `retry-after-skip` ID 先记录 skipped，再实际执行 python 写 ready/PID 后 sleep。看到 ready 后强杀宿主，再从 SessionStore.load 恢复。输出为 `records=[['retry-after-skip','skipped']]`，`executed_ids=['retry-after-skip']`；本次实际执行的 unknown 消失。所属子进程已随 Job 退出。
- 影响：恢复后的事实账本仍把已经启动的尝试描述为未执行；相同 ID 继续调用也无法返回正确的 unknown 回执。
- 建议：已提交回执的判定排除 skipped，与 `_prior_receipt` 的现有规则保持一致；未结算的新尝试应形成 unknown。不要为此改变正常批次分发或添加额外模型调用。
- 修复复核：`known` 已排除 skipped。审查者重新运行同一个真实强杀探针，恢复得到 skipped + unknown 两条记录；同 ID/参数再次调用直接返回 unknown，ready 中 PID 不变，原子进程已经退出，未重新执行。

### [已修复，原 P2] read 回执复用被误当作进程输出，正文翻倍且范围说明丢失

- 位置：`src/agent_loop.py:1785` `_prior_receipt` 同时设置 content/stdout；`src/agent_loop.py:2083` `_format_tool_result` 新分支把任一非空 stdout 当作进程结果。
- 独立实测：创建实际文件，同一 native read ID/参数读取 offset=2、limit=80 两次。首次结果格式 1251 字；回执复用结果 2425 字，开头为 `exit_code: None`，正文重复两份。首次包含 read_note，第二次缺失。Context 仍仅一条记录，确认没有重新执行读取。
- 影响：恢复/重放读取结果时丢失范围与后续读取位置，并多发送一份正文；该布局也不再满足精确 read 投影对正文起点的契约。
- 建议：read/recall/list_files/glob/grep 保持内容型结果的既有展示契约，或在复用回执时恢复正确结果字段；保留范围说明，不展示不存在的 exit_code。
- 首次修补复核：read 已恢复 1251→1251、完整文本相等且范围说明保留。但修补用 parser `TOOLS` 集合排除内容型结果，未覆盖不在该集合中的 list_files/glob/grep。实际重复同 ID 的 list_files 首次 10 字，重放 45 字，仍带 `exit_code: None` 及重复正文；同根因还需收口。
- 最终根因修复：Context 用可选 `result_content` 区分进程确认正文与 stdout，回执复用恢复原来的内容通道。审查者独立复核 read 1251→1251、list_files 10→10，真实 custom-command 56→56，真实 image-command 168→168，展示逐字相等；read 范围说明保留，进程 stdout/stderr 和 output_path 确认正文各出现一次。JSON 快照恢复后四项仍与首次结果逐字相等；custom/image 的计数文件都只记一次执行，确认正文可通过独立引用回取，结束时 pending_tasks=0。新增 `test_receipt_replay_preserves_content_and_process_output_separately` 单独运行通过。

## 已独立实测通过

初次选择性执行 12 项现有测试，另做上面 2 个独立探针；修复后仅复核失败场景、内容/进程通道的直接影响，并单跑新增回归 1 项。没有全库重复回归，也没有远程模型请求。

| 验收点 | 审查者实际执行的证据 |
|---|---|
| CLI 成功、401、503、用户中断退出码 | `test_success_and_http_failure_exit_codes`、`test_keyboard_interrupt_exits_130`：真实 CLI + 本地 HTTP，分别退出 0/1/1/130；503 两次请求、401 一次 |
| 开放流中强杀，成功副作用回执保留且恢复 | `test_completed_receipt_survives_kill_during_open_stream`：真实文件内容、success、executed_ids 保留；resume 退出 0 |
| 开始后未结算即强杀 | `test_unresolved_intent_survives_process_kill`：普通无历史 skipped 的路径正确恢复 unknown、戳记与指纹；同命令 resume 不重复启动且退出 1 |
| SDK 会话切换、新会话、unknown 重放 | `test_binding_switch_new_and_unknown_replay`：新回执写入所选会话，原会话文件不变，observer 不重复，新会话清空正确 |
| 真实 Registry 分发及覆盖 | `test_registry_preserves_builtin_failure_and_override_in_each_channel`：内置失败保持 false，SDK/direct、tf、native 都执行替换 handler |
| 启动后新增工具 | `test_tools_registered_after_start_are_discoverable_and_callable`：目录更新、load_tools 开放、真实调用可达 |
| 信息批次与副作用链 | `test_native_read_failure_keeps_independent_result_but_stops_write_chain`、`test_text_read_failure_and_later_read_return_in_same_feedback`：失败 read 不遗漏另一成功 read；混合副作用批次仍停止，未写出文件；文本两请求完成 |
| 桌面实际切换 OpenAI↔Anthropic | `test_real_configure_both_directions_after_compaction_and_snapshot`：压缩/快照后通过 service.configure 双向切换；调用与结果 ID 配对、用户原文和错误保留，原 messages 不被改写 |
| native、need_result、自动信息反馈三路精确 read | `test_streamed_tf_reads_need_and_auto_result_survive_projection_and_compaction`、`test_native_read_reference_materializes_after_actual_compaction`：两协议正文保留一份，引用源被压缩后重新实体化；范围/版本说明和新版本正文保留，内部 span 元数据未发送给 provider |

源码核对还覆盖：事件持久观察者不替换宿主 renderer；tool_started 在执行前写日志，tool_completed 先保存结果快照再记录完成；跨协议投影只构造请求视图，原始 reasoning 字段留在历史；本轮 Registry 使用执行结果对象，展示发生在投影层。

## 当前结论

PASS：本轮两个 P2 均已修复，真实强杀后的未知回执恢复及内容/进程结果重放均通过独立复核。通过范围以表内具体场景与修复复核为准，不代表完整测试集或版本级全面验收。

## F10 显式读取边界补充复核

最后仅核对 `_dispatch_text_commands` 的只读边界补丁：先前默认批次 read 失败，后续独立 read 以 need_result 结束批次时，允许完成该读取，再反馈失败与成功结果。新增 `test_explicit_read_boundary_settles_prior_independent_read_failure` 独立单跑通过，实际两次请求完成，记录为 failed + success。

审查者另用真实 StreamingParser、CommandExecutionQueue 和文件操作检查两个直接停止条件：

- 缺失文件 read → need_result write：记录 failed + skipped，返回 tool_failed，没有创建目标文件。
- 对已有文件未经读取直接 write → need_result read：记录 conflict + skipped，返回 tool_failed，原文件保持 original；终态 pending_tasks=0。初次探针把明确冲突误期望为普通 failed，修正状态断言后通过，执行停止行为从首次就正确。

因此纯信息边界继续完成读取，混合副作用链仍停止。本次只新增运行该项测试及上述两个短探针，报告保持 PASS。

## 清理

两个独立探针均通过 stdin 提交，没有落地脚本，专属目录为 `temp/pro-runtime-quality-20261111`。临时子目录、空父目录、输出存储均已清理；强杀探针确认其 python 子进程已退出。现有测试自带的本地 HTTP 服务、CLI 进程及 TemporaryDirectory 由 teardown 关闭清理。未更改源码、登录、真实用户应用或系统配置。

最终通道拆分复核沿用同一专属临时目录，已清理全部临时文件与输出存储。首次 image 探针未显式按 UTF-8 解码约定输入，在中文路径下被 Python 本地默认编码错误解析；调整探针读取 `stdin.buffer` 后成功，属于探针输入解码错误，不计为产品缺陷。
