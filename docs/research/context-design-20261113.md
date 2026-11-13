# 阶段工作状态：参考实现与最小方案

2026-11-13。实现前定向研究，基线 `38a08079d76777f1742dd1ee53f5d4307d94c906`；方案确定后增加文末的最小实现与离线验证。没有调用付费模型，也没有据此更新效率成绩。沿用 [11月10日方向](../harness-direction-20261110.md)。

## 推荐实施

给现有 `task_plan` 增加可选 `context`，保存当前阶段要点、仍有效的决定和少量关键文件导航；通过现有 `update_plan` 的流式入口更新。`steps/status/acceptance/evidence/explanation` 继续承担进度、下一步、阻塞和完成依据。无需新的记忆工具、另一份计划或默认摘要请求。

工作状态由正在执行任务的模型在已有生成中写出，适合换阶段、关键决定变化或接续复杂工作时更新；不是每次工具之后写笔记。确定性压缩仍负责旧消息摘录及近期原文，运行状态负责跨压缩保留需要持续使用的语义信息。模型正在处理的原始用户要求继续完整保留。

这是一项有直接实现依据的设计选择，是否减少整任务请求还需真实长任务对照；本轮只确认接线和可判定验收场景。

## 固定上游证据

本轮通过 GitHub connector 查询 Pi 默认分支，再固定到 `275dde410065080d291de45fbe2110272d10de23`（提交时间 2026-11-13 04:53:16 UTC）。读取的是当前源码，不把它当作历史 Pi 1.0.1 基准。

| 问题 | Pi 当前实现 | 对 ThinkFlow 的启发 |
|---|---|---|
| 阶段间保留什么 | 摘要有目标、约束、完成/进行中/阻塞、关键决定及理由、下一步、关键上下文；要求保留准确路径、函数名和错误信息 | 现有计划已覆盖大半，只补决定和工作上下文，不复制整份摘要模板 |
| 多次压缩怎样接续 | 新消息与上一摘要一起生成更新摘要，更新进度及下一步，并允许移除不再相关的信息 | 当前有效状态应替换旧状态；历史摘录不承担当前状态的唯一来源 |
| 文件列表怎样保留 | `readFiles/modifiedFiles` 存在 compaction details；从上一详情和新消息累积，独立于自然语言摘要，再附到正文 | 事实索引与语义总结分开；ThinkFlow 已有回执和文件版本，不另维护全量文件账本 |
| 原文和模型视图 | 会话条目追加保存；`firstKeptEntryId` 选保留尾段；投影产生摘要和尾段；`context_edit` 只改变被发送的内容贡献 | 原始要求、冻结输入和工具输出继续使用现有保存层，状态只是本次发送视图的一部分 |
| 摘要请求成本 | 常规压缩单独请求模型；切开一个长回合时，旧历史和当前回合前缀可能各请求一次，usage 合并记录 | 不能把“结构化摘要”误写成没有 API 成本；本项目默认用正常流式输出补状态 |

固定源码：

