# 实验性续想 agent 及其技术路线已经开源

上一篇文章讲的是“续想”作为技术路线的核心判断：

> 可预测式工具调用不需要打断模型的推理与思考。

本文讲的是“续想 agent（ThinkFlow）”这个实验性实现。

需要先说清楚：当前续想 agent 的实现方式非常粗糙。它不是一个成熟产品，也不是这条技术路线的完整形态。它更像是一个最小可用样机，用来证明：只要模型能按约定输出可解析的工具调用内容，客户端就可以在不打断模型流、不新开一轮 API 调用的情况下，把一部分可预测工具调用执行掉。

项目链接：

* 技术路线文章：正式发布时填写公开链接。
* 开源项目仓库：正式发布时填写公开链接。

## 一句话概括

续想 agent 当前的大概工作方式是：

```text
用户提出任务
-> 大模型判断需要写文件、追加文件、创建目录等可预测工具
-> 大模型在流式输出中写出指定格式的 tf-\* 工具标签
-> 续想的状态机持续扫描模型流
-> 标签完整闭合后，parser 把标签转成 Command
-> Command 进入单 worker FIFO 队列
-> executor 按顺序执行工具
-> 成功结果进入 command ledger，不打断模型
-> 失败结果打断模型，并在下一轮上下文中反馈错误
```

这条链路的关键点是：工具调用内容必须先由模型生成出来。续想不会在模型还没有写出工具内容时替模型执行，也不会猜测模型想写什么。

例如，模型在输出流里写出：

```xml
<tf-write id="1" path="notes/plan.md">
# Plan

...
</tf-write>
```

只有当 `</tf-write>` 出现，工具调用块完整闭合，续想才会把它解析成一条 `Command`，然后放进执行队列。

这就是续想当前最核心的实现路径：**用结构化文本协议承载可预测工具调用，用状态机识别完整命令，用队列执行副作用，用失败反馈恢复推理。**

## 为什么这能不中断模型

传统 tool call 的路径是：

```text
模型生成 tool\_call
-> provider 结束当前模型流
-> 客户端执行工具
-> 客户端把 tool\_result 加入上下文
-> 重新调用模型
```

续想 agent 对可预测工具走另一条路径：

```text
模型仍然在 streaming
-> 客户端从 stream 中识别 tf-write / tf-append / tf-mkdir
-> 客户端执行这些命令
-> 如果成功，只记录，不要求模型立刻停下来读结果
-> 如果失败，才中断并反馈
```

所以续想节省的不是工具执行时间，而是避免“成功的可预测工具调用”制造新的模型回合。

这里仍然有反馈。区别只是反馈路径不同：

* 成功反馈进入运行时记录和 command ledger。
* 失败反馈进入模型上下文，并触发下一轮修复。

## 指定格式：tf-\* 工具标签

续想 agent 当前用的是 XML-like 标签协议。

canonical 形式带 `tf-` 前缀：

```xml
<tf-write id="1" path="src/app.py">
print("hello")
</tf-write>

<tf-append id="2" path="README.md">
追加内容
</tf-append>

<tf-mkdir id="3" path="src/components" />
```

当前 parser 支持的工具类型包括：

* `read`
* `write`
* `append`
* `mkdir`
* `touch`
* `copy`
* `bash`
* `edit`

但它们的语义并不一样。`write`、`append`、`mkdir`、`touch`、`copy`、部分 `edit` 属于可预测副作用工具，适合流式执行；`read`、`bash` 等信息型或高风险工具仍然可能阻塞。

默认运行时要求 canonical `tf-` 前缀。旧式 `<write>` 标签只作为迁移兼容，不应该作为正式协议。

## Parser：正则入口 + 状态机缓冲

续想的 parser 不是一次性处理完整回答，而是持续吃模型的 streaming delta。

模型输出可能是这样分片到达的：

```text
<tf-write id="1" pa
th="a.txt">hello
</tf-write>
```

所以 parser 不能看到 `<tf-write` 就立刻执行。它必须先把不完整片段放进 buffer，等待：

1. 起始标签完整。
2. 属性完整。
3. 正文完整。
4. 闭合标签出现。

只有完整闭合后，parser 才会产出 `Command` 对象。

当前代码里的关键约束包括：

