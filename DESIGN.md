# 续想 ThinkFlow · v0.8 架构

更新于2026-11-10。原 v0.5 设计保留于 Git 提交 `b45c01e`。当前实现以本文、PROTOCOL 1.6 与源码为准。

## 核心判断

工具是否需要形成模型回合边界，取决于它的结果是否为后续推理提供新信息。可预测的文件写入在输出流中排队执行；参数已知的独立读取和检查在同次生成中集中声明，结果在批次结束后统一反馈。依赖尚未返回的信息，才需要先结束生成。真实失败、显式need_result和confirm仍即时形成边界。

最小信息量用于选择相关文件、搜索范围和检查输出，并去除已存在的精确正文副本；首次工具结果、实际错误和供应商要求的reasoning完整保留。省量主要来自减少反馈回合，不靠减少必要工程工作或截短思考。

这是一种有边界的乐观执行，不是事务，也不是「正在生成就代表已成功」。最终成功以执行回执为准。模型必须支持可读取的输出流以及遵循协议；无法获取 thinking 的端点可以使用 text 通道。不会假定所有供应商都暴露原始推理内容。

## 运行结构

```text
CLI / Electron desktop
       ↓ 用户输入、配置、取消、单次批准
    AgentLoop（同一实现）
       ├─ provider SSE → thinking/text parser → 工具分流
       │                                  ├─ delayed → FIFO 单 worker
       │                                  ├─ blocking → FIFO + 批次结果一起返回
       │                                  └─ need_result/confirm → barrier + 立即反馈
       ├─ native tool fragments → 按 index 聚合 → 工具执行
       ├─ Executor / ToolRegistry / SecurityPolicy
       ├─ ContextManager 命令账本 → 下一轮反馈
       └─ RuntimeEvents → CLI renderer 或 DesktopService
                                   ↓
                          原子快照 + 意图/回执日志
```

### 必须成立的约束

- 解析器只提交完整 canonical tf-* 标签；普通 XML 和正文代码围栏示例不执行。
- 所有文本命令沿用单worker FIFO；信息工具先等之前的写入，已知独立调用在同批声明，成功结果按序完整反馈一次。批量反馈不等于本机并行。
- 后台失败与 SSE 读取竞速；发现失败后停止队列中尚未执行的操作。
- 同一块中落在显式need_result/confirm即时边界之后的命令记skipped，由模型根据结果重新决策；普通read/bash/python不会截掉同批后续声明。
- 截断或断连不把不同请求的残缺命令拼在一起；未完整的命令丢弃，下一请求使用新 id。
- 取消会等待已开始的文件操作收尾，并清理本次 shell 进程；不回滚已完成副作用。
- 重复 id 不能伪造成功；相同命令返回原回执，内容冲突报错。去重窗口仍有限，不提供分布式 exactly-once 保证。
- 模型的自然结束只是本轮结束；不等同于所有需求均被验收。应用默认达到40轮或1800秒停止；连续失败阈值默认null，显式数字配置才生效。

## 模块职责

| 模块 | 职责 |
|---|---|
| `agent_loop.py` | provider调用、核心协议注入、反馈边界、FIFO生命周期、运行限额 |
| `prompts.py` | 分开维护框架协议与推荐工程指引，协议按真实工具集合生成 |
| `parser.py / text_filter.py` | 完整命令解析、显示过滤、围栏保护 |
| `executor.py / security.py` | 文件动作、shell与原生Python代码块；共用进程输出、取消及权限策略 |
| `tool_registry.py / interfaces.py` | 工具schema、分流/风险、自定义接口 |
| `context.py / feedback.py / compaction.py` | 可审计回执、observation 合并、去重与确定性摘录；先压缩再按最终历史判断重复 |
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

### 桌面交互状态

v0.7 在桌面层增加最近工作区、会话标题/置顶/归档与本机草稿。主进程 `navigation.json` 只保存通过目录选择器登记的工作区和上次会话；renderer 的最近目录入口必须匹配该列表。启动恢复上下文，不启动模型请求。归档只是元数据，不删除快照或意图日志。

用户任务队列与工具FIFO是两条不同队列。v0.8任务队列由Python服务持有，提交以session/command_id和原始输入指纹标识，持久接收后才ACK，查询回执解决丢ACK。运行中的任务不重放，恢复的待发任务暂停；失败/取消暂停后续。renderer只保存草稿与待确认候选，旧版队列人工迁移。

