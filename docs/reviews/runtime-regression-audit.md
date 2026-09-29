# 运行核心增量与效率回归定向审计

2026-09-29。只读比较首次开源 `d038117`、接手基线 `b45c01e`（0.5.1）与本次复测核心 `34119c6`；检查时 HEAD 为 `1064e6d`。未调用真实模型、未读密钥、未使用 Claude/Pro，未修改生产代码。本报告不是全版本终审。

## 问题与确定性证据

### P2：0.8 把计划和验收引用做成了默认暴露的交互协议

新增来源为 `e49b717`：`src/agent_loop.py:531` 注册 `update_plan`，`src/task_plan.py:8` 定义带验收条件、状态和证据的 schema；每次更新走 provider 原生工具，因此该调用形成下一轮反馈边界。`src/agent_loop.py:608` 每请求额外附加完整 CURRENT TASK PLAN，历史里同时保留此前 update_plan 的完整参数。

`validate_plan` 要求 completed 至少一个成功回执、一次最多一个 in_progress。错误引用或过多 active 会返回工具失败；它们并不证明产物错误，却可使模型消耗恢复轮次。0f02aaa 加入短回执引用是修复易错接口，不能消除计划维护本身的额外工作。

确定的开销是 schema、工具调用/返回、参数历史和当前状态副本；计划是否导致新 benchmark 的特定额外调用，须按样本执行记录归因，不能仅从存在该工具推算 token。工具描述称简单任务不必调用，system 也用“可用”，故它不是代码层强制计划。它仍默认进入全部请求，benchmark 没有禁用它。

主会话随后按完整事件（不是可能截断的 retained records）统计当前12份：update_plan 调用为0。因此本批实际支付的是额外 schema，没有非空计划的状态注入，也没有计划维护/引用失败轮次。**不能把本批主要耗量归因给计划。** 以上重复状态与失败开销是启用后的条件行为及产品复杂度问题。

建议：把面向桌面长期任务的结构化计划设为显式能力/任务类型选项，短任务先关闭；保留可见进度但不要求模型为收尾维护一套证据账单。若启用，当前状态尽量紧凑，只保留对下一步必要的信息。完成证据应绑定验收事件，不能只以任意成功工具 id 替代验收语义。

### P2：0.8 全文保留并回传 reasoning，使流式写入正文存在新的重复载荷

`e49b717` 新增 `assistant_reasoning` 累积，当前 `src/agent_loop.py:872` 收集全部 thinking，`:1070`–`:1075` 对非原生工具轮也写入 `reasoning_content`；`:1724`–`:1727` 对原生工具轮保留；`:657` 为 DeepSeek 的历史 assistant 补齐字段。b45c01e 只保留清除命令标签后的 assistant 正文，不把 thinking 放回 messages。

如果模型把 tf-write 源码放在 thinking，这份源码现在留在 reasoning_content，同时 `src/context.py:86`–`:100` 的流式 ledger 仍返回 write/append/edit 内容（有裁剪）。原来的 ledger 内容并非新增；新增的是 reasoning 原文副本。离线纯计划 fixture 给 `_handle_traditional_tools` 传入 1000 个 R，随后历史确实保留了 1000 字符 reasoning。

这证明请求载荷增长，不证明 DeepSeek 把这些字符全部计成 input token，亦不证明移除它们能保持模型正确性。保留 reasoning 有服务商协议目的，尤其 thinking + tool calling；不能为省 token 直接删掉必要字段。建议先按 provider/模型协议收窄保留范围，并分开计量 reasoning 字节、实际 provider 输入 usage、缓存命中；针对无需回传的模式减少留存。只有实际用量 A/B 才能量化收益。

### P2／正确性取舍：0.6 让每个 read/bash 成为即时反馈边界，减少旧版可连续执行空间