* 只识别支持列表里的工具名。
* 默认只接受 `tf-` 前缀。
* `id` 必须存在。
* `id` 必须是数字。
* 同一轮不能重复 id。
* `write`、`append`、`mkdir`、`touch`、`copy`、`edit` 必须有 `path`。
* `copy` 必须有 `dest`。
* `bash` 必须有 `cmd`。
* `edit` 必须包含 `<old>` 和 `<new>`。

如果标签不完整、属性缺失、id 重复、格式错误，parser 会记录错误。运行时会把这种错误当成工具失败处理：打断当前推理，并要求模型修正格式后继续。

所以续想不是“看到类似命令的文本就执行”，而是“只有 parser 批准的完整结构化命令才执行”。

## 两条输入流：thinking 与 text

续想当前有两套 parser：

* `parser`：处理 thinking / reasoning stream。
* `text\_parser`：处理普通 text stream 中的命令块。

理想情况下，模型应当在思维链或 reasoning 流里输出工具调用。这样可预测工具调用不会污染用户可见回答。

但不同 provider 的 stream 能力不一致，有些模型不稳定提供 reasoning delta，所以续想也做了 text fallback：如果普通文本里出现合法 `tf-\*` 命令，`text\_parser` 也能识别并执行。

为了避免把命令块原样显示给用户，前端会用安全过滤器把真实执行的命令块从可见文本和 assistant history 中剥离。也就是说，工具标签是执行协议，不是最终回答正文。

这部分实现仍然很粗糙，但它证明了一点：续想的可预测工具调用既可以从 thinking 流里来，也可以从 text fallback 里来；本质上它依赖的是可解析的 streaming delta，而不是 provider 原生 tool\_call。

## Dispatcher：parser 和 executor 的边界

parser 只负责把流式文本变成 `Command`。

executor 只负责执行 `Command`。

中间的边界是 dispatcher，也就是当前代码中的 `\_dispatch\_text\_commands()`。

这个边界很重要：

```text
raw model stream
-> parser
-> Command
-> dispatcher
-> CommandExecutionQueue
-> executor
```

executor 永远不直接读取模型原始文本。它只接受 parser 产出的结构化 `Command` 对象。

这能避免把 Markdown 示例、普通 XML 片段、半截命令或模型闲聊内容当成真实工具调用。

## CommandExecutionQueue：单 worker FIFO 队列

模型输出可能比文件系统执行更快。

如果模型连续输出：

```xml
<tf-mkdir id="1" path="src" />
<tf-write id="2" path="src/app.py">
print("hi")
</tf-write>
```

两个命令可能在很短时间内同时被 parser 识别出来。但执行上不能乱序：必须先创建目录，再写文件。

所以续想使用单 worker FIFO 队列：

```text
enqueue command 1
enqueue command 2
worker executes command 1
worker executes command 2
```

队列规则是：

* 一次只执行一个命令。
* 命令按模型输出顺序执行。
* 如果前一个命令失败，后续已排队命令标记为 `skipped`，不执行副作用。
* turn 结束时，队列会 drain，确保已经入队的命令都有结果。

这保证了“模型推理不中断”和“工具副作用不乱序”可以同时成立。

续想当前不是把工具乱扔到后台并发执行，而是把工具放进一个有事务边界感的顺序队列。

## need\_result：主动要求阻塞

有些工具虽然可以写成 `tf-\*` 标签，但模型可能明确需要结果。

协议里提供了 `need\_result="true"`：

```xml
<tf-read id="4" path="README.md" need\_result="true" />
```

当 dispatcher 遇到 `need\_result=true` 时，会先等待前面队列清空，然后执行这条命令，并把结果作为 `NEED\_RESULT` 中断反馈给模型。

也就是说，续想不是强行让所有 `tf-\*` 都不中断。模型可以通过 `need\_result=true` 声明“我接下来需要这个结果才能继续”。

这也是技术路线里的核心边界：不是工具格式决定是否中断，而是工具语义和模型需求决定是否中断。

## Executor：真正执行副作用

executor 接收 `Command` 后执行实际操作。

当前实现包括：

