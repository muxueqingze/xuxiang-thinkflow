# 连续执行与独立信息合批 · 2026-11-10

固定三题各两次，最终ThinkFlow与Pi各六份均通过独立实物交付验收。ThinkFlow用34次请求、178,804总token，Pi用48次请求、至少459,820总token：**请求减少29.17%，总token至少减少61.11%**。六个对应样本的总token均低于Pi；请求五份更少、一份持平。

最终源码为`40dfa30`，流式主路径实现为`60a0087`。本轮保持工程任务、模型思考强度和输出能力，优先减少不提供必要新信息的模型往返。

## 61.11%的账面来源与能力口径

相对Pi已报值，总差额为281,016token：输入少268,211（占差额95.44%），输出少12,805。输入从381,113降至112,902，已报差额70.38%；输出从78,707降至65,902，差额16.27%。减少模型往返、减少每次重复历史和DeepSeek文本模式的历史处理共同作用。上述是输入/输出账面拆分，不将其分配成各个改动的独立因果百分比。

工程执行能力新增了独立信息整批反馈、流中写入后直接检查、多行Python stdin与实际协议错误恢复；产物质量的证据是双方各6份完整交付。61.11%衡量token消耗，不是代码质量或模型能力的评分。

三题的代码结构、错误定位、内存与检查质量另见[最终产物质量对比](reviews/artifact-quality-20261110.md)：核心功能相当，具体工程质量各有取舍，也记录了ThinkFlow产物的多余兼容和弱断言问题。

后续按用户要求将协议与工程方法分层：协议在框架核心默认注入，推荐工程指引可自定义或关闭，桌面提供三种模式。这项配置改动的验证见[提示词分层审查](reviews/prompt-layers-20261110.md)；本页基准继续对应40dfa30，没有重写原计量。

## 查出的主要问题

上轮原生模式三题ThinkFlow为31请求/433,938token，Pi为27/249,511。ThinkFlow没有实际使用流中写入；已知文件操作拆轮，独立读取分别反馈，检查自身的错误又制造返工。较早生成的长历史随后被反复带回，第一轮工具说明不是主要差额来源。