`b9f6f7f` 修改 `src/agent_loop.py:1276`：原来仅 `cmd.need_result` 才中断，如今 registry flow 为 blocking/confirm 也强制 `need_result=True`。b45c01e 的 bash schema 本来已标 blocking，但旧文本分派器忽略它，故无 need_result 的 tf-bash 能与后续 tf-write 一起进入 FIFO。旧版即使未显式 need_result，成功 blocking 命令也可能在 END_TURN 由 pending_auto_result 反馈，不能说其 bash 永远不增加轮次。

本次使用从两份 git 源码 AST 提取的真实分派方法，配无副作用 stub，输入相同 `[bash, write]`：

| 核心 | 返回 | 进入 FIFO | 立即执行 | 同块后续命令 |
|---|---|---|---|---|
| b45c01e | `(2, none)` | bash、write | 无 | 保留 |
| 34119c6 | `(1, need_result)` | 无 | bash | write 标 skipped |

所以“旧版能同轮连续跑的某些操作，改造后需要反馈轮”是确定变化。是否是回归须区分任务：读取结果、测试成败、不可预测 shell 输出是后续推理依赖，旧版不停有错误执行风险；对于确实独立、可预测的批处理，统一阻塞会损失吞吐。不能把所有 bash 放回 delayed 作为修复。应优先用连续 tf-write/edit 输出；如要批量 shell，需明确独立性和失败边界，再提供有界批处理能力。

### P2／正确性取舍：0.8 强制完整读版本，已有文件修改有新增门槛

`src/executor.py:66` 默认 `require_read_revision=True`；`:378` 起检查已有文件是否完整、未脱敏地读取及 revision 一致，拒绝未读覆盖。0.8 新增，b45c01e 不具备此检查。system 对应新增“覆盖或修改已有文件前必须完整读取”。这会使只通过 grep/局部内容就可定位的修改增加一次完整读，甚至因截断读无法授权。新建文件与自己刚写的文件可连续操作，门槛不普遍阻断生成任务。

建议保留外部修改冲突防护，优化已传入附件/已知完整版本的授权复用；不要在无证据时让模型反复读同一版本。不能为了旧 benchmark 数字回退成盲覆盖。

### P2：提示层确有叠加，但不能把全部工作流风格归给本轮改造

三份源码的准确演变如下（字符数为 Unicode 字符，不是模型 token）：

| 项目 | d038117 首次开源 | b45c01e 接手基线 | 34119c6 |
|---|---:|---:|---:|
| BUILTIN_SYSTEM_PROMPT | 2347 | 1409 | 1908 |
| bundled thinkflow SKILL.md 原文 | 不存在 | 3995 | 4528 |
| 首次先读 read_skill 要求 | 无 | 有 | 有 |
| 拉尔夫/todo/30行对抗检查等 | system 内有 | 迁入 skill | skill 内保留但减轻机械要求 |
| runtime 状态 | 起始戳记 | 起始戳记 | 戳记＋环境＋计划（若有）＋协议恢复（若有） |
| 原生文件工具首轮可用 | 是 | 是 | 是 |

`0eeee19`（2026-08-25）将工作方法迁到新 bundled skill，并引入“第一次先 read_skill”。从 d038117 看，这是新增的一次指南读取要求；若模型遵循，至少要发生一个原生工具反馈边界，且系统仍保留部分重复协议。它不是后续 0.6–0.8 才添加。首次开源本身就有拉尔夫、待办、对抗验证、文件归纳和最终报告要求，不能描述成原来只有很短协议、后来全部套上个人工作流。

0.6–0.8 确实又加了结构化计划、完整读版本、环境提示、原生/文本协议区分和证据引用。当前 skill 相对 b45 还删弱了“超过30行就复查”、任意短任务留接力、通过后反复验证等要求；这些修改方向是在减负。建议将首次必读指南改成少量核心协议随 system 一次交代，其余故障/高级机制按需读；这是可以单独消融的候选，不是已证实的性能结论。

## 请求体离线计量

