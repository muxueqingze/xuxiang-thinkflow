# v0.6 第 2 轮独立对抗验收

日期：2026-09-29。独立审查对象：`codex/thinkflow-desktop-v06` 工作树及 `artifacts/desktop-2026-09-29T08-11-05-413Z/ThinkFlow-win32-x64/ThinkFlow.exe`。源码检查进行期间主会话提交了 `74e67e2`，随后处理本轮即时反馈；下列“发现时”记录不代表修改后仍然存在。**发现时结论：FAIL（明确 P2，未复现 P0/P1）；修复后的定向复核见文末。** 本轮是功能与创意节点验收，不替代主人要求的 Pro 版本级终审。

审查者只写本报告、自有 `temp/review-2/` 驱动及 `docs/evidence/round-2/` 截图；没有改源码或已有测试。使用真实 Electron、真实私有 Python 后端、localhost HTTP SSE、自建文件和假密钥。独立包实测 `app.isPackaged=true`、`process.execPath` 为交付 exe，`app.getPath('userData')` 指向本轮临时目录；没有访问真实用户设置或收费端点。所有自行启动的 Electron/HTTP 服务均在驱动 finally 中关闭。

## 发现的问题

### S1 · P2：较长端点错误会将输入区挤出窗口

- 位置：`desktop/renderer/styles.css` 的 `.error-banner`、`.error-banner>span`；`desktop/renderer/app.js:showError`。
- 复现：实际 localhost SSE 返回 `type:error`，message 为“端点返回详细诊断：”加 300 次“错误段落内容”（约 1800 中文字符）。窗口外框 1000×700，内容区实际 986×664。
- 观察：banner 高 **1118.28px**，composer top **1294.28px**、bottom **1443.21px**，页面 scrollHeight **1485px**。真实截图中诊断占满会话列，输入/发送区域不在初始窗口内。用户可关闭错误恢复，但不应要求关闭错误才能继续看到主操作。
- 建议：限制错误区域高度，详细文本独立滚动，关闭按钮固定可达；仅裁剪字符串不如保留可滚动全文。
- 证据：[修复前长错误画面](../evidence/round-2/long-error-before.png)；`temp/review-2/adversarial-1790669772268/results.json` 的 `longError`，复现驱动 `adversarial-ui.cjs`。

### S2 · P2：新会话继承前一会话的错误横幅

- 位置：`desktop/renderer/app.js:applyState`，发现时只在非空 last_error 时显示，没有在 session 切换时清除旧提示。
- 复现：真实 SSE 提前 EOF → 显示 incomplete 错误 → 点击“新建会话”。
- 观察：新会话 `state.status=idle`、界面“准备就绪”，空消息区上方仍显示前一会话的 EOF 错误。状态表达互相冲突。
- 建议：成功切换 session/workspace 时清除旧会话提示，再按新会话自身 last_error/settings_warning 重建；不要无条件清掉当前运行中仍有意义的错误。
- 证据：[修复前新会话残留错误](../evidence/round-2/stale-error-before.png)；同一 `results.json` 的 `errorAfterNew`。

### S3 · P2：扩展命令取消已修，但输出读取仍无预算

- 位置：`src/interfaces.py:_communicate_owned`、`run_custom_tool`、`_run_image_command`。
- 复现：自建 custom Python 命令向 stdout 写 2 MiB 的 `a`。实际工具返回 **2,097,152 字符**，对应 receipt.stdout 仍为 **2,097,152 字符**。发现时 `_communicate_owned` 使用 `proc.communicate()` 全量收集，完成前没有实际读取限额。
- 影响：高输出命令会增加子进程捕获、模型 tool result 和未反馈快照的内存/上下文负担。没有进行 OOM 压测，不能声称已复现崩溃；这是首轮 R4 所提输出预算的残留缺口。
- 建议：并发 drain stdout/stderr，分别限制保存字节数且继续消耗多余输出，明确返回截断标记；沿用已修复的取消/超时终止并回收自身进程树。
- 证据：`temp/review-2/extension-boundaries.py`、`extension-results.json`。同一驱动的 timeout 子进程在返回后 0.9 秒仍未写 marker，证明生命周期修复有效。

### S4 · P3：长会话保留与导出说明不够准确

