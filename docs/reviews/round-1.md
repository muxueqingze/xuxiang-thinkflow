# v0.6 第 1 轮独立对抗审查

日期：2026-09-29。审查基线：`b45c01e`；被审对象为 `codex/thinkflow-desktop-v06` 当时未提交工作树。**发现时结论：FAIL。** 本文记录缺陷发现时的实现，不因实现者随后修改而自动宣称通过。行号为发现时位置，修改后以函数名定位。

审查者没有修改源码或已有测试；只新增本报告与 `temp/review-1/` 隔离复现。没有读取真实凭据、调用收费模型、操作他人进程或争用桌面 GUI。

## P1：必须修复

### R1. Native tool id 不去重，重复副作用可以再次执行

- 位置：`src/agent_loop.py:1644–1684`，`_execute_traditional_tool`；对比 `_execute_command:1506`。
- 复现：同一 agent、同一 `tool_id="native_same"` 两次 native `append(path, "X")`。真实文件内容为 **XX**；产生两条同 id 的 success 回执。Native id 未进入持久化的 `_executed_ids`。
- 影响：provider 重复调用、恢复后重放、同批重复 id 都能重复追加/执行非幂等命令；协议的重复 id 保护只覆盖文本工具。
- 最小修复：两种通道共用执行意图、去重与冲突判定；同 id 同输入复用原回执，不同输入失败。Native 指纹须包含完整规范化 JSON input，不能只用现有 Command 的 path/cmd/content 字段，否则 url/pattern/custom 参数无法区分。恢复要保留同一保护。
- 证据：`temp/review-1/repro.py`，`native_duplicate.content="XX"`。

### R2. Native 批次首条失败后，后续副作用仍执行

- 位置：`src/agent_loop.py:1559–1598`，`_handle_traditional_tools`。
- 复现：一个批次先 `edit(absent.txt)`，再 `write(after-failed-native.txt)`。账本依次 failed、success；第二文件实际存在。
- 影响：模型依据尚未收到的成功假设继续修改工作区；文本 FIFO 已有失败截断，native 通道没有。
- 最小修复：首个失败后停止本批次剩余操作，为剩余 tool calls 返回明确 skipped 结果，保留 provider 要求的 call/result 配对；不要把 skipped 覆盖成根因。
- 证据：`repro.py`，`native_batch_failure.second_executed=true`。

### R3. SSE 未收到正常结束标记便 EOF，被报告为 completed

- 位置：`src/agent_loop.py`，`_process_stream` 尾部与 `_run_one_turn_impl` 的默认 `AbortReason.NONE` 收尾。
- 复现：真正调用 SSE 解析器，响应只有一帧 `<tf-write id="1" path="truncated.txt">partial`，之后正常迭代结束，无 finish_reason、[DONE] 或 message_stop。
- 实际：文件没有创建，`last_error=""`，`run_finished.stopped_reason="completed"`，没有续写。
- 影响：代理/网关截断连接但以正常 EOF 结束时，未完成任务会显示正常完成；完整 native 参数与不完整批次也需要统一处理这种结束状态。
- 最小修复：区分 provider 正常终止与 EOF；缺少终止信号时返回流中断错误或受限续写，丢弃残缺命令，禁止默认为 completed。
- 证据：`repro.py`，`unfinished_eof`。本项使用自定义 response 的真实 `aiter_lines` 接口，没有替换 `_process_stream`；没有启动真实 HTTP 服务器，HTTP 正常 EOF 到该接口的映射仍可补集成验证。

### R4. Custom/image 子进程取消和超时后仍可能继续产生副作用

- 位置：`src/interfaces.py:215–244`、`:269–296`，`run_custom_tool`、`_run_image_command`；native 包装在 `agent_loop.py`。
- 复现：自建 custom Python 子进程，等待 0.7 秒后写 `custom-after-cancel.txt`。运行 0.25 秒后取消工具并等待返回；此时 marker 不存在，1 秒后 marker 出现，账本已显示 cancelled。
- 影响：桌面“停止”后后台继续修改文件；运行时限不构成扩展工具的副作用边界。两处 timeout 分支也直接返回，未终止进程。
- 最小修复：扩展命令复用受管理的子进程生命周期；取消/超时必须终止自身进程树并 wait/drain 后返回。运行输出亦应有实际读取限额，而非只在完成后裁剪。
- 证据：`temp/review-1/repro_extensions.py`，`marker_at_return=false`、`marker_after_return=true`。测试子进程已自然结束；没有杀其他进程。

