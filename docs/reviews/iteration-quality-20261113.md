# 四项迭代定向对抗验收 · 2026-11-13

## 问题与修复

### [P1，已修复复核] 崩溃恢复后的补充被投递到后续任务

- 位置：`src/desktop_service.py` 的 `resume` 输入恢复循环，原 497–505 行；对比 `_run.finally` 对未投递补充的处理。
- 初始实现只有正常收尾会清除 pending 输入的 `delivery=steer`；崩溃快照恢复时只把 running 改为 interrupted，遗留的 steer 会被下一个普通任务取走。
- 独立复现使用真实 `DesktopService.submit_input/_begin_run/resume` 及隔离落盘。检查点包含 A running、B queued、S queued/steer（正文 `CORRECTION_FOR_A`）。恢复并继续队列后，B 与 S 成为同一 run 的 `request:1/request:2`，`active_refs` 同时保护两者，S 被标 completed。第二个实际 provider 请求同时携带 B 和 A 的补充。
- 修复：在现有恢复循环中将 pending steer 降为普通队列，并标记 changed 走原保存路径；不改变队列位置和提交指纹。
- 原路径复核：恢复内存与磁盘均不再带 delivery；原带 steer 的重复 submit 仍查询到同一 queued 回执；继续后 2 次 HTTP、2 次独立 run_finished、0 次 steering_applied、`active_refs=[]`。B 的请求不含 S；最终 A interrupted、B completed、S completed。问题关闭。

没有剩余 P0/P1。本轮没有提出额外防护功能或架构重写。

## 结论：PASS

范围是基线 `38a0807` 之后的四项迭代及其直接影响，审查未提交工作区实物。这是功能节点的定向验收，不是版本级全面终审。

| 用户条件 | 独立核查与结果 |
|---|---|
| 可预测状态更新符合原协议、真正可预测 | `tf-update_plan` 解析 JSON 后复用现有验证和持久化，成功结果是固定确认，不需要外部新信息。FIFO 中的计划→写入→引用该成功回执完成计划在同一 SSE 响应执行；错误仍反馈并跳过依赖操作；显式 need_result 仍增加结果边界。原生模式保留 provider 工具回合，没有把原生工具伪装成可跨回合的流式命令。只读模式可以更新本地计划。通过。 |
| 搜索仅作优化 | 保持 Python 正则、现有权限与脱敏、相对路径/行号输出；三个工具共用惰性遍历，PathSpec 承担 ignore 规则，明确指定忽略目标仍可搜索。没有增加摘要 API、工具重试或第二套搜索语言。实测嵌套否定规则、Unicode、显式目标、正则后顾/反向引用、截断及线程取消均通过。 |
| steering 好好测试且不影响性能 | 响应和完整工具批次结束后接入，既有读写先结算；两种原生 provider 的工具结果均在补充之前。无补充空队列与没有 steering_source 的请求正文逐字一致、HTTP 次数相同，离线本地开销相同量级。崩溃恢复串任务的问题已经按原路径关闭。通过。 |
| 长程上下文参考成熟 harness 设计思想 | 已读取固定 Pi 源码研究和 Anthropic 上下文工程资料的项目记录，设计落到现有 task_plan 的可选 context，不新增记忆数据库、自动摘要调用或每步笔记。当前有效决定和导航随原请求投影发送；原始要求及同一 run 的补充保持独立原文；执行事实仍来自 receipt 和读取基线。两次压缩、恢复/分支及 provider 请求正文核查通过。 |

## 独立运行证据

### 已有回归的实跑

- `python -m unittest discover -s tests -p test_stream_state.py -v`：4 项通过。阅读了测试与对应运行实现；流式成功测试在响应未结束时观察到真实文件和回执，不只检查最终属性。
- `python -m unittest discover -s tests -p test_task_context.py -v`：8 项通过。包括连续两次压缩、SessionStore 的 plan_updated 落盘、DesktopService resume/fork、OpenAI/Anthropic × 原生/文本请求、旧计划对象形状、阶段导航不能授予编辑所需的读取基线。
- `python -m unittest discover -s tests -p test_workspace_search.py -v`：3 项通过。取消用例确认线程收到停止信号并结束，不以 asyncio 任务返回代替工作线程退出。

### 独立原生协议探针

以本地 MockTransport 提供实际 SSE 事件，运行真实 AgentLoop、工具注册器、计划验证与 provider 消息投影。启用 read_only，第一轮原生调用 update_plan，第二轮正常完成：

| provider | 补充 | HTTP | 次请求的消息角色（省略 system） |
|---|---|---:|---|
| OpenAI | 无 | 2 | user → assistant → tool |
| OpenAI | 有 | 2 | user → assistant → tool → user |
| Anthropic | 无 | 2 | user → assistant → user(tool_result) |
| Anthropic | 有 | 2 | user → assistant → user(tool_result) → user(补充) |