- 位置：`src/desktop_service.py:state/export_session`、`desktop/renderer/app.js:render`、`docs/desktop.md`。
- 发现时 UI 说“导出查看全部”，文档说显示对话和执行账本继续保留。实现为展示最近 120 条消息、每条末尾 32000 字符，账本展示/导出最近 500 条，已反馈 receipt 存储有 4096 条及正文预算；导出总长超过 16,000,000 字符会拒绝。
- 实测：自有快照注入 34 MiB 文本后仍可恢复、显示有界内容；点击导出得到明确上限提示，后端保持连接，文件未创建。原 R12 的崩溃风险在此路径已避免，但“全部”不宜无条件使用。
- 建议：写清“展示窗口、持久化对话、账本摘要、单次导出上限”四个不同边界。无需为本版重做分页导出。

## 首轮 R1–R12 定向复核

| 原项 | 独立证据与结果 |
|---|---|
| R1 native 去重 | 原复现复制到独立目录实际执行：同 id append 两次，真实文件为 **X**，只有一条 success；恢复及参数指纹测试通过。 |
| R2 native 批次失败 | 原复现实物：首条 edit failed、次条 write skipped，后续文件不存在；OpenAI/Anthropic 配对测试通过。 |
| R3 EOF 假完成 | 原解析器复现以及真实 HTTP SSE 均显示 error/incomplete，不创建残缺 write；UI 实际看到错误文字。 |
| R4 扩展进程生命周期 | 自建真实进程取消后等待 1 秒无迟到 marker；真实 timeout 后等待 0.9 秒也无 marker。输出预算另见 S3。 |
| R5 只读扩展 | 原真实 image command 复现：read_only=true、receipt failed、marker 不存在；额外测试确认未启动命令进程。 |
| R6 junction | 独立执行 `tests/test_security_paths.py`，真实 Windows junction 的 write/read/copy 源/copy 目标均拒绝，外部原文件未改。 |
| R7 bash 失败信息 | 原真实 Python 命令 exit(2)，模型 tool message 包含 `exit_code: 2` 和 `ASSERT: expected 2 got 1`；truncated 测试通过。 |
| R8 扩展假成功 | 原 fetch_url 缺参返回 failed，`_last_turn_failed=true`；普通包含 ERROR 的成功文本不会被误判。 |
| R9 错误字段 | 源码 renderer 接受 `event.message || event.error`；真实 EOF/error HTTP 场景中完整错误可见。长提示布局另见 S1。 |
| R10 恢复动作信息 | 亲读本轮真实 journal：write started 含 input_hash、content_bytes=30；bash started 含脱敏限长 cmd 和 input_hash。恢复 UI 确认锁可见，明确确认后没有自动重放。 |
| R11 加密配置恢复 | 真 Electron 用自建 damaged ciphertext 启动，UI 可进入设置、替换假密钥、加密文件不含明文；关闭重启后 has_api_key=true，warning 消失。 |
| R12 长会话 | 5 项 metadata 测试通过；34 MiB 自有快照的真实桌面恢复/超大导出拒绝保持连接。完整长时负载和累计磁盘写放大未做，不能声称无限会话支持。 |

定向测试共 15 项：9 个 harness 回归、5 个 runtime metadata、1 个 Windows 链接路径测试，全部通过。还独立执行了真实文件/子进程原始复现；没有循环跑全库。依据保存在 `temp/review-2/repro-core.py`、`repro-extensions.py`、`repro-bash.py` 及对应工具输出。

## 桌面与独立包亲验

- 源码与独立包都实际完成：初次选择工作区 → 配置假 localhost 模型和系统加密假密钥 → 键盘发送 → 流式写入真实文件 → success 账本 → 安全 Markdown/复制 → 分支 → 新会话 → 授权拒绝 → 停止流 → 导出 Markdown → 注入未完成 intent、恢复锁和明确确认 → 清除密钥。两者均没有 pageerror。
- 额外核心语义测试：保持 SSE 连接未结束，write 的真实文件及 success 回执已出现，状态仍 running，调用数仍 **1**；继续同一连接发送后续文本并完成。read 流经 barrier 后触发 **第 2 次**模型调用，真实请求包含已写文件内容。
- `contextIsolation=true`、`sandbox=true`、`nodeIntegration=false`；renderer 无 require；initialize 等越权 IPC 请求被拒绝。没有把这些检查等同于 shell 的 OS 级沙箱。
- 独立包窗口 1000×700 时内容区 986×664，document.scrollWidth=986；composer bottom=622.67，right=706，inspector left=730，未重叠。
- 长授权参数约 28000 字符时，参数区 clientHeight=94、scrollHeight=8214；允许按钮 bottom=441.07 小于 panel bottom=457.74，`elementFromPoint` 实际命中 approve。真实停止按钮也可操作。见[长授权参数画面](../evidence/round-2/long-approval-1000.png)。
- 干净的独立包会话预览：[实机画面](../evidence/round-2/packaged-conversation.png)。其余设置、恢复、两个窗口尺寸原图在 `temp/review-2/smoke-1790669674376/`，源码原图在 `smoke-1790669555445/`。