服务投影带进程stream_id、递增seq和稳定消息id；UI拒绝旧投影、遇事件断档取快照。依然单运行实例，切换会话/工作区受运行状态约束。

文件工具有SHA256读版本前置条件；完整未脱敏读取或冻结附件才能授予基线，自身写入后更新。ChangeStore将意图及文本快照存于会话专属用户数据目录，diff/回退主动读取；UI回退也进入执行意图日志。工具结果unknown立即停止当前模型循环，不能走普通失败自动修复。最终版本检查与文件替换之间仍有外部进程竞争窗口，这不是操作系统事务或绝对CAS。

update_plan将步骤、验收条件和工具回执引用存入agent快照。完成标记需要真实成功回执，但不会把回执等同语义验收。DeepSeek请求保留必要reasoning_content，界面只发活动信号，不广播内部推理正文。

草稿及待发文本存在当前用户的本机应用数据中，不是加密保险箱；API 密钥仍只能通过原有系统加密设置保存。界面复用已完成消息节点；查看旧消息时保持阅读位置，回到底部由用户控制。

## 可观测性与评估

工具事件区分开始、完成、成功、失败、跳过与取消。账本展示 path/hash/bytes/flow/risk。用量来自供应商返回；缺少usage时不应据此推断免费调用。节省API调用是「成功的delayed操作若逐条同步调用」的反事实估算，不是对原生批量调用的实测胜率，也不是实际省下的钱。

CLI和桌面默认使用tf-*文本协议；CLI的`--native-tools`、桌面设置的原生工具开关或显式`enable_native_tools=true`启用原生兼容能力。原生模式首批schema包含read/write/edit/bash/python/read_skill/load_tools；搜索、计划、联网和扩展工具从同一registry由load_tools(names)按需开放，会话及快照保留已加载名称。显式native_tools名单优先，disabled_native_tools继续生效。原生多个调用同批提交、一次反馈；其回执仍标记blocking。独立嵌入调用的ProviderConfig保留原生默认，应用入口负责默认策略。

工具协议由AgentLoop在请求前按真实开放工具生成，CLI切换provider无需改写用户提示。推荐工程指引默认启用，可由system_prompt/文件替换或关闭；工作区规则独立按context配置加载。桌面使用相同两字段保存推荐／自定义／关闭。用户提示不会替换框架协议。DeepSeek文本模式显式发送tool_choice=none，原生模式发送auto。返回独立行上的DSML控制标记（含未闭合开头）或空正文stop且无已执行命令时，记录provider_protocol_error并进入原有错误反馈；不把空响应当完成，也不执行DSML。供应商要求的reasoning不删改。提前关闭供应商流可能收不到最终usage，界面标明用量不完整。

2026-11-09 的效率实现：未反馈的命令载荷和执行器输出完整保留；模型请求先完成历史压缩，再按最终有效历史精确去重成功写入正文。DeepSeek携带tools时保留完整reasoning；未携带tools时不把其忽略的reasoning当成正文副本。runtime与待反馈账本合入末尾observation，不另造原生工具后的用户回合。已反馈的本机旧账本按原预算归档，与首次模型反馈分开。

OpenCode Go 请求使用 ThinkFlow 自有 User-Agent 和会话级 x-opencode-session；会话标识随快照恢复，新CLI会话重新分配。Python子进程沿用调用方已有的UTF-8及不写bytecode设置，API密钥仍不在默认传递列表。

`tf-python`正文与原生`python(code)`把多行Python直接通过stdin交给独立解释器，可集中执行数据处理和独立检查。它与bash共用`Executor._run_process`，复用cwd、环境、输出捕获、超时和进程树取消；不生成临时脚本，不经过shell转义。产物修改用受版本管理的文件工具。工具授权与read-only检查仍由AgentLoop原执行链处理。解释器是`THINKFLOW_PYTHON`或PATH中的`python`，每次调用没有跨进程变量状态。

工具失败继续进入原有反馈恢复链。`max_consecutive_failures`默认null，可在配置中显式指定数字；它不再以隐藏的3次阈值截停仍可恢复的工具错误。现有任务轮数/时间预算、取消及不确定副作用停止保持原语义。

验证分三层：解析/调度隔离测试，真实本地HTTP SSE与文件系统集成，实际桌面及独立包流程。真实商业端点、长时负载与多操作系统是单独的验证边界。

## 后续扩展

见 [harness 能力地图](docs/harness-roadmap.md)。优先扩大可验证的能力，避免在可靠性地基尚未验证时叠加自治调度。