* `write`：覆盖写入文件，使用临时文件再 `replace`，并自动创建父目录。
* `append`：追加写入。
* `mkdir`：创建目录。
* `touch`：创建空文件或更新时间。
* `copy`：复制文件。
* `edit`：用 `<old>` / `<new>` 做精确替换。
* `read`：读取文件。
* `bash`：执行 shell 命令。

执行前会经过 security policy：

* 相对路径固定到 agent 的 cwd 下。
* 写入、追加、创建、复制、编辑等操作会检查权限。
* 读取敏感内容时会做过滤或拦截。
* shell 命令有更高风险等级。

executor 返回 `ExecutionResult`，里面包含：

* 是否成功。
* 工具名。
* 路径。
* 写入字节数。
* stdout / stderr。
* exit code。
* error。
* status。

这个结果不会自动等同于模型下一步输入。只有失败、need\_result、阻塞工具结果等情况，才会被注入回模型。

## Command Ledger：不靠遗忘节省上下文

续想不中断成功路径，但不能假装工具没有发生过。

所以每条执行过的命令都会进入 command ledger。

ledger 记录：

* id。
* tool。
* path / dest / cmd。
* content / old\_text / new\_text。
* need\_result。
* flow。
* risk。
* content hash。
* output summary。
* status。
* error。
* bytes\_written。
* stdout / stderr / exit\_code。
* injected 状态。

它的作用有两个。

第一，审计。系统能知道模型在什么时候执行过什么副作用。

第二，上下文恢复。下一轮模型调用前，续想可以把未注入的 ledger 记录构造成 `\[THINKFLOW COMMAND LEDGER]`，放回上下文，让模型知道上一轮哪些命令成功、哪些失败、哪些被跳过。

这点很关键：续想不是通过删除工具历史来省 token，而是通过减少不必要 API 回合来省中断。工具历史仍然需要可审计、可恢复。

## 失败恢复

续想当前的失败恢复很直接。

如果 parser 发现格式错误：

```text
未闭合标签
缺少 path
重复 id
缺少 edit 的 old/new
```

当前流会被打断，系统追加一条 parser error 反馈，让模型修正命令格式后继续。

如果 executor 执行失败：

```text
路径不允许
write 内容为空
bash 失败
edit 找不到 old\_text
文件系统异常
```

命令记录会进入 ledger，失败信息会被构造成 `\[THINKFLOW ERROR]`，在下一轮作为用户消息注入给模型。

如果队列中已有后续命令，它们会被标记为 `skipped`，表示这些命令没有产生副作用，模型如果还需要它们，必须重新规划并使用新的 id。

这就是续想当前的“失败时再中断”。

## 传统 tool\_call 仍然保留

续想 agent 并没有删除 provider 原生 tool\_call。

当前系统仍然保留传统工具通道，用来处理：

* read。
* grep / glob / list\_files。
* bash。
* provider native tools。
* 其他信息型或高风险工具。

当模型走原生 tool\_call 时，续想仍按传统方式收集 tool\_use 参数、结束当前流、执行工具、把结果作为 tool message 放回上下文。

所以续想 agent 当前是双通道架构：

```text
可预测副作用工具 -> tf-\* 流式协议 -> parser -> queue -> executor -> ledger
信息型/高风险工具 -> provider 原生 tool\_call -> tool\_result -> 下一轮模型
```

这也是它能作为实验样机存在的原因：它没有试图重写所有工具调用，只把可预测副作用工具这一路拿出来验证。

## 初步工程对比实验

为了避免只停留在概念论证，我把续想 agent 放进了一个可复现的同任务对比实验里。实验目标不是证明某个模型绝对优于另一个模型，而是观察：在同一批 prompt、同一类任务、同一模型供应链下，续想的流式执行路线是否能稳定减少 agentic turn fragmentation。

实验包含两类任务：

1. 前端工程任务：根据同一份需求创建一个 React/Vite 前端项目，并通过独立 validator 检查必要文件、组件数量和构建结果。
2. 长文本写作任务：读取同一份大纲，生成五章小说，并检查章节文件、字数和结构完整性。

实验包含两轮模型：

1. `glm-5.2`
2. `deepseek-v4-flash`

对照对象是 Claude Code 与续想 agent。两者都使用同一个任务 prompt；每一轮结束后由独立 validator 判断是否通过。如果未通过，validator 的失败信息会作为下一轮 prompt 继续发送，直到通过或达到外层回合上限。