### R5. Read-only 对 image/custom 扩展副作用没有统一约束

- 位置：`src/agent_loop.py:1644–1674`、`_authorize_tool`；`src/interfaces.py:image_generate`；`src/security.py:check_write_allowed`。
- 复现：使用 `SecurityPolicy.from_config(profile="read-only")`，启用本地 image command，执行 native image_generate。测试命令真实写出 `readonly-marker.txt`，结果 success，`policy.read_only=true`；image risk 为 medium，因此甚至没有高风险授权回调。
- 影响：界面/CLI 只读语义对新工具不成立；开启生成器后可执行任意预配命令。Custom 在逐次批准后同样没有只读硬限制。
- 最小修复：在所有副作用工具进入 handler 前统一执行只读检查，以工具 kind/capability 分类，不能只在内建文件/bash 分支中检查；一次批准不能改变只读边界。
- 证据：`repro_extensions.py`，`read_only_extension.marker_exists=true`。未调用真实生图服务。

### R6. Windows junction 绕过 allowed_roots

- 位置：发现时 `src/security.py:174–188`、`src/executor.py:_normalize_path/_resolve_path`。
- 复现：允许根为 `temp/review-1/junction-workspace`，其中 `link` 是指向同测试目录下 `junction-outside` 的 junction。写 `link/outside.txt` 返回 success，真实文件出现在允许根外。
- 影响：文件工具的工作区约束可被现存链接绕过；read/copy 的两端同类受影响。此处是文件工具实际约束缺陷，不是要求 shell 获得操作系统级沙箱。
- 最小修复：规范化根与目标真实路径，写不存在目标时解析已有父链；Windows 路径比较考虑大小写，敏感文件检查覆盖别名与实际目标。
- 证据：`temp/review-1/repro_junction.py`。
- **跟进状态**：主会话已通知按 realpath/normcase 修复，并自行定向检查原复现与 read/write/copy；本审查者未重复运行，不能将通知当独立复验通过。旧 outside.txt 已存在，再验证不能仅用 exists 判断。

### R7. Native bash 失败时丢失 stdout 和 exit_code，且不再通过账本反馈

- 位置：`src/agent_loop.py:1701–1719`，`_format_tool_result`；`_handle_traditional_tools` 末尾 mark_injected。
- 复现：真实本地 Python 命令输出 `ASSERT: expected 2 got 1` 到 stdout 后 `exit(2)`。账本保存 stdout 和 exit_code=2，但发给模型的 tool message 只有 **bash failed**；该回执随后 injected=1。
- 影响：pytest/编译器等常把诊断写 stdout。模型拿不到实际失败依据，下一轮普通账本也不补发，无法按错误修复。
- 最小修复：bash 无论成功失败都返回 exit_code、stdout、stderr、truncated/timeout；附加错误原因。不要在通用 failed 早返回处丢掉诊断。
- 证据：`temp/review-1/repro_bash_feedback.py`。

## P2：明确的契约与持续运行问题

### R8. 扩展工具的失败字符串被标为 success

- 位置：`src/agent_loop.py:1667–1669`；`src/interfaces.py` 的错误返回分支。
- 复现：新 agent 调用 `fetch_url({})`，返回“缺少 url”，但回执 success，`_last_turn_failed=false`。其他配置缺失、HTTP 失败、custom 非零退出同一机制。
- 影响：桌面账本显示假成功，连续失败限制不能统计这些失败。错误文本虽反馈模型，但执行状态与审计不可信。
- 修复：接口返回结构化成功/失败结果或明确异常，再转换 provider 文本；不要靠语言关键词判断状态。
- 证据：`repro_extensions.py`，`error_status`。

### R9. 桌面 error 事件字段不一致

- 位置：`src/events.py:RuntimeView.__getattr__` 与 `desktop/renderer/app.js:270`。
- 复现：`view.render_error("observable failure")` 实际发 `{type:"error", error:"observable failure"}`，前端只读 `event.message`。
- 影响：流错误/失败打断时 banner 没有实际错误文字；最终 state.last_error 只能部分兜底，工具失败后继续推理的中间反馈仍丢失。
- 修复：统一 error event 的 message 字段并覆盖真实事件消费，不只测服务手工发出的 message 事件。
- 证据：`temp/review-1/repro_observability.py` 输出；前端消费为源码证据，未占用 GUI。

### R10. 崩溃恢复意图缺少原始动作的可核对信息