- [摘要字段及更新规则：compaction.ts:507](https://github.com/earendil-works/pi/blob/275dde410065080d291de45fbe2110272d10de23/packages/coding-agent/src/core/compaction/compaction.ts#L507)
- [独立摘要请求：compaction.ts:618](https://github.com/earendil-works/pi/blob/275dde410065080d291de45fbe2110272d10de23/packages/coding-agent/src/core/compaction/compaction.ts#L618)；[一次或两次摘要与 usage：952](https://github.com/earendil-works/pi/blob/275dde410065080d291de45fbe2110272d10de23/packages/coding-agent/src/core/compaction/compaction.ts#L952)
- [文件详情累积：compaction.ts:52](https://github.com/earendil-works/pi/blob/275dde410065080d291de45fbe2110272d10de23/packages/coding-agent/src/core/compaction/compaction.ts#L52)；[文件列表生成：utils.ts:31](https://github.com/earendil-works/pi/blob/275dde410065080d291de45fbe2110272d10de23/packages/coding-agent/src/core/compaction/utils.ts#L31)
- [按压缩点选择上下文：session-manager.ts:476](https://github.com/earendil-works/pi/blob/275dde410065080d291de45fbe2110272d10de23/packages/coding-agent/src/core/session-manager.ts#L476)；[保留来源的投影：544](https://github.com/earendil-works/pi/blob/275dde410065080d291de45fbe2110272d10de23/packages/coding-agent/src/core/session-manager.ts#L544)；[追加压缩条目：1278](https://github.com/earendil-works/pi/blob/275dde410065080d291de45fbe2110272d10de23/packages/coding-agent/src/core/session-manager.ts#L1278)

具体差异：Pi 的文件提取检查 assistant toolCall 和 nestedCalls 的路径，没有以成功回执筛选，所以其 `modifiedFiles` 是操作路径索引，不能直接等同于成功写入事实。ThinkFlow 的已修改、已读基线和完成证明继续依据执行器及 receipt；模型提供的 `context.files` 只是导航，不授予编辑权限。

另一个官方工程参照是 Anthropic 2025-09-29 的 [Effective context engineering for AI agents](https://www.anthropic.com/research/effective-context-engineering-for-ai-agents)。其压缩保留架构决定、未解决问题和实现细节；轻量路径引用支持按需读取，结构化笔记适合明确阶段的任务。这支持保留简短工作状态与回取入口，不要求 ThinkFlow 采用独立笔记工具、每步记忆或额外 agent。该文章是工程方法依据，不是今天闭源产品行为或性能的实测。

## 本项目已有机制与真正缺口

基线已确认：

- `ConversationContext` 保存原始请求、冻结输入、请求位置和结构化摘要条目；`recall(request:N)` 与 `request:N:input` 可取回相应原文。
- `compact_messages` 保留当前请求和近期完整消息；旧消息做头尾摘录，摘要条目受总预算约束。任意旧 assistant 决定的原文并没有统一的 recall 地址。
- `task_plan` 每次请求随 `[CURRENT TASK PLAN]` 注入；它在 `AgentLoop.to_snapshot/load_snapshot` 保存恢复。计划的成功回执受 `compact_history(protected_ids=...)` 保护。
- `SessionStore._on_event` 遇到 `plan_updated` 保存快照；`DesktopService._on_agent_event` 同样保存，并发送更新后的 state。
- 工具输出有独立输出存储及 `receipt:N:字段` 回取；冻结输入和工具输出不应再复制进工作状态。

缺口是：计划标题与完成回执能说明做到了哪一步，却不稳定表达“为什么采用这个办法”“哪个文件承担哪项职责”“哪些失败结论仍影响继续工作”。把这些信息仅写在长 assistant 正文中，头尾摘录可能裁掉中段，后续再压缩也可能淘汰整个条目。

## 字段与更新契约

字段形状如下；步骤与context组合用于同一次计划更新：

```json
{
  "explanation": "数据加载完成，继续接入展示。",
  "steps": [
    {
      "id": "view",
      "title": "接入列表展示",
      "acceptance": "搜索和空结果均按现有接口展示",
      "status": "in_progress",
      "evidence": []
    }
  ],
  "context": {
    "summary": "加载层已统一返回 records；下一阶段从列表组件接入。",
    "decisions": [
      "继续使用既有本地索引，因为服务端没有查询接口；不新增远端服务。"
    ],
    "files": [
      {"path": "src/store.py", "note": "数据入口，返回约定见 load_records"},
      {"path": "src/list.py", "note": "待接入的列表组件"}
    ]
  }
}
```

`context.summary` 是阶段接续所需的短文本；`decisions` 只保留当前有效决定及理由；`files` 只选继续工作需要定位的路径和作用。不另增 objective/progress/next_steps/blocked/evidence 字段，它们已经在请求、计划和回执中。

更新语义：

1. 旧调用省略 `context`：更新计划步骤，保留已有 context。旧快照没有 context：照常加载，不生成默认笔记。
2. 显式提供 context：整体替换该对象，移除过期决定或路径不会被旧状态合并回来。传空对象明确清空；新会话从空计划开始。
3. 文件、决定和总结都是模型提交的工作状态，不把其内容自动升级成执行事实。已有完成项 evidence 契约保持原样。
4. 只做现有 schema 风格的类型/体积约束；不增加文件存在性探测、语义审查器、自动补全、兜底摘要或新执行门槛。提示可用一句：“换阶段或关键决定变化时，随计划更新必要工作上下文。”

### 接线表

| 位置 | 最小改动 |
|---|---|
| `src/task_plan.py` | PLAN_SCHEMA 增加可选 context；validate_plan 接受该字段，按 previous 处理省略与显式替换。当前严格 `set(value)` 判断需一起改；step 的既有证据校验不变 |
| `AgentLoop._update_plan_tool` | 继续统一验证、赋值和发 plan_updated；tf 流式与原生入口复用同一逻辑，不能两套状态语义 |
| `build_runtime_status_message` | 发送当前 task_plan 一份即可；如果支持没有步骤的 context，现有 `if self.task_plan['steps']` 条件需覆盖 context 非空的情形 |
| `to_snapshot/load_snapshot` | 复用 task_plan 字段和 validate_plan；恢复后深拷贝对象沿现有路径，分支更新不修改来源会话 |
| SessionStore / DesktopService | 沿用 plan_updated 保存时点；主要核查事件先后与三端恢复，不增第二份状态文件 |
| `compact` / `ConversationContext` | 不复制 context 到 summary_entries 或 summary，不以压缩重写当前工作状态；原始请求与冻结输入不变。无需新增 ConversationContext 最新状态字段 |
| `recall` | 仍以 request 和 receipt 原文为依据；本次当前 context 已每次发送，无须为它另发一次 recall。files.note 中需要可回取依据时可以明确写已有 request/receipt 引用 |

旧的模型工作状态在被新状态替换之后，当前方案不提供专用历史状态 recall；不能声称保存了完整 assistant 推理历史。若真实任务证明确需取回旧阶段决定，再沿 ConversationContext 加阶段快照及 `phase:N` 回取，不先做数据库、向量检索、时间线 UI 或全会话事件树。本轮验收目标是当前有效状态跨压缩/恢复持续可用，原始要求与执行证据继续可回取。

现有按消息摘录可能包含过去的计划更新文本，属于历史记录；不要额外把整个新 context 写进压缩摘要。是否消除历史中的精确重复，留给已有投影机制按完整相等处理，不按“看起来过时”猜测删除。

## 有用的验收场景

| 场景 | 可判定结果 |
|---|---|
| 决定位于旧长消息中段，正文会被摘录裁掉 | 把该决定随计划保存后，连续两次压缩，真正 provider request 仍含完整当前决定与关键路径 |
| 新用户只修改一项要求 | 新请求保持原文；更新 context 后发出最新决定，旧的同项取舍不被自动合并恢复；旧要求仍能 recall |
| 普通更新只改变步骤状态 | 省略 context 不丢已有状态；显式空对象可以清除；空步骤但有工作状态时也能发送 |
| 失败 write 曾提到某路径 | 该路径可作为导航，但不得进入成功修改事实、授予 read 基线或让完成项通过 evidence 校验 |
| CLI/SDK 和桌面在更新后立即恢复 | 两条保存路径从实际落盘恢复同一计划 context；fork 修改只影响新会话 |
| tf 更新后紧接可预测写入 | 一次流式响应能完成状态提交和后续已知操作；状态成功不制造新 HTTP 请求，实际失败走现有反馈路径 |
| 压缩与协议切换 | 两种原生 provider 与 tf 的真实构建请求得到相同当前状态；压缩前后 request/receipt recall 保持可用 |
| 普通短任务 | 未使用 context 的旧调用/快照行为不变，不自动生成状态请求，不出现新提示长段或强制笔记 |

离线测试可以证明持久化、协议次数和压缩后的内容存在，但不能证明模型会主动写对摘要。真实效果对照应使用跨多个文件的自然长任务：有用户中途改约束、至少两次压缩、恢复后交付；先比较当前实现与可选工作状态，不靠填充消息制造“长任务”。

所有运行保持同模型、任务和必要验收。首先看正确交付和遗漏，其次看总 HTTP 尝试、因上下文丢失产生的重复搜索/读取/返工，再看输入/缓存/输出和时间。语义摘要若另作实验，其一次或两次独立调用及失败重试全部计入；只有整项任务省下的请求超过这些成本、且质量相同时才有收益。

## 不复制的复杂度与本轮产物

不移植 Pi 的会话树/context_edit 扩展体系、不照搬完整七段摘要提示、不加每步笔记规则、不由框架推断语义决定、不把任意工具改成可预测工具，也不要求每次压缩额外模型总结。Pi 文件索引的调用记录语义不能取代 ThinkFlow 成功回执。

官方源码保留固定链接。研究阶段没有运行性能对照；表中流式连续执行由主实现节点接线验证。

## 最小实现与离线验证

方案确定后，仅修改 `src/task_plan.py` 并增加 `tests/test_task_context.py`：context 内三个字段各自可选，提供的对象整体替换；省略整个 context 沿用前值，显式 `{}` 清空，从未使用时保持原计划对象形状。类型与长度约束随 PLAN_SCHEMA 声明，文件不作存在性检查，也不授予读取基线。

执行 `python -m unittest discover -s tests -p test_task_context.py -v`：8项通过；`test_plan_receipts.py`：11项通过。检查覆盖实际连续两次 compact、SessionStore 的 plan_updated 保存、磁盘恢复与分支隔离、真实 DesktopService 的 resume/fork、两种 provider 的原生/文本模式请求正文、旧计划不增加 context 字段，以及仅有文件导航时仍拒绝未经读取的 edit。状态更新和压缩没有发出模型请求。

首次测试夹具没有为 Anthropic 配置必须的 max_tokens；补齐夹具后原失败模式通过，运行代码没有为此增加兜底。测试临时目录使用 `temp/context-research-20261113/`，每项结束即清理；本轮结束该目录不存在，无后台进程或新增依赖。这里的请求正文相等证明旧轨迹未因状态默认值增加内容；没有单独测量真实模型减少了多少请求。