四条轨迹均保存同一已知 plan，成功 receipt，正常 completed；有补充没有增加确认轮。

### 无补充时的请求与本地耗时

真实 AgentLoop、read FIFO、SSE 解码；`steering_source=None` 对比 `DesktopService._take_steering` 空队列。每轮从同一快照开始，先读一个文件再正常回答；交替执行顺序，32 轮去掉前 2 轮热身。

| 指标（30 对） | 无 source | 桌面空队列 source |
|---|---:|---:|
| 每 run HTTP 尝试 | 2 | 2 |
| 本地耗时中位数 | 1.110 ms | 1.118 ms |
| 本地耗时 P90 | 1.162 ms | 1.164 ms |

全部 30 对的两次请求正文逐字相等。测量隔离的是本地接线开销，使用 MockTransport，不涉及真实模型网络时间。没有据此新增模型效率成绩。

## 已有 UI 证据与旧夹具归因

阅读了 [桌面实测记录](steering-ui-20261113.md)、对应 Electron 测试和 renderer/backend 差异。本轮不重复启动用户桌面；真实 Electron 的输入法、排队快捷键、回执丢失、窗口布局和 4 次回环 HTTP 证据由该专项记录提供。

旧 `electron-ux.cjs` 的两个阻断与本次 steering 无因果关系：基线 `38a0807` 已存在 `#inspector.isVisible()` 的初始断言，也已存在 `messages.slice(0,-1)` 后提取最后 user 的夹具逻辑。基线 `_messages_with_runtime_status` 已经通过 `with_observation` 把状态并入最后 user，并非必定额外追加一条；旧夹具因此会删掉当前输入。不能用这次夹具超时宣称新运行链回归，也不能把未走到的 UI 项目当作已经通过。

## 声明边界与收尾

- 搜索专项报告的性能和冻结产物证据见 [search-20261113.md](search-20261113.md)；本审查亲自重跑功能/权限/取消测试，没有重新构建发布包。
- 运行中补充的正式全量集成检查由主会话集中执行；本报告记录的是上面的独立定向运行及已关闭的问题。
- 未运行付费模型请求、未修改用户桌面或登录、未提交或推送。独立探针只使用 `temp/iteration-qa-20261113/`，服务与 clients 均已关闭；本轮隔离数据核对绝对路径后移入回收站。无新增依赖。

## 0.10.0 正式 ZIP 亲验：PASS

检查对象：`artifacts/desktop-2026-11-13T07-30-02-771Z/ThinkFlow-0.10.0-Windows-x64.zip`，189,849,276 字节。解压读取全部 245 个 ZIP 条目时完成一次 CRC 校验，通过。后续运行复用该次解包，没有重复扫描 CRC。

- 从 ZIP 解出的 frozen `resources/backend/thinkflow-service.exe` 启动，子进程 PATH 只有 `C:\Windows\System32`，清除 THINKFLOW 环境覆盖；该 PATH 没有系统 Python。
- 本地 HTTP SSE 首次生成依次提交 `tf-update_plan`、`tf-write`、带成功写入回执及 context 的 completed 计划。服务在流结束之前依次交出三张成功回执，实际文件正文正确；整个任务 1 次 HTTP。
- 第二项任务同批执行 `tf-grep`、`tf-glob`、`tf-python`，一次反馈后完成，共 2 次 HTTP。搜索只返回可见文件和真实行号，`.gitignore` 排除的文件路径与内容均未出现在反馈中。
- Python 工具实际执行标准库 `json/sys/pathlib`，算得 10 并读回交付正文；输出的 `sys.executable` 精确指向 ZIP 内 `resources/backend/python/python.exe`。
- 关闭并重新启动同一解包后端、打开原工作区：恢复同一 session，completed/evidence 与完整阶段 context 一致；恢复过程增加 0 次模型 HTTP。
- 包内 `PathSpec-LICENSE.txt` 含 PathSpec 1.1.1 版本及 Mozilla Public License 原文；独立 Python 与 `_internal/winpty/OpenConsole.exe` 均在包内。

机器可读证据：[iteration-package-20261113.json](../evidence/iteration-package-20261113.json)。本轮采用 stdio 服务协议和本地 HTTP 真实进程，未导入仓库运行源码代替成品。首次取证脚本误把 ledger 的摘要字段当作 stdout JSON；改为从实际 provider 反馈取 stdout 后原路径通过，产品代码未改。

两次成功后端均以退出码 0 关闭，回环服务已关闭。解包、探针脚本和所有轮次数据都在 `temp/iteration-package-20261113/`，核对绝对路径后整体回收，原目录不存在。正式 ZIP 未改动。本段只补打包产物的定向验收，不重复版本级终审。
