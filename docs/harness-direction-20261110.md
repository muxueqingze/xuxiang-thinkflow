# 成熟 harness 参照与下一阶段方向

2026-11-10。以Pi为主，OpenCode和Anthropic官方工程资料交叉参照。本轮交付调研与方案，不修改执行链，也不发付费模型请求。

## 判断

下一阶段应建立“原始证据完整可回取、模型工作集按当前任务选择”的上下文机制。沿用ThinkFlow的连续可预测执行与独立信息合批，把精力放在减少丢信息后的重跑、重复传输和协议恢复。最小信息量是提供足够决策的内容，并保留取回细节的路径。

压缩不能只优化摘要长度。语义摘要多用一次API，有时可能省掉后续多次重建上下文；是否值得，要比较整项任务的总请求、质量和用量。默认先完善确定性整理，语义摘要作为后续对照选项。

## 核对版本与证据

| 对象 | 固定范围 | 本轮依据 |
|---|---|---|
| ThinkFlow | `941b90a` | 当前源码、已有真实模型记录、两项本地组件探针 |
| Pi | 本机1.0.1 / tag `a7229ddc`；另看上游`6273031b` | 本机包实际运行，上游固定提交源码。两者分别记录，不把main的新能力写进旧基准 |
| OpenCode | `c554c31c`，当前主体为`packages/core` | 固定提交源码；本轮未运行该版本 |
| Claude Code / Claude API | 2026-11-10读取的官方文档 | 工程方法和接口契约，不据此推断闭源实现细节或性能排名 |

历史61.11%仍对应ThinkFlow `40dfa30`与Pi 1.0.1的三题对照，自动压缩关闭；本次源码调研和局部探针不更新该数值。[历史计量](efficiency-results-20261110.md)

## 成熟实现怎么做

### Pi：事实记录与模型上下文分开