需要特别说明：Claude Code 的 JSON usage 在这两轮里把 `cache\_read\_input\_tokens` 和 `cache\_creation\_input\_tokens` 都报告为 `0`。这不应被解释为 Claude Code 后端没有缓存，而只能解释为当前链路没有暴露可比较的 provider 侧缓存读数。因此表格里把 Claude Code 的 cache 字段视为“reported 0 / not comparable”，不把它用于证明缓存命中差异。

### GLM-5.2 轮

|任务|Agent|校验|外层轮数|API calls|input tokens|output tokens|total tokens|reported cache read|用时|
|-|-:|-:|-:|-:|-:|-:|-:|-:|-:|
|前端工程|Claude Code|通过|2|34|2,631,316|32,133|2,663,449|reported 0 / not comparable|871.893s|
|前端工程|续想 agent|通过|1|25|727,273|30,401|757,674|462,166|842.187s|
|五章小说|Claude Code|通过|2|20|510,228|15,986|526,214|reported 0 / not comparable|2671.297s|
|五章小说|续想 agent|通过|1|11|187,158|23,561|210,719|152,128|360.022s|

在 `glm-5.2` 轮里，四个 cell 都通过校验。前端任务中，续想 agent 的 total token 相比 Claude Code 减少约 71.55%，API calls 减少约 26.47%。五章小说任务中，续想 agent 的 total token 减少约 59.95%，API calls 减少 45.00%，用时减少约 86.52%。

前端产物截图：

!\[GLM-5.2 / Claude Code 前端产物](assets/agent-benchmark-20260705/glm-claude-code-frontend.png)

!\[GLM-5.2 / 续想 agent 前端产物](assets/agent-benchmark-20260705/glm-thinkflow-frontend.png)

### DeepSeek v4 Flash 轮

|任务|Agent|校验|外层轮数|API calls|input tokens|output tokens|total tokens|reported cache read|用时|
|-|-:|-:|-:|-:|-:|-:|-:|-:|-:|
|前端工程|Claude Code|未通过|5|30|629,712|9,471|639,183|reported 0 / not comparable|361.006s|
|前端工程|续想 agent|通过|1|22|381,253|42,663|423,916|348,928|392.639s|
|五章小说|Claude Code|通过|1|13|265,585|9,389|274,974|reported 0 / not comparable|841.005s|
|五章小说|续想 agent|通过|1|6|95,240|10,336|105,576|63,232|180.043s|

在 `deepseek-v4-flash` 轮里，前端任务的 Claude Code cell 未通过校验：项目启动服务可以起来，但 `index.html` 引用了 `/src/main.tsx`，实际产物中缺少 `src/main.tsx`、`src/App.tsx`、`src/utils/metrics.ts`、`src/styles.css`、`README.md` 等核心文件。因此这一项不能作为“双方都成功”的效率对比，只能作为失败样本记录。

同一轮中，续想 agent 的前端任务通过校验。五章小说任务里双方都通过校验，续想 agent 相比 Claude Code：API calls 减少约 53.85%，total tokens 减少约 61.61%，用时减少约 78.59%。

这里还需要解释一个容易误读的数字：`deepseek-v4-flash` 轮里，续想 agent 的五章小说任务显示为 6 次 API calls。这个数字不是五个章节写入分别打断了模型。实际执行记录是：

```text
turn 1: provider 原生工具调用 pwd / list\_files，用来确认当前目录。
turn 2: provider 原生 read，读取 initial\_prompt.md 中的大纲与任务约束。
turn 3: 一次模型流里完成 README.md 与 chapter-01.md 到 chapter-05.md 的 6 个流式 write；随后模型主动调用 bash 做校验。
turn 4: bash 校验命令输出有问题，模型继续换方式校验。
turn 5: 再次 bash 统计非空白字符数。
turn 6: 生成最终完成报告。
```

因此，从理论最小路径看，这类小说任务可以接近 2 次 API：启动任务一次、读取大纲一次，然后在同一条流里写完所有章节。但本轮实际运行中，模型主动选择了多次传统阻塞验证命令，续想保留了这些信息型/高风险工具的阻塞语义，所以最终记为 6 次 API。这个结果反而说明：续想节省的是可预测写入工具造成的中断，不会强行把 read / bash 这类信息型或高风险工具也旁路掉。