## 审美判断与边界

亲看欢迎、普通消息、账本展开、授权、恢复、设置和错误画面。三列分区明确，主会话保有足够宽度；米白与深绿一致，按钮、边框和留白克制；主要功能标签为中文，英文为产品名、协议、工具名和诊断等实用信息，没有无意义英文装饰。原 S1 导致的严重空间失衡应修复；其余已测画面没有遮挡主操作。

非阻断观察：最小窗口设置对话框存在内外两层滚动条，视觉略重；自动恢复到欢迎页时滚动位置可不在顶部。这些没有阻止保存或任务入口，不建议为凑“高级感”重做布局。未测移动端（产品为桌面最小 1000×700）、键盘/读屏全覆盖、不同 Windows 缩放和跨 OS。

PROTOCOL 第六节最初发现旧模板将 bash 列为旁路且将无错误等同全成功；主会话在本轮中已修，复读后不再作为未解决缺陷。

## 修复后定向复核

**最终结论：PASS（本轮约定的功能/视觉定向验收范围）。** 首轮缺陷与本轮 S1–S3 的必要修复已得到独立运行证据，S4 文案已澄清；未解决的 P0/P1/P2 为零。不是版本级全面审查，仍待主人 Pro 终审。

复核对象为 `artifacts/desktop-2026-09-29T08-19-51-393Z/ThinkFlow-win32-x64/ThinkFlow.exe`。再次实际核对 packaged=true、exe 路径和独立 userData，随后仅执行原失败场景及受修改影响的子进程边界：

- **S1 通过。** 相同约 1800 中文字符真实 SSE 错误，在同一 986×664 内容区，banner 高度 **128px**，composer bottom **622.67px**，document.scrollHeight **664px**。亲看[修复后错误画面](../evidence/round-2/long-error-after.png)，正文独立滚动，关闭、输入和发送区域可见。
- **S2 通过。** EOF 后新建会话，实际 status=idle，bannerVisible=false，error-text 为空。对应 `temp/review-2/focused-recheck-1790670123477/results.json`；驱动 `focused-packaged-ui.cjs` 对此有明确断言。
- **S3 通过。** 重新实跑 custom 2 MiB 输出：工具返回和 receipt.stdout 降为 **80081 字符**（80000 内容字节及截断说明）。另用真实子进程同时向 stdout/stderr 各写 2 MiB，两路捕获均为 **80081 字节**，均含 `THINKFLOW TRUNCATED`，子进程 exit=0；没有截断后停读造成管道阻塞。重新运行真实取消和 timeout 场景，返回后 marker 仍不存在。证据 `extension-results.json`、`bounded-output-after.json` 和对应本轮驱动。这里只验证输出捕获及修改涉及的生命周期，不把 80000 字节等同完整的全局资源配额。
- **S4 通过（文案范围）。** 复读代码/指南：长消息与窗口提示写明导出上限1600万字符；导出账本标题明确“最近500条；旧正文可能已摘录”；指南说明界面120/500窗口及正文裁剪，未再声称无限完整导出。原34 MiB拒绝导出测试已有证据，未重复生成大数据。
- PROTOCOL 第六节复读确认将 write/append/mkdir/touch/copy/edit 与 read/bash 反馈边界区分，并明确“生成继续不代表队列已经执行成功”。

一次追加“干净欢迎截图”的 Playwright 截图调用超时（核心两项断言此前已经通过），驱动正常关闭应用；显式聚焦窗口后只重试同一截图/失败项，成功退出。未据此推断产品故障或隐瞒为首遍全通过。最终另启动全新隔离 profile，捕获并亲看[新版独立包干净首屏](../evidence/round-2/final-packaged-window.png)。

临时目录包含自建快照、假密钥、复现脚本与原始图；正式证据已复制至本报告和 `docs/evidence/round-2/`。没有本轮留存的运行进程；主会话收尾可按项目规则可恢复清理 `temp/review-2/`，不必保留大快照。商业 provider、长时负载、真实掉电/磁盘故障、账户迁移及无障碍全覆盖仍是未验证边界。