通过 git show + 内存 import loader 装载 b45c01e，当前源码装载 34119c6 同核心；只构造 AgentLoop 和请求体，不发送 HTTP。统一 Windows cwd、一个 fixture 用户消息、禁用 web_search/fetch_url/generate_image/list_skills，关闭用户技能发现，其他 provider 工具默认开放。JSON 用 `ensure_ascii=False,separators=(',', ':')`；没有读取配置文件或个人 skills。第一次脚本因基线源文件 UTF-8 BOM 无法编译，改为 utf-8-sig 后成功，未产生业务副作用。

| 项目 | b45c01e | 34119c6 | 增量 |
|---|---:|---:|---:|
| 无计划 runtime content | 21 | 244 | +223 |
| tools schema JSON | 3712 | 4584 | +872 |
| 首请求 messages JSON | 1641 | 2389 | +748 |
| tools 数 | 14 | 15 | +1 update_plan |

schema 增量包括 update_plan 和 bash 平台说明。system＋schema＋runtime 固定载荷确有增加，但总计不是“请求数翻倍”的证明。单步骤 fixture 计划 `fixture / implement / pass` 使 runtime 从 244 增至 401 字符（+157）；真实计划按字段长度变化，最大20步骤且每个验收字段允许1000字符，不能把 fixture 当典型均值。

附带发现：worker 的禁用名写成 generate_image，而注册名是 image_generate，因此这个工具仍出现在14/15项列表中。未调用它、未联网；这是一项装置声明与实际暴露面不一致的小问题，不能归为本次 token 变差的已知原因。

## 排除的错误归因

1. **新 benchmark 没有关闭压缩。** worker `thinkflow_worker.py:58` 的 `context.enabled=False` 仅供 `src/cli.py:144` 控制 AGENTS/上下文文件注入；压缩由独立 `compaction` 配置控制。worker 未设置 compaction，实际 AgentConfig 为 enabled=True、80条/200000字符触发、保留最近30条、摘要16000字符。b45 与当前 `src/compaction.py` 完全未改；`runtime_context.py`、`delivery.py` 也未改。旧 runner 没有显式关闭 compaction，但它加载外部 provider profile/配置，因此不能据未保存的配置推断旧实测最终生效值。此次未从全部历史快照逐一核实触发次数。
2. **原生回执没有被常规路径再次注入 ledger。** 当前 `_handle_traditional_tools` 在 tool/result 消息落入历史后才 `mark_injected`（`:1771`–`:1774`）。纯 update_plan fixture 后 `context.build_injection()` 返回 None。b45 原生工具不进入这套 ledger；当前增加原生回执 header，但没有“工具全文＋ledger全文”的常规双注入。崩溃前 pending 回执为恢复故意保留，不能直接认定重复。流式命令 pending 注入后置 injected=1，旧版已有。
3. **动态状态不是每轮修改 system，也不无限累积历史。** 从 d038117 已采用 stable system＋请求末尾临时 user 状态；`:615` 拼接新列表，不写入 self.messages。当前新增环境/计划会重复发送，但没有因此把 system 前缀每轮改变；不能声称它必然导致全量缓存失效。
4. **自动续写和交付验证不是本轮新框架。** d038117 已有 max_auto_continues=8、delivery_verify=True，并无开关地检查可运行文件；0a804bc 后改为两个验证开关默认 False。b45 和当前都默认关闭，当前 worker 没开启，故不能把新36次中的重复验证归给这两个强制验证分支。它可能来自模型、skill、用户题意或计划维护，需要样本逐次核对。
5. **当前首轮原生 write 不是偏离基线的新开关。** d038117/b45 默认全开放；e49b717 反而在未 fallback 时隐去六个文件原生工具，34119c6 恢复旧行为。文本优先仍由 prompt 指导，`tool_choice=auto` 从基线已在。应区分 e49 原轮和341复测，不能把恢复保底说成首次引入传统调用。