- 位置：`src/agent_loop.py:_tool_event` 与 `src/desktop_service.py:_on_agent_event/_unresolved_intents`。
- 复现：对 `bash cmd="echo benign"` 发真实 tool_started，事件只有 id/tool/channel/flow/risk/path=""/dest=""/need_result，没有 cmd、输入摘要或 hash。
- 影响：工具开始前 journal 是唯一持久化意图，此时 context 尚无 record。崩溃恢复会要求用户核对“bash”，却无法显示执行过什么；write 也不能核对预期内容 hash。生成式原命令不保证存在于 assistant 可见文本。
- 修复：在开始事件持久化经过脱敏、有限大小的执行参数摘要、输入 hash/字节数；shell 至少保留可核对命令。不要保存凭据或把巨大正文复制进事件。
- 证据：`repro_observability.py` 的 tool_started 实际输出。

### R11. 旧凭据无法解密时，应用无法通过设置清除或替换它

- 位置：`desktop/settings.cjs:prepare`，`desktop/main.cjs:initialized/wrapped`。
- 复现：纯假 safeStorage.decryptString 抛错，`store.prepare({...DEFAULTS, clear_api_key:true})` 仍在清除前调用 backendConfig，因此抛“已保存的密钥无法解锁，请重新设置密钥”。main 初始化同一错误后，所有 IPC 先 await 已 rejected 的 initialized。
- 影响：更换 Windows 账户/系统凭据失效时，“重新设置”无可用 UI 路径，必须手工处理 settings.json。
- 修复：清除/提供新 key 不应先解密旧 key；初始化凭据失败应允许进入设置修复，后端可先空凭据初始化或支持显式重试。
- 证据：`temp/review-1/repro_desktop.cjs`；没有读取或修改真实设置。

### R12. 长会话没有展示与账本保留预算，最终合法 state 会杀后端

- 位置：`src/desktop_service.py:state/_save/_on_agent_event`、`src/context.py:records/to_dict`、`desktop/backend.cjs:receive`。
- 证据：transcript 每次 state 全量发送且没有上限；compact 只压缩 agent.messages。实际构造 100 条同路径 10,000 字符 write 回执后 compact，仍保留 100 条、1,000,000 字符正文。每个 started/completed 又同步序列化/fsync 全量快照，长期累计写放大。前端 transport 收到一条合法、正文为 32 MiB 的 state JSON，真实 receive 方法立即 fail 并请求 kill。
- 影响：足够长的正常会话最终使后端断开，重启/恢复再次发送同一超限 state；“压缩上下文”不能解决。文件改写历史也会令快照成本不断增长。
- 修复：state 使用有界 transcript 窗口/分页与增量事件，账本正文按预算裁剪或拆为独立归档；超限应给可恢复错误而不是直接终止后端。保留消息/回执总量的可观察统计。
- 证据：`repro_observability.py`、`repro_desktop.cjs`。32 MiB probe 用 stub child.kill，仅检查协议方法，没有启动或杀真实后端；没有做长时负载测试。

## 已实际通过的检查

- `python tests/test_harness.py`：19/19，通过，14.653 秒。
- `python tests/test_desktop_service.py`：17/17，通过，7.695 秒。包括真实 localhost HTTP SSE 的正常写入/恢复、空 API key、逐次授权拒绝。
- 源码检查支持：文本 FIFO 的失败抢占和 queued skipped；事件启动先持久化、完成先 snapshot 后 marker；取消时文件线程收尾；私有 NDJSON、窄 preload、contextIsolation/sandbox、禁止页面导航；会话 key 不回传的基本路径。
- 以上通过不覆盖 R1–R12，不能据此认定整体通过。未循环跑全库测试。

## 未验证边界与交接

- 第一轮不争用 GUI；独立 Windows 包、安装/冷启动、视觉质量、真实窗口交互留第二轮专家核验。
- 未调用真实商业 provider、未测收费生图、未做跨操作系统和长时负载。
- 未模拟真实掉电/磁盘损坏、Windows 账户迁移；恢复/加密边界以源码与隔离故障注入为证据。
- 所有临时实物在 `temp/review-1/`。其中含自建 junction，目标也在同一审查目录；收尾可恢复清理时先确认链接，勿把历史 marker 的存在当修复后成功/失败证据。
- 修复后只需针对原失败项复验。R6 已由主会话进入修复，其余项均已即时报告；本轮保留 **FAIL（发现时）**，通过状态应由具体修复证据追加，不能重写掉原观察。
