# Production benchmark 定向预检 · 2026-09-29

## 问题与结论

本轮未发现阻塞开跑的 P0/P1，亦未确认需要修复的 P2。**当前 production 路径可以冻结后开跑。** 这是移除实验预算与隔离入口的定向预检，不是 Pro 版本终审，也不代表真实服务商长任务已经通过。

审查者读取了当前工作树的 provider、agent_loop、cli、model_registry、desktop_service、桌面 settings/renderer，以及 benchmark meter、run、worker、adapters、monitor、两个 production 钩子与 probe。没有读取真实密钥、调用模型或使用 Claude Code；下面实测均使用离线 MockTransport 或 127.0.0.1 假 SSE 服务。

## 独立实测

| 检查 | 实际结果 |
|---|---|
| 当前 Pi CLI 的 production adapter | exit 0，1 次实际 HTTP 请求；max_tokens、max_completion_tokens、max_output_tokens 全部缺失；4 个工具。复跑覆盖了取消 maxRetries=1 后的当前配置 |
| 当前 OpenCode CLI 的 production adapter | exit 0，1 次实际 HTTP 请求；上述三个输出预算字段全部缺失；10 个工具 |
| 完整 ThinkFlow worker 子进程 | exit 0，1 次实际 HTTP 请求；上述字段全部缺失；harness.execution_config 中 max_tokens、max_run_turns、max_run_seconds、max_auto_continues 全为 null，compaction.enabled=false |
| ThinkFlow worker 最终状态 | stopped_reason=completed、monitor_errors=[]、stderr 为空；live.phase=finished、run_active=false、stopped_reason=completed |
| ThinkFlow 请求快照与旧压缩阈值 | 构造 100 条、402,990 字符历史；连续构造 5 次请求，messages 与 context.to_dict() 深比较不变；compact(force=False) 返回 changed=false；请求保留全部 100 条历史，另加 system/runtime 两条 |
| OpenCode 父目录指令隔离 | 在专用临时父目录放置唯一 AGENTS.md 哨兵后，重新启动独立 profile；exit 0，实际 wire 中没有哨兵 |
| tests/test_optional_output_budget.py | 独立执行 13/13 通过，23.208 秒；含 provider/agent/CLI 请求字段、profile 切换、Anthropic 显式预算、None 运行轮次、续写次数与原有限额保留 |

真实 CLI 用当前 adapters.configuration(..., mode='production') 启动，外部密钥没有进入子进程。ThinkFlow 使用 stdin 提交测试 prompt，没有绕开 worker 的环境分流或 monitor。

## 源码核对

- `meter.py` 的 production 构造覆盖三个实验预算为 None；请求数、总时间、8 MB 请求体与响应体截断仅在 bounded 生效。发现客户端输出预算时显式拒绝，不静默替客户端删除。转发保持 messages 不变，并分别记录前后哈希与输出预算字段；完成时等待已接收的上游请求结束再保存汇总。
- `run.py` 的 production 进程等待没有 300 秒总截止；8 秒 communicate timeout 仅触发进度轮询。manifest 纳入生产钩子、适配器、计量器、worker、monitor、锁文件、任务和 src；记录 production 模式、空预算、能力元数据。修改冻结源码后必须换实验名，正式跑期间仍须保持冻结文件不变。
- `thinkflow_worker.py` 明确传入 None 和关闭 compaction；没有依靠旧默认值。正常 HTTP 空闲超时、连接超时、bash 超时、工具分页、账本历史载荷保留和连续故障保护不等于本轮要求移除的整个任务/上下文实验预算。
- ThinkFlow 的消息压缩确实因 enabled=false 提前返回。`context.compact_history()` 仍会整理已注入账本载荷，但不改已进入 messages 的历史；本轮没有将其误判为总上下文截断。monitor.snapshot 只构造请求并读 usage，不调用 build_injection，也不消费回执。
- Pi 本机安装源码的 `_checkCompaction()` 在 settings.enabled=false 时直接返回，涵盖 threshold 和 overflow 分支；resource-loader 的 noContextFiles 路径为空，noExtensions 仅保留显式 CLI 扩展。production hook 只删除输出预算字段。
- OpenCode production 配置明确关闭 auto/prune，使用独立 HOME/XDG 与禁用项目配置发现；显式 hook 只清除 maxOutputTokens。真实请求与父目录指令哨兵结果支持当前入口有效。未声称操作系统沙箱隔离；各 harness 的命令工具仍按其权限执行。
- 桌面 UI 将空输入序列化为 null，回填 null 时为空；OpenAI 默认不设输出预算，显式正数保留；Anthropic 缺预算明确报错。桌面的有限任务运行设置仍保留，benchmark 由独立 worker 显式关闭总预算。

## 证据边界与收尾

本轮未重复主会话负责的 meter 大请求/超过24请求/超过300秒回归、Node settings 测试及 analyze 文案检查；那些结果不计入本审查者独立实测。服务商能力与省略字段后的默认输出长度仅核对为已记录元数据，没有另发真实 API 验证；“客户端不加输出预算”不代表服务端无限输出。真实长任务仍应按原始 meter、终态、测试执行证据与外部 grader 分别报告。

本审查只新增本报告，没有修改实现。临时 CLI profile、wire 与测试文件仅位于 `temp/production-review`，检查结束后整体送入回收站；正式运行无需这些临时文件。
