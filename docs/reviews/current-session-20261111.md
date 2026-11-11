# 当前版本会话恢复定向检查 · 2026-11-11

基线：0.9.0 / `a677f43`。范围是执行意图日志、增量快照、检查点与分支输出；没有模型请求、发布或全量回归。

## [P1，已修复] 执行日志末行截断会阻止已提交回执恢复

- 位置：`src/session.py:65` 的 `_journal`、`:91` 的 `_recover_intents`。
- 原行为：`.changes.jsonl` 忽略没有换行的末行，但 `.execution.jsonl` 按文本逐行解析所有内容。进程在执行日志追加期间退出，半行 JSON 会使加载失败；截断中文字符还会在进入 JSON 解析前触发 `UnicodeDecodeError`。原续写代码只添加换行，导致损坏尾行永久留在日志中。
- 实物复现：本地 SSE 驱动真实 CLI 写文件，等到完成事件落盘后强杀 CLI，再截断完成日志末行；此前保存的成功回执无法通过 `SessionStore.load()` 读取。修复前新增测试实际报错于 `_recover_intents` 的文本迭代。正式回归使用工具实际产生的中文路径日志，在 UTF-8 首字节后截断。
- 契约：同步 `tool_started` 观察者返回后才执行工具；`tool_completed` 先提交快照回执，再写完成日志。因此未换行的开始日志没有获得执行资格，未换行的完成日志对应的回执已先保存。
- 修复：二进制读取，只处理已经换行的完整日志行；继续追加前截去未换行尾行。完整但损坏的日志行仍按原有错误处理，不吞掉其他错误。
- 验证：真实 CLI 从截断的完成日志恢复 `success`，继续第二个工具并再次加载，两条回执均为 `success`、第一条写入没有重放。真实 SDK 开始日志写到中文半字后抛出写入错误，文件没有产生；恢复后同一命令正常执行一次。

## 同根桌面补修与回执裁剪复核

独立质检发现桌面仍有同根遗漏：`src/desktop_service.py:130` 的 `_journal` 与 `:410` 的 `_unresolved_intents` 使用独立 `.jsonl`，最初的 CLI 修复没有覆盖这条路径。真实 Agent `touch` 中文路径成功后截断完成日志，再初始化桌面并打开原工作区，旧会话会被跳过；直接恢复旧 ID 报 `UnicodeDecodeError`。新增正式测试在修改前实际观察到恢复后的 session ID 与原 ID 不同，text 成功和 native 失败两个分支均失败。

桌面补修遵循自身契约：

- 只读取有换行的已提交行，续写前清除未提交尾行。完整损坏行保留桌面原有显式 `recovery` 错误，不静默忽略。
- `resume` 使用已经读取的持久快照；其他恢复检查只有存在未结算日志时才读取对应快照。正常已完成工具日志不增加快照读取。
- 对 text/native 工具，仅首个非 skipped 回执为 success/failed/conflict，且命令指纹与开始事件相同，才根据真实回执补写缺失的 completed。写入目标是正在恢复的会话，不是原来打开的会话。
- cancelled、unknown 与无 Agent 回执的 UI revert 保持待核对；`recovery_acknowledged` 仍清除已确认项。

再次核对发现不能只在内存跳过旧 started：`ContextManager.compact_history` 会裁剪旧回执，命令指纹也有 4096 条上限；保留缺少 completion 的旧日志会在未来重新变成 unknown。因此桌面与 `SessionStore._recover_intents` 都在确认持久回执后一次性补齐完成日志。补写仍走原 fsync 追加接口；如果再次在补写期间中断，下次恢复继续从原快照核对，不执行工具。

最终直接影响验证：

```text
python tests/test_desktop_service.py \
  DesktopServiceTests.test_real_torn_completion_reopens_same_session_and_continues \
  DesktopServiceTests.test_torn_uncertain_receipts_and_ui_intents_still_require_recovery \
  DesktopServiceTests.test_journal_unfinished_and_corrupt_line_need_explicit_acknowledgement \
  DesktopServiceTests.test_cancelled_tool_receipt_requires_recovery_confirmation -v
4 passed

python tests/test_cli_persistence.py \
  SDKPersistenceTests.test_recovered_receipt_settles_intent_before_compaction \
  CLIProcessTests.test_torn_completion_journal_restores_receipt_and_accepts_new_tools \
  CLIProcessTests.test_unresolved_intent_survives_process_kill -v
3 passed
```

桌面用真实 Agent 执行文本 touch 成功、原生 read 失败，逐次截断中文完成日志、重建服务，确认恢复原会话；随后经本地 SSE 完整运行第二个工具直到 `_run` 收尾，再恢复仍没有未决项。成功回执没有重放。两端均实际调用 `mark_injected`、`compact_history(keep_records=0)` 与 4096 次 `_remember_command` 淘汰原回执/指纹，再保存和恢复，旧 started 不会重新出现。unknown/cancelled 测试只替换执行结果，事件分发、账本、快照、恢复与确认均走真实代码。

桌面补修测试使用系统临时目录 `temp/thinkflow-current-session-20261111`，避开项目上级 Git 仓库；本地 HTTP 服务和 Agent 在测试退出时关闭。末轮检查该专属目录为空后送回收站。新增/修改的源码与测试 `git diff --check` 通过。

## 其他检查结果

- 检查点：旧 delta 在新 base 替换后残留时按 checkpoint ID 隔离；中断的 delta 尾行不参与恢复，继续追加后新状态可读。
- 跨路径：同一 `SessionStore` 切换已有目标路径会重新读取目标检查点；来源快照保持原样。
- 跨分支：真实 Agent 的完整工具输出可由历史快照恢复；分支持有独立输出文件，来源输出目录改名后分支仍能 recall 原文。
- 未决执行：完整开始事件但没有完成回执的强杀场景仍恢复为 `unknown`，恢复 CLI 不自动重放。
- 类型：检查了 `AgentLoop.to_snapshot`、原生消息存储与用量采集。Python 相等比较对 `True` / `1`、`1` / `1.0` 的区分确实较弱，但没有找到当前 Agent 正常执行中导致业务状态错误的同字段类型替换路径。原生历史输入随消息追加，固定布尔状态和计数器各自保持类型。本轮不为任意 JSON 类型变换修改 `session_delta.py`。

## 定向验证

所有命令将进程级 `TEMP` / `TMP` 指向项目 `temp/current-session-20261111`，测试以自身临时目录隔离数据。

```text
python tests/test_cli_persistence.py \
  CLIProcessTests.test_torn_completion_journal_restores_receipt_and_accepts_new_tools \
  CLIProcessTests.test_unresolved_intent_survives_process_kill \
  SDKPersistenceTests.test_binding_switch_new_and_unknown_replay -v
3 passed

python tests/test_cli_persistence.py \
  SDKPersistenceTests.test_torn_start_prevents_execution_and_resume_can_commit_it -v
1 passed

python tests/test_session_incremental.py -v
5 passed

python tests/test_output_recall.py \
  OutputRecallTests.test_snapshot_history_and_fork_have_independent_output_files -v
1 passed
```

共 10 个独立测试通过。`git diff --check` 通过。新增正式回归保留在 `tests/test_cli_persistence.py`；测试进程、HTTP 服务及临时产物由测试收尾关闭、清理，专属临时目录检查为空后回收。

结论：本次定向检查通过；修复只改变日志尾行的提交/恢复语义，不增加模型调用或正常工具执行步骤。