前端产物截图：

!\[DeepSeek v4 Flash / Claude Code 前端失败样本](assets/agent-benchmark-20260705/flash-claude-code-frontend.png)

!\[DeepSeek v4 Flash / 续想 agent 前端产物](assets/agent-benchmark-20260705/flash-thinkflow-frontend.png)

### 实验材料

实验材料放在项目目录：

```text
bench/agent\_comparison\_20260704/
```

关键文件包括：

* `clean\_prompts.py`：统一 prompt 来源。
* `run\_benchmark\_normal.py`：同会话、多轮 validator 驱动的实验 runner。
* `reports\_normal\_app/summary.md`：`glm-5.2` 轮摘要。
* `reports\_normal\_app/technical\_report.md`：`glm-5.2` 轮技术报告。
* `reports\_deepseek\_v4\_flash/summary.md`：`deepseek-v4-flash` 轮摘要。
* `reports\_deepseek\_v4\_flash/technical\_report.md`：`deepseek-v4-flash` 轮技术报告。
* `runs\_normal\_app/`：`glm-5.2` 轮四个任务产物。
* `runs\_deepseek\_v4\_flash/`：`deepseek-v4-flash` 轮四个任务产物。

这组实验的意义不在于给出最终 benchmark 结论，而在于已经把续想从“可以解释的想法”推进到了“可以跑、可以复刻、可以失败、可以检查产物”的工程样机层面。

从工程化角度看，续想 agent 当前已经具备以下能力：

* 能在真实 OpenAI-compatible 模型流上执行任务。
* 能把可预测工具调用从模型输出流中解析出来。
* 能按 FIFO 队列顺序执行副作用工具。
* 能保留 command ledger，使执行历史可审计、可恢复。
* 能在失败时中断并把错误反馈给模型。
* 能用同一组 prompt 与传统 agent 做控制变量对比。
* 能输出可复现产物、metrics 与技术报告。

因此，我认为续想 agent 已经不只是概念 demo，而是进入了工程化级别能力的早期阶段：它还不成熟，但已经具备被复测、被审查、被扩展的基本工程形态。

## 当前实现为什么说粗糙

当前续想 agent 只是证明技术路线可行，还远远不是理想形态。

主要粗糙点包括：

* 工具协议还是 XML-like 标签和正则/状态机解析，鲁棒性有限。
* 模型需要遵守 prompt 约定输出 `tf-\*`，不是 provider 原生强约束。
* text fallback 容易带来渲染、过滤、可见内容边界问题。
* UI 对流式工具执行状态的展示仍然不成熟。
* 安全策略只是基础工作区约束，离产品级沙箱还很远。
* ledger 注入和上下文压缩还比较直接，没有做到最优 cache layout。
* benchmark 已经有初步复刻脚本和两轮报告，但样本量仍然很小，还需要更多模型、更多任务类型、更多失败案例分类。

但这些粗糙不影响它作为技术样机的价值。

因为它已经跑通了最小闭环：

```text
任务需求
-> 模型生成可预测工具调用
-> 正则/状态机识别完整命令
-> 命令入队
-> 顺序执行
-> 成功不打断
-> 失败再反馈
-> ledger 恢复上下文
```

## 开源的意义

续想 agent 开源的意义不是宣布“这就是最终答案”。

它更像是在给出一个可复刻的实验问题：

> 如果大模型天然知道自己写入可预测工具的内容，那么这个成功路径是否真的需要一次新的 API 回合？

当前实现给出的回答是：不一定需要。

只要工具调用内容能被流式解析，只要副作用能按顺序执行，只要失败能中断恢复，只要执行历史能进入 ledger，那么可预测工具调用就可以在不打断模型推理的情况下完成。

如果有能力、有想法的团队沿着这条路线继续做，可以把现在粗糙的标签协议换成更强的结构化流协议，把基础 security policy 换成真正沙箱，把简单 ledger 换成更好的上下文记忆系统，把 TUI 反馈做成产品级体验。

续想 agent 当前只是这条路线的一个早期证明，证明了“可预测式工具调用不必打断模型推理与思考”或许是一个有价值的研究方向。

作者：沐雪清泽