另外，DeepSeek在携带原生tools时会把历史reasoning纳入上下文；不携带tools时忽略历史reasoning。这使主执行通道和上下文成本成为同一设计问题。[DeepSeek官方契约](https://api-docs.deepseek.com/guides/thinking_mode/)

旧案例的大幅省量有真实记录：一次长生成中执行许多可预测操作，避免不断返回模型入口。当前对手Pi本身会合批，必须比较真实请求，不能把每个delayed命令都算成相对Pi省了一次API。逐回合分析、旧案例核对及成熟harness源码依据见[方向评估](efficiency-direction-20261109.md)。

## 实现

- **连续做已知操作。** CLI和桌面默认使用tf-*，已确定的文件修改连续执行，已知检查接在同次生成末尾。原单worker FIFO保证先落盘再检查。
- **信息需求合批。** 已知且相互独立的read/bash/python在一轮集中声明；成功结果按序完整反馈一次。显式need_result/confirm、真实失败和取消保持原边界。批量反馈不需要新增并行执行器或batch语言。[Claude公开批次契约](https://platform.claude.com/docs/en/agents-and-tools/tool-use/parallel-tool-use)也区分了同批声明、执行顺序和一次返回全部结果。
- **最小必要信息。** 按任务读取相关实现、集中检查、只输出参与决策的结果；沿用精确正文去重。首次结果、实际错误和供应商要求的字段保留。没有关闭缓存、压低输出额度或降低思考强度。
- **tf-python。** 多行Python直接经stdin进入原执行器，复用cwd、环境、授权、超时与取消。检查和数据处理可集中完成，产物修改继续用受版本管理的文件工具。
- **有效配置一致。** 原生工具开关保留扩展能力；CLI覆盖、provider切换、桌面IPC与持久保存和实际指引一致。DeepSeek文本模式明确发送tool_choice=none。
- **处理观察到的协议失败。** 空正文stop且无执行、围栏/文件块之外的DSML控制标记进入原错误反馈；未闭合开头同样识别。没有执行DSML、自动改写失败命令或新增验收门禁。

三个显式none单请求回放都得到规范tf-read，但最终工程运行仍出现DSML；none不是格式保证。最终组3次协议恢复与3次parser恢复均计入实际成本。

## 同模型实测

Go端点`https://opencode.ai/inference/go/openai/v1`，模型`deepseek-v4.1-flash`，thinking enabled/high；无客户端输出、模型轮数、任务时长或续写额度，自动压缩关闭。任务版本`2026-09-29.1`，相同公共任务提示，三题各两次。temperature=0被发送，DeepSeek thinking不因此变成确定性采样。

ThinkFlow全部取自`go-stream-final-20261110`（40dfa30）。Pi1.0.1全部取自此前冻结组`go-stream-batch-final-20261110`，保留其原生提示和read/bash/edit/write，没有为当前实现改写Pi行为。后续Pi观察器修正只更新本地终态记录，不影响请求、工具或历史。最终TF全部34条请求实际无tools、tool_choice=none；Pi48条请求均携带四个原生工具。

| Harness | 完整交付 | 请求尝试 | 输入（含缓存） | 缓存输入 | 输出（含推理） | 总token |
|---|---:|---:|---:|---:|---:|---:|
| ThinkFlow 40dfa30 | 6/6 | 34 | 112,902 | 40,704 | 65,902 | 178,804 |
| Pi 1.0.1 | 6/6 | 48 | ≥381,113 | ≥294,016 | ≥78,707 | ≥459,820 |

ThinkFlow缓存输入至少减少86.16%。非缓存输入为72,198，Pi已报87,097；推理token已经包含在输出中，不重复相加。

Pi receipt-r2的第5次请求ConnectError，没有HTTP状态或usage；另47次用量完整。未知请求保留未知，Pi数值作为下限，因此总token下降61.11%是保守下限。连接错误后Pi实际自动恢复，最后message_end/turn_end均为stop且完整交付；旧观察器的last_error残留导致原accepted=false。原始result保持，派生完成状态及依据见[独立验收](reviews/efficiency-final-20261110.md)。

| 任务（每题两次合计） | TF请求 / Pi请求 | TF总token | Pi总token |
|---|---:|---:|---:|
| receipt_package：金额聚合与CSV命令行 | 10 / 17 | 49,680 | ≥143,078 |
| pricing_refactor：跨文件共享定价 | 12 / 15 | 61,133 | 148,768 |
| frame_decoder：增量UTF-8帧解析 | 12 / 16 | 67,991 | 167,974 |

这是完整执行模式的对照，包含生成方式、信息合批、指引及DeepSeek历史reasoning契约的共同作用；没有将全部差额归因于某一项。旧六题生产组和旧前端/小说结果继续保留，各组独立比较。

## 调用为什么仍会增加

最终pricing-r2的一次重复命令编号需要纠正；frame-r1包含两次DSML恢复、两次编号恢复，随后修正“未完整帧应在feed时报错”的错误自测期望。receipt额外检查也出现过把A/a错误合并的问题，按契约纠正后完整复验。它们都保留在最终请求和token里。

独立审查核对了每一份最终文件、最后修改后的公开与附加检查及真实收尾；发现自测期望错误时按公开契约核对，没有只看exit 0或隐藏评分。全部产物各8/8。frame-r2一次生成实际输出22,329 token，之后输入6,420；没有用截短生成取得低数字。最终六份均有流中落盘，独立信息合批也真实发生。

## 探索、计量与复核材料

- [comparison.json](evidence/efficiency-20261110/comparison.json)：12份最终比较、逐请求原始usage数字、未知请求、配置/源码指纹、工具批次与原件hash。
- [workspaces.json](evidence/efficiency-20261110/workspaces.json)：54份最终README/Python文件，保留UTF-8字节、换行与hash，作为惰性数据公开；不含模型思考或会话profile。
- [exploration.json](evidence/efficiency-20261110/exploration.json)：18次探索任务和6次单请求回放。包括指引仍全native、text协议未收尾、缺导入、空响应与未闭合DSML等失败；没有用低值替换最终TF六份。
- 本阶段30次任务运行加6次单请求回放，共212次请求尝试，已报至少1,547,522token，1次usage未知。最终比较与探索互不重复记账；[上阶段28次记录](efficiency-results-20261109.md)单独保留。
- [源码定向审查](reviews/efficiency-direction-20261109.md)与[最终12份实物/导出审查](reviews/efficiency-final-20261110.md)分别记录实现和交付依据。

完整节点检查：parser19、core82、242项unittest通过，见[完整日志](evidence/efficiency-20261109/protocol-recovery-full.txt)。最后协议修正后仅复验直接相关的[23项runtime](evidence/efficiency-20261110/protocol-runtime.txt)、[14项provider wire](evidence/efficiency-20261110/provider-wire.txt)、[12项meter](evidence/efficiency-20261110/meter-metadata.txt)，均通过。桌面14项及配置往返独立通过；Pi观察器的真实事件回放见[记录](evidence/efficiency-20261110/pi-observer-recovery.json)。

首次GitHub检查的唯一失败是浅克隆缺少历史基线2a4f4b8；CI checkout已改为拉取完整历史，让已有基线检查取得真实输入。没有跳过该检查或改变运行核心，最终状态见PR检查。

原始工作区、meter、events、harness和manifest在本机bench/efficiency_20261011/artifacts/保留。隔离profile与生成bytecode回收，清单随证据保存；没有新增依赖、PATH或Codex认证修改。源码与文档更新到既有GitHub PR；便携包仍为原构建，本次没有发布npm或Release。
