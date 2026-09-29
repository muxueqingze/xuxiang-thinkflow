# 续想 ThinkFlow · v0.6 架构

2026-09-29。原 v0.5 设计保留于 Git 提交 `b45c01e`。当前实现以本文、PROTOCOL 1.4 与源码为准。

## 核心判断

工具是否需要形成模型回合边界，取决于它的结果是否为后续推理提供新信息。可预测的文件写入可以在输出流中排队执行；读取、搜索、测试、shell、失败与显式结果依赖需要反馈边界。

这是一种有边界的乐观执行，不是事务，也不是「正在生成就代表已成功」。最终成功以执行回执为准。模型必须支持可读取的输出流以及遵循协议；无法获取 thinking 的端点可以使用 text 通道。不会假定所有供应商都暴露原始推理内容。

## 运行结构

```text
CLI / Electron desktop
       ↓ 用户输入、配置、取消、单次批准
    AgentLoop（同一实现）
       ├─ provider SSE → thinking/text parser → 工具分流
       │                                  ├─ delayed → FIFO 单 worker
       │                                  └─ blocking/confirm → barrier + 返回结果
       ├─ native tool fragments → 按 index 聚合 → 工具执行
       ├─ Executor / ToolRegistry / SecurityPolicy
       ├─ ContextManager 命令账本 → 下一轮反馈
       └─ RuntimeEvents → CLI renderer 或 DesktopService
                                   ↓
                          原子快照 + 意图/回执日志
```

### 必须成立的约束

- 解析器只提交完整 canonical tf-* 标签；普通 XML 和正文代码围栏示例不执行。
- 可预测操作严格 FIFO；信息型工具先等待之前的写入，再执行并反馈。
- 后台失败与 SSE 读取竞速；发现失败后停止队列中尚未执行的操作。
- 同一块中落在反馈边界之后的命令记 skipped，由模型根据结果重新决策。
- 截断或断连不把不同请求的残缺命令拼在一起；未完整的命令丢弃，下一请求使用新 id。
- 取消会等待已开始的文件操作收尾，并清理本次 shell 进程；不回滚已完成副作用。
- 重复 id 不能伪造成功；相同命令返回原回执，内容冲突报错。去重窗口仍有限，不提供分布式 exactly-once 保证。
- 模型的自然结束只是本轮结束；不等同于所有需求均被验收。达到40轮、1800秒或连续3次失败会停止。

## 模块职责

| 模块 | 职责 |
|---|---|
| `agent_loop.py` | provider 调用、反馈边界、FIFO 生命周期、运行限额 |
| `parser.py / text_filter.py` | 完整命令解析、显示过滤、围栏保护 |
| `executor.py / security.py` | 文件与shell动作、路径规则、敏感文件与命令策略 |
| `tool_registry.py / interfaces.py` | 工具schema、分流/风险、自定义接口 |
| `context.py / compaction.py` | 可审计回执、结果注入、确定性摘录 |
| `events.py` | 实例级事件与可选终端视图，不修改全局renderer来服务桌面 |
| `session.py` | 原子快照、fsync、历史保留 |
| `desktop_service.py` | 私有NDJSON RPC、工作区会话、分支、授权、恢复、导出 |
| `desktop/` | 本地界面、窗口、目录对话框、系统加密凭据 |

## 桌面进程边界

Electron main 通过子进程 stdin/stdout 与 Python 服务通信，没有本地 HTTP 监听器。renderer 不解析 tf-*、不执行文件操作、不持有回读凭据的接口。preload 仅暴露允许的方法。页面使用本地资源、CSP、contextIsolation 和 sandbox；这保护 UI 边界，不代表模型的 shell 被操作系统隔离。

独立 Windows 包包含 Electron 与 PyInstaller Python 运行时，用户无需安装 Python/Node。开发依赖仍分别由 pip 与 desktop/package-lock.json 管理。

## 持久化与恢复

每个工作区用规范化绝对路径的摘要隔离会话。会话包含模型上下文、账本、用量、展示用对话与事件序号。已反馈账本保留最近4096条、正文约200万字符预算，hash与摘要保留；未反馈观察不会删除。桌面state仅发送最近120条消息和500条账本，长消息明确标记显示截断，完整展示对话保留在本机。API key 由桌面 main 使用 safeStorage 加密保存，不写入会话；系统加密不可用时不得降为明文持久化。

工具启动前 fsync 写 intent；完成后先原子保存包含回执的 snapshot，再写 completed 标记。崩溃窗口宁可留下待核对意图，也不自动重复副作用。恢复存在未完成或取消中的意图时，必须人工核对后继续。确认只解除恢复锁，不重放工具，不声称自动回滚。

CLI 继续沿用原有会话路径和策略；桌面使用独立数据目录，尚未提供 CLI 历史导入。跨进程共同写同一工作区不是本版支持的调度能力。

## 可观测性与评估

工具事件区分开始、完成、成功、失败、跳过与取消。账本展示 path/hash/bytes/flow/risk。用量来自供应商返回；缺少usage时不应据此推断免费调用。节省API调用是「成功的delayed操作若逐条同步调用」的反事实估算，不是对原生批量调用的实测胜率，也不是实际省下的钱。

验证分三层：解析/调度隔离测试，真实本地HTTP SSE与文件系统集成，实际桌面及独立包流程。真实商业端点、长时负载与多操作系统是单独的验证边界。

## 后续扩展

见 [harness 能力地图](docs/harness-roadmap.md)。优先扩大可验证的能力，避免在可靠性地基尚未验证时叠加自治调度。