- 会话记录追加保存，压缩追加摘要及`firstKeptEntryId`。模型上下文由摘要和保留尾段投影；当前上游还支持追加`context_edit`改变投影，保留原始记录。这个接口不等于默认会自动删除重复结果。[session-manager.ts](https://github.com/earendil-works/pi/blob/6273031b012ab3c017cdbf593a62b7e6792186a8/packages/coding-agent/src/core/session-manager.ts#L476)
- 压缩触发依据模型上下文窗口、最近provider usage和新增消息估算；默认预留16,384 token，近期保留20,000 token。摘要单独调用模型，切开长回合时可能分别总结旧历史和当前回合前缀，产生两次摘要调用。[估算与触发](https://github.com/earendil-works/pi/blob/6273031b012ab3c017cdbf593a62b7e6792186a8/packages/coding-agent/src/core/compaction/compaction.ts#L193)、[摘要路径](https://github.com/earendil-works/pi/blob/6273031b012ab3c017cdbf593a62b7e6792186a8/packages/coding-agent/src/core/compaction/compaction.ts#L952)
- Shell输出默认展示末尾2,000行/50KiB，超过显示预算时完整字节写入文件；失败、取消和超时路径也先结算已收到的输出。完整日志目前放系统临时目录，不能由此推导跨恢复的持久保证。[输出累积器1.0.1](https://github.com/earendil-works/pi/blob/a7229ddc21810d6245105978033b7df645ecc2f7/packages/coding-agent/src/core/tools/output-accumulator.ts)、[当前shell反馈](https://github.com/earendil-works/pi/blob/6273031b012ab3c017cdbf593a62b7e6792186a8/packages/coding-agent/src/core/tools/bash.ts#L350)、[当前文件位置](https://github.com/earendil-works/pi/blob/6273031b012ab3c017cdbf593a62b7e6792186a8/packages/coding-agent/src/utils/output-files.ts#L17)
- Read提供从1开始的行offset和limit，返回明确的范围及继续位置。当前上游按流选择行，限制内存；已有1.0.1也具备范围读取。[read.ts](https://github.com/earendil-works/pi/blob/6273031b012ab3c017cdbf593a62b7e6792186a8/packages/coding-agent/src/core/tools/read.ts#L14)
- 同一响应的多个工具结果集中后再进入下一轮；执行器支持顺序与并发。并发缩短本机等待，模型是否合批决定能否少一次请求。[agent-loop.ts](https://github.com/earendil-works/pi/blob/6273031b012ab3c017cdbf593a62b7e6792186a8/packages/agent/src/agent-loop.ts#L268)

### OpenCode：输出与压缩有明确预算，也有引用寿命取舍

- 通用工具超出2,000行/50KiB后，将完整输出落盘并返回路径；按mtime清理7天前文件。Shell单独持续落盘，预览取尾部，而且每个服务只保留最近25个已退出进程，超过后会删除最老的输出文件。[通用输出](https://github.com/anomalyco/opencode/blob/c554c31cbea16d3059d63e848f897f49a991dcd8/packages/core/src/tool-output.ts#L64)、[shell删除路径](https://github.com/anomalyco/opencode/blob/c554c31cbea16d3059d63e848f897f49a991dcd8/packages/core/src/shell.ts#L170)、[25条上限](https://github.com/anomalyco/opencode/blob/c554c31cbea16d3059d63e848f897f49a991dcd8/packages/core/src/shell.ts#L469)
- Read支持行范围，Grep返回位置；工具说明明确鼓励合理大小的一次读取，避免许多微小切片。[read.ts](https://github.com/anomalyco/opencode/blob/c554c31cbea16d3059d63e848f897f49a991dcd8/packages/core/src/tool/plugin/read.ts#L18)
- 当前压缩默认保留约15,000文本token，按原消息ID重建近期内容，fork会重映射ID；摘要使用额外模型请求。近期序列化的工具输出还会截为前1,250码点，因此“近期原文”不代表所有工具结果完整。[压缩](https://github.com/anomalyco/opencode/blob/c554c31cbea16d3059d63e848f897f49a991dcd8/packages/core/src/session/compaction.ts#L269)、[近期文本](https://github.com/anomalyco/opencode/blob/c554c31cbea16d3059d63e848f897f49a991dcd8/packages/core/src/session/recent-text.ts#L6)、[fork映射](https://github.com/anomalyco/opencode/blob/c554c31cbea16d3059d63e848f897f49a991dcd8/packages/core/src/session/projector.ts#L223)

这些设计支持“保存事实、按需投影”，但其临时文件和定额清理不能直接作为ThinkFlow会话恢复的原文保证。旧版OpenCode的40k保护、固定两轮尾段等参数也不能用于描述这个提交。

### Anthropic：按需取用与信息合批

官方工程文章提倡用路径等轻量引用按需载入信息，同时指出探索本身也有往返成本；当前Claude Code文档建议常驻规则只保留代码中无法直接发现的知识，长流程按需读skill。Claude API允许一轮声明多个工具，一次返回全部结果，顺序执行还是并发由harness决定。这与我们的最小信息量、短入口和信息合批一致。[上下文工程](https://www.anthropic.com/research/effective-context-engineering-for-ai-agents)、[短规则与按需skill](https://code.claude.com/docs/en/best-practices#keep-claudemd-short)、[多工具结果契约](https://platform.claude.com/docs/en/agents-and-tools/tool-use/parallel-tool-use)

## 本地行为探针

实际执行本机Pi 1.0.1的OutputAccumulator/Read及ThinkFlow对应组件，未调用模型。两端收到相同UTF-8字节；首次夹具的Windows换行转换已纠正并在证据中记录。

| 场景 | Pi 1.0.1 | ThinkFlow 941b90a | 可以确认的结果 |
|---|---|---|---|
| 90,026字符输出，长行后附末尾诊断 | 显示包含诊断；完整90,030字节原样落盘 | 保留前80,000字符，末尾诊断消失 | 输出捕获及回取确有质量缺口 |
| 已知目标在3,000行文件第2,500行，取2,498起6行 | 返回307字符，包含目标和下一offset | 返回128,993字符全文，也包含目标 | 范围读取能减少本次无关输入 |

第二行只比较已知范围的读取正文；定位范围的搜索、后续编辑和模型请求均未计入。第一行不把累积器的预览字符数当作完整工具回执用量。原始数据见[探针证据](evidence/harness-reference-20261110.json)。

## 建议实施顺序

### 1. 完整输出与模型反馈分层

输入、工具结果和会话快照继续沿用现有入口。短结果原样反馈；超长输出在首次接收时保存完整原文，模型获得有效预览、实际状态、输出长度和可回取引用。日志优先保留尾部，文件内容按请求范围读取，中部信息可定位回取；显示预算由任务验证选择，不直接复制Pi的数值。通过现有recall扩展到回执，不另造一套检索语言。失败时保留末尾诊断，取消/超时也保存已收到内容。

归属由实际SessionStore决定，恢复与fork保持引用可用；仍被会话引用的材料不能被临时清理误删。无需保留的实验副本每轮回收，正式证据与会话原文按各自生命周期管理。不要照搬Pi的OS临时路径或OpenCode的25条删除策略。

文件边界：`src/executor.py`接收原文；`src/context.py`保存回执引用并生成反馈；`src/session.py`与`src/desktop_service.py`管理持久归属；`src/conversation.py`/AgentLoop复用recall入口。先交付这个完整单元，再改变旧输出的上下文投影。

### 2. 精确读取与编辑契约一起设计

Read补行范围、总范围和继续位置，独立读取沿用现有合批。读取粒度以完整函数、配置段或诊断段为单位，由模型选择，避免把所有文件强制切成小页。

ThinkFlow当前仅完整读取才授予修改基线（`src/executor.py:177`、`:378`）。若只增加分页，局部读取之后可能仍需额外全文读取。实施时区分探索读取、精确edit和整文件覆盖：局部edit是否可凭已观察片段及文件版本执行，需要作为同一设计问题验证；整文件覆盖继续按现有完整读取契约。验收按“定位→读取→修改→检查”的完整请求数判断收益。

### 3. 统一上下文投影与实际预算

沿用已有ConversationContext和回执，不新增平行记忆系统。原文存储负责可恢复，投影负责本次要发什么。先把精确去重覆盖到文本及两种原生路径：原文仍在有效输入、完整内容相同时返回引用；来源被压缩移出时重新提供所需正文。既保留事实，也避免为解引用再多发请求。

压缩触发从固定消息数/字符数演进为模型窗口、已报usage与新增内容估算，尾段围绕完整工具批次和正在处理的任务保留。模型窗口只是上限；工作集的目标预算应通过整任务实测选择，不能照搬“接近窗口满时才整理”。框架不机械丢弃旧错误、决策或“看起来过时”的结果；精确事实之外的语义取舍需要真实任务验证。新增小回执不应引起每轮重新改写整个历史前缀。

Provider usage对应当时发送的上下文视图；压缩或投影变化后重新估算当前视图，不能继续沿用旧usage加增量。Pi对这点有明确实现，可直接借鉴其估算边界。[投影与usage有效性](https://github.com/earendil-works/pi/blob/6273031b012ab3c017cdbf593a62b7e6792186a8/packages/coding-agent/src/core/compaction/compaction.ts#L226)

复用已有计划、文件版本及成功回执提供简短工作状态。工程思想保持“先找再写、事实可回取、独立信息合批、已知动作连续做”，放进实际工具能力与少量指引，不扩写常驻法条。

### 4. 用完整长任务选择压缩策略

原始要求9/9的恢复探针已证明保真收益，但输入增加、请求一组下降一组上升。下一次选择依靠真实跨阶段任务：代码定位、多个文件修改、用户提出不重复的新约束、至少两次压缩、恢复/分支后继续交付。按文件量和执行轨迹自然产生长历史，不以填充消息冒充长任务。

先比较当前确定性方案与上述1—3项；语义摘要作为独立可选方案，与Pi的额外摘要调用一起计入总请求。模型能力、必要思考、任务和工程验收保持一致。主要判断是正确交付、压缩后返工/重复读取、总API请求；其次看输入/缓存输入/输出、墙钟时间和原文存储成本。失败、摘要、协议恢复、缺失usage全部记账，不能拿质量漏项的便宜产物作为胜出基线。

## 暂不作为默认改动

- 全部工具并行：同一信息批次已可只用一轮模型请求；并行是否有益主要看本机等待，不能破坏连续写入与后续检查的顺序。
- 自动删除老结果或极小分页：可能制造回取和重建上下文的额外轮次，先保留可恢复原文，再按完整任务测量。
- 大段工程指引、强制每步自测、额外审查/摘要agent：这些都会引入注意力或调用成本，现有试验未证明扩展指引稳定改善质量。[工程质量记录](engineering-quality-20261110.md)
- 仅凭“成熟harness也这样做”下性能结论：本轮确认实现机制和两项局部行为；完整长任务结果是下一阶段的取舍依据。

本轮只保留本文与探针JSON；两轮夹具、Pi生成日志均在独立目录核对后回收，无本轮后台进程或新增依赖。

独立方案复核已核对三条固定提交源码、本机Pi版本及ThinkFlow相关实现，通过本次方向评审；提出的usage视图有效性已纳入上文。该复核未重跑组件探针，也未运行OpenCode或完整长任务。