上述验证开关也不等于业务验收：从 d038117 至当前 `src/delivery.py` 未变，它查找受影响文件父级的 package.json，必要时 `npm install`，然后 `npm run build`；没有支持的 manifest 则 attempted=False，不自动执行这次 Python 基准的公共测试。`_build_runnable_artifact_feedback` 只是让模型继续验证，且同一 turn records 只要有一个成功 bash 就免提醒，并不核对该 bash 是否测试相关文件、是否在最后一次修改后执行、是否完成用户验收。重新打开它们不能直接补齐本轮业务测试证据。

## 流式核心是否还在

仍在。`src/agent_loop.py:1261` 的文本分派对 write/append/edit/mkdir/touch/copy 继续进入 FIFO；`:1292` enqueue 后返回，模型流继续读取；执行失败与 socket 读取竞速，成功不因每个写入重新调用模型。执行器并未改成“全部操作等流结束才执行”。新增 read/bash 即时边界、原生工具调用、计划维护会减少单轮能覆盖的动作集合；模型如果选择 native write，也会按原生反馈边界走。这证明混合 harness 的适用范围更窄，不证明流式设计已被删除。

因此准确回答是：**没有放弃流式内核，但运行外层确实变厚，0.8 的默认计划/验收协议和 reasoning 回传是新增负担；部分速度损失来自更严格正确性边界；一批看上去“后来加的流程”其实首次开源或0.5就已存在。**

## 优先处理顺序与证据缺口

1. 先处理本批实际出现的长思考与截断：主会话完整事件统计为10次length分布9/12，7次出现completion=8192全部为reasoning、text=0、commands=0；length合计输出81920、reasoning72657。这比未调用的计划工具更直接。下一轮做同模型、同题的输出预算/思考行为隔离，不用增加更多通用提醒来覆盖症状。
2. 单独消融“首轮先读 skill”，将真正必需协议浓缩为稳定提示，其余按需查阅。本批主会话完整事件统计read_skill为9次，故这是实际出现的成本；但它是0.5已有机制，不能归责0.8新增。
3. 按 provider 协议审计 reasoning 回传与生成源码复载，保留必要 thinking 工具链字段；实际计量前不承诺 token 收益。轻任务可移除未用update_plan的schema，但由于本批0次调用，不把计划瘦身列为解决主要差距的首要手段。
4. 保留流式文件操作；read/bash 的正确性边界与原生保底保留，若要恢复批处理吞吐，做明确有界、失败可停止的批次而非忽略结果。
5. 基准另做同模型、同题、同8K/较大预算、同prompt能力开关的隔离比较。旧 normal runner 明确 `--max-tokens 20000`、max_auto_continues=12，新worker为8192/默认8，且模型、题型、正常终态标准不同。较小上限减少单轮连续输出空间是结构事实，但提供商实际输出可能超请求上限，不能把参数差直接换算成轮次损失。

旧7 calls对104等报告未冻结源码 SHA；d038117 是最早入库代码，不是已证明的每份旧报告运行源码。因此本审计只给各版本可复核行为，不宣称由它复原旧成绩。未做真实模型 A/B，未给某新增机制分配“造成多少百分比退化”的虚假因果。该结论支持承认新增负担与调整方向，同时不支持简单退回旧版就能重现旧优势。

当前已发布复测本身也不支持“调用次数更差”的概括：`benchmark-rerun-review.md:87`–`:91` 的 ThinkFlow/OpenCode 为108/120请求，输出204919/117265（ThinkFlow约多74.7%），其中推理125495/70578；主要现象是调用略少而输出更长。静态输入字符计量不能解释所有输出增量，以上结构候选仍需隔离消融。该报告`:64` 的 ThinkFlow最终代码主动测试成功证据6/12是当前口径发现，另外3份未知、1份改后未复验、2份未执行；没有旧版同口径审计，不能称为“主动验证从旧版退化到6/12”。外部产物评分及正常终态仍为12/12。

本次验证：源码/git归因、两版离线请求体、纯计划回执去重 fixture、两版分派边界 fixture均完成；`git diff --check` 无报错。无临时快照文件和缓存产物，只新增本报告。
