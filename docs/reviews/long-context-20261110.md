# 长程上下文节点独立验收 · 2026-11-10

## 问题与修复

- **P1，已修复：原生 recall 成功却不给模型原文。** `src/agent_loop.py:1894` 将 recall 送入 `_format_tool_result`，其内容型工具分支原先缺少 recall。审查者直接调用 `_handle_traditional_tools`，OpenAI 的 tool 消息与 Anthropic 的 tool_result 都只有 `recall ok`；账本保存了原文，模型消息却没有。最小修复是在 `src/agent_loop.py:2022` 的内容型分支加入 recall，主会话已实施并补实际 provider 消息断言。修复后审查者再次走两个 provider 的真实消息组装路径：同批回取长要求的中部与尾部，两个精确字符切片均进入反馈，回执各注入一次。

## 独立行为证据

- **桌面完整状态路径：** 使用真实 DesktopService 与 AgentLoop，仅将 HTTP 替换为 MockTransport；依次执行带附件的 submit_input、压缩、fork_session、分支新输入、configure 更换 profile/model、恢复原会话、恢复分支、new_session。原文与附件展开分离，原会话 request 引用和状态未被分支修改，分支新增 request:2，配置切换保留引用，新会话全部清空。仅两次 mock 模型请求。
- **反复压缩：** 100 次压缩中，当前执行输入始终完整且恰好一次，位置映射保持正确，结构化条目不嵌套摘要。最终 6 条消息、1 条预算内摘要条目、摘要 1,388 字符，累计压缩 996 条消息。
- **混合历史与索引：** 固定随机种子 11，300 次真实用户修订，混合附件展开、with_observation 追加账本、原生 tool_calls/tool 结果，每步按低阈值压缩并核对每个保留位置与原始输入；每轮 JSON 往返恢复。无索引漂移、当前输入缺失或重复，最终摘要 1,781 字符（预算 1,800）。
- **旧快照及去重：** 缺少 conversation 字段的旧快照正常载入；后续新输入从 request:1 开始记录。同 ID 改 offset 被明确判冲突，快照恢复后仍拒绝该冲突。
- **接口装配：** 检查默认未压缩会话的动态 native schema 与系统协议入口，recall 按压缩状态开放；原生和流式共用 conversation.recall。普通压缩流程无模型摘要调用。

## 结论

**PASS，限定本次长程上下文节点。** 上述 P1 已按原失败路径复核；其余范围未发现需修复的确定缺陷。本报告不覆盖版本级全库审查或真实模型质量评估。完整工具输出持久留存属于后续节点，本次不将现有工具输出裁剪算作已修复。

审查命令使用 Python `-B`，未发网络模型请求。唯一落盘实验目录 `temp/qa-long-context-20261110` 已移入回收站，随后 Test-Path 为 False；客户端均关闭，无本轮后台服务。保留本报告作为证据，不保留临时脚本。

## 真实模型产物与比较口径复核

随后独立检查 `bench/long_context_20261110/run.py` 与 `docs/evidence/long-context-20261110.json`。直接执行 JSON 保留的四份完整源码，再在内存模块上运行脚本当前 GRADER；九项检查与各份 `artifact_recheck` 逐项一致。原始 `checks` 仍是当时八项耦合判题记录，本次九项成绩以离线 `artifact_recheck` 为准。

| 种子场景 | 旧压缩方案 | 当前方案 | HTTP 请求数 | 已报告 total_tokens |
|---|---:|---:|---:|---:|
| current_request | 4/9 | 9/9 | 6 → 5 | 18,290 → ≥22,752 |
| archived_request | 5/9 | 9/9 | 5 → 7 | 15,930 → 39,496 |

逐轮 token 求和与保存的 totals 一致，HTTP 计数、usage.api_calls 与轮数一致。当前方案 current_request 的第一轮 `usage_reported=false`，故 22,752 是下限；不能据此计算完整 token 节省比例。archived_request 的工具记录含成功 `recall(request:1, offset=0, limit=8000)`，当前要求场景则无 recall。

限定结论成立：**在本次种子历史、两次压缩、磁盘快照恢复之后，当前方案的两份最终实现均通过九项离线检查，旧压缩方案两份实现分别漏掉五项、四项。** 四份源码的具体行为与检查结果一致。这里不作完整长任务成功率、稳定性或相对 Pi 效率结论。

比较口径的两处边界：

- 脚本只将基线的 `compaction.py` 函数换回 `ee48f51`，其余使用当前 harness。因此表内是旧/新压缩方案对照，不是两个完整版本的对照。
- REVISION 要求省略最终 units 为零的组；这已被原始“数量非负、零数量不建组”的规则蕴含。archived_request 能支持原始要求归档后回取的结论，不能单靠本场景证明非冗余修订或冲突要求优先级处理。

**复核结论：PASS，限上述功能质量证据和比较口径。** 本次仅内存离线执行，无 API 请求、临时文件或后台进程。
