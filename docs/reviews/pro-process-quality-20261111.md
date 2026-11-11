# F05/F07/F09 进程与输出定向验收

日期：2026-11-11。基线：`558078e`；核对当前工作树。范围：`process_owner.py`、`executor.py`、`interfaces.py`、`desktop_terminal.py` 的本轮改动及对应运行契约。未把主会话正在集成的 Registry/AgentLoop 适配算成已交付缺陷。

## 问题

### [已修复，原 P2] 第二次取消打断进程结算，丢失已经捕获的输出引用

- 位置：`src/executor.py` `_run_process` 的 `except asyncio.CancelledError` 分支（验收时 671–678 行）。`_stop_process_tree` 和 `_settle_output_capture` 均直接 await；前者的 Windows Job 等待存在实际异步让出点。
- 独立复现：Windows Python 3.12，真实 image command 启动 Python 子进程，写出 `x * 90000 + IMAGE_TAIL`、flush、写 ready 文件并 sleep。看到 ready 后执行 `task.cancel(); loop.call_soon(task.cancel)`，不 mock 进程或清理函数。
- 实测结果：`{"exception":"CancelledError","has_result":false,"output_refs":null}`；50 ms 后 `pending_tasks=0`。返回的是普通取消异常，完整输出引用无法交给上层回执。没有观察到运行任务残留，故不把本问题描述成进程清理失败。
- 实际触发：停止与关闭路径先后取消同一运行。文件线程分支已处理同一场景，进程分支仍会丢失结算事实。
- 建议：把拥有进程的终止和输出结算作为一项必须完成的清理任务；重复取消只中断等待者，清理结束后仍交回 `ExecutionCancelled(result)`。无需引入重试、全局任务管理或新的 API。
- 修复与独立复核：取消分支已使用独立 cleanup task 完成 stop/settle，shield 等待期间消费重复取消。审查者重放原始无 mock 探针，返回 `ExecutionCancelled`、`status=cancelled`、`output_size=90010`，归档与完整原文逐字一致，实际 PID 已退出，返回瞬间 `pending_tasks=0`。另单跑新增 `test_repeated_image_cancel_settles_output_and_returns_receipt` 通过。没有重跑已通过的全量项目。

## 明确通过范围

以下均由审查者实际执行，未只采信 `docs/evidence/pro-review-20261111/process.json`：

| 场景 | 运行与结果 |
|---|---|
| Windows 父先退出、子继承管道，主动取消 | `test_exited_parent_cancel_stops_inherited_pipe_child` 通过；子进程退出，延迟写未发生 |
| Windows 父正常返回，子关闭/重定向输出 | `test_successful_parent_closes_owned_child_with_redirected_pipes` 通过；成功返回同时终止本次拥有的子进程 |
| POSIX 父先退出、子继承管道，超时 | WSL `AutoResearch-Ubuntu2404`、`/opt/autoresearch/harbor/bin/python`，`test_exited_parent_timeout_stops_inherited_pipe_child` 通过 |
| bash/python/custom/image 四入口，退出码 0/7 | `test_all_command_entries_share_complete_streams_and_exit_status` 通过；stdout/stderr 原文各 90013 字符，尾部预览和完整归档一致 |
| 文件线程提交前、回执完成后取消，并重复取消 | `test_file_cancel_preserves_committed_result_before_and_after_receipt` 通过；实际 append 一次，bytes_written=4，结果及 ChangeStore 都为 success |
| 回执提交失败期间取消 | `test_cancel_at_failed_receipt_commit_keeps_unknown_fact` 通过；实际文件已写，结果与 ChangeStore 都保留 unknown |

源码核对：Windows 在 CREATE_SUSPENDED 创建后先加入 Job，再恢复初始线程；Job 不依赖父 PID 持续存活。POSIX 使用独立 session 及 killpg，即使父已退出仍清理原进程组。DesktopTerminal 本轮仅复用抽出的 WindowsJob，相关调用签名保持一致。

## 当前结论

PASS：本轮发现的 P2 已修复，原失败探针及直接影响通过独立复核。F05 父进程先退出、F09 文件最终事实，以及 F07 四入口成功/失败完整输出已通过本次定向实测。结论仅覆盖这里列出的进程与输出单元，Registry/AgentLoop 集成另行验收，不扩张为版本级全面审查。

## 运行与清理

选择性执行现有契约测试共 6 项（Windows 5、WSL 1），未运行全库。第一次以 `python -m unittest tests.test_process_contracts...` 调用时 tests 不是可导入包，随后使用测试文件入口成功运行，不计为产品失败。独立复现通过 stdin 运行，没有落地临时脚本；该次 `TemporaryDirectory(prefix='thinkflow-quality-')` 随上下文退出清理，Executor 输出存储已 close，未遗留 pending task。现有测试的 TemporaryDirectory 由 teardown 清理。没有操作真实用户应用或配置。

修复复核只运行原独立探针和新增重复取消测试 1 项。探针本轮目录为 `temp/pro-quality-20261111`，临时子目录及空父目录均已清理，Executor 输出存储已 close；未留下测试进程、异步任务或临时脚本。
