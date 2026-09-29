# 旧 benchmark 的证据与新测试的可比边界

审计日期：2026-09-29。只读检查仓库资产与 Git 历史；未调用模型、未运行 Claude Code / Pro、未读取凭据、未改变被测源码或数据。本文件是审计输出。

## 主要发现

1. 旧公开结果有实际任务支撑。正式对照是完整 React / TypeScript / Vite 前端和长大纲五章小说，包含 `glm-5.2`、`deepseek-v4-flash` 两轮；不能把它们概括成没有工程内容的演示。
2. 旧结果和新六题测试回答不同问题。旧对手是 Claude Code、以大量新建文件和连续写作为主，每次 ThinkFlow 请求 `max_tokens=20000`，最多五轮外层验证与反馈，默认每外轮 1800 秒；新对手是 Pi / OpenCode，含诊断、修复、状态恢复、协议边界与重构，统一请求 8192、全程 300 秒 / 24 上游请求，没有把隐藏验收失败投回模型。直接用新排名推翻旧案例优势，或用旧优势证明新排名应当更好，都不成立。
3. 7 vs 104 的早期快照能逐行重算，数值内部一致；但缓存完全免费后的 token 代理指标只减少约 14.85%，远小于原始 total token 减少约 93.16%。它是一次前端交付成本样本，不能直接说现金成本减少 93%。
4. 07-05 正式报告明确修复了 ThinkFlow final usage chunk 提前丢失，不能用初版 Pi “只取末轮 usage”的缺陷否定这组 Claude Code / ThinkFlow 正式对照。不过正式报告的 raw runs、session、metrics.json 未公开、当前本机路径也不存在，无法独立核验所有请求和产物。应保留报告值及其证据边界。
5. `controlled_same_prompt_v0.5` 是实验规范和离线分析模板，没有真实运行记录；不能把示例的 `glm-5.2` / 成功标记当作实测。
6. 当前证据不能把成绩变化归因于某一个因素。任务、对手、模型入口、输出预算、验证闭环、源码版本与计量方式同时变化，缺少一次只变一个条件的交叉实验。

## 历史范围与保存状态

`b45c01e` 是 2026-08-25 的 0.5.1 版本/文档字符串修复；其父提交 `0eeee19` 的 README 第188—202行已经列出两组旧实测，并明确 raw runs、日志、session 与构建产物不进入仓库和 npm 包。三个旧 benchmark 目录在 `d038117`（2026-07-06 初次开源）已存在；从 `b45c01e^` 到当前 HEAD 对它们执行 `git diff` 无差异，因此本文引用的现存旧文件就是该历史资产，并非本次新测试改写的结果。

当前检查：`runs`、`runs_normal_app`、`runs_deepseek_v4_flash`、`reports_normal_app/metrics.json` 均不存在；`controlled_same_prompt_v0.5/runs` 存在，但没有运行 JSON，已提交 summary 也写明无记录。没有跨工作区搜索私人配置或旧日志。

有一个版本边界必须保留：`0a804bc`（2026-07-06，`Disable extra validation turns by default`）将生产默认 `delivery_verify` 从 true 改成 false，并新增默认 false 的 `auto_verify_runnable_artifacts`。更晚 `0eeee19` 又改变内置 skill 与系统提示。07-05 报告不能被视为“在 `b45c01e` 的默认配置上完成”，公开材料没有把每格精确绑定到源码 commit / 客户端版本。下文对历史生产逻辑的引用均注明 `0eeee19`，只证明公开实现与计量结构，不能倒推旧运行配置。

## 三组材料分别是什么

### A. `reproducible_agent_efficiency`：一次前端配对快照

任务是从空目录建立 Agent Efficiency Lab，要求 React / TypeScript / Vite、真实状态与计算、localStorage、响应式布局、清晰的组件/数据/类型/工具/样式结构，禁用 UI 和图表库。任务中的 ThinkFlow / Claude Code / Baseline 示例数值是页面种子数据，不是本轮成绩，不能把那三行当成额外 benchmark 样本。依据：[prompt.txt](../../bench/reproducible_agent_efficiency/prompt.txt#L2)，其中第22—49行为种子数据。

| 字段 | ThinkFlow/TK | Claude Code/CC |
|---|---:|---:|
| API calls | 7 | 104 |
| 输入 | 124,515 | 2,246,442 |
| 缓存命中 | 92,672 | 2,231,040 |
| 非缓存输入 | 31,843 | 15,402 |
| 输出 | 33,344 | 61,155 |
| total | 157,859 | 2,307,597 |
| 缓存全免费代理指标：非缓存输入 + 输出 | 65,187 | 76,557 |

数据源：[usage_compare_report.latest.json](../../bench/reproducible_agent_efficiency/usage_compare_report.latest.json)、[报告](../../bench/reproducible_agent_efficiency/usage_compare_report.latest.md#L3)。本次只在内存逐项求和：TK 7条、CC 104条的 prompt / completion / total / cached / cache_miss 均与汇总相同，没有 total=0 的行。CC 的104条全标 `deepseek-v4-flash`；TK逐轮没有 model 字段，因此该材料自身无法证明 TK 模型、两边同模型或同上游。

已知请求入口：CC 经本地 8765 Anthropic-to-OpenAI bridge 记录 usage JSONL，[报告第24行](../../bench/reproducible_agent_efficiency/usage_compare_report.latest.md#L24)。公开文件没有上游端点域名；不将 profile 名或今天的配置替代历史入口。bridge 的实现、实际启动命令及总表生成脚本未入本组资产，无法检查是否含 smoke、重试、辅助请求、断流后用量，亦不能反向断言一定漏计。

TK 37条操作回执，按快照分类是 append=1、bash=3、mkdir=3、read=6、write=24；这是行为记录数，不是额外模型调用。CC请求行保留 messages / tools / tool_calls，不等于逐项工具执行日志。

验证与终止：README 规定分别对实际目录执行 `npm run build`，快照称两者通过；CC忽略请求目录，创建到另一工作区，所以交付合规不等价。没有保存统一外层 validator、实际 build log、任务完整交互测试或终止状态机。`max_tokens`、思考参数、TF请求超时/总时间、CC请求上限、人工反馈次数与系统提示均未知，不能拿生产默认值补齐。TK某单轮 completion=26,174 只证明观察输出较长，不证明请求配置为26K或上限被严格执行。

[analyze.py 第28—41行](../../bench/reproducible_agent_efficiency/analyze.py#L28)仅读取已存汇总并比较大小，没有执行模型、build或逐行统计，也没有重新确认产物。它证明快照比较关系，不能作为完整重跑 runner。

支持的结论：该一次成本样本中 TK calls减少约93.27%、原始total减少约93.16%、completion减少约45.48%；即便把缓存输入完全免费，代理指标仍减少约14.85%。不能据此推出普遍现金节省、同模型因果收益或前端功能等质。

### B. `controlled_same_prompt_v0.5`：规范，零实测样本

[README 第11—22行](../../bench/controlled_same_prompt_v0.5/README.md#L11)要求固定prompt、空目录、无手修、记录provider/model/system/cache，并分同入口 harness 对照与产品对照。manifest列出 npm install、build、目录合规、无明显TS/console回归，但没有实现相应 runner 或 validator。

[summary.md](../../bench/controlled_same_prompt_v0.5/reports/summary.md#L3)明确 `No run records found`。`run_record.example.json` 的 `glm-5.2`、成功标记和零计数是示例。它不提供 provider 公共域名、token / 思考 / 超时 / 终止配置，也不构成样本。

[analyze.py 第19—37行](../../bench/controlled_same_prompt_v0.5/analyze.py#L19)只加载 JSON；缺少usage字段按0处理。[第80—91行](../../bench/controlled_same_prompt_v0.5/analyze.py#L80)依赖 JSON 中 build/delivery 布尔值选赢家，未验证产物、prompt hash或计量完整性。因此将来用于实测时也需要独立采集与验证，不能用这个离线汇总器宣称实验已被控制。

### C. `agent_comparison_20260704`：两道完整任务 × 两模型 × 两对手

正式样本共8格，每个“题目×模型×agent”仅一次：GLM四格 + DeepSeek四格，不含重复运行或可估计稳定性的样本。README 明确 Pi smoke未可靠结束，正式报告只比较 CC与TF；不能据此宣布胜过 Pi 或 OpenCode。

前端沿用完整 Agent Efficiency Lab 要求。正式正常runner读取 `prompts_clean`；[clean_prompts.py 第21—44行](../../bench/agent_comparison_20260704/clean_prompts.py#L21)重建小说prompt，要求五个独立章节、README、每章800非空白字符、连续人物/设定/文风；大纲含主题、五章结构和八次细节备忘。其内容生产性质与多文件写入确实吻合续想的设计目的。

| 模型/任务 | TF calls / total / 秒 / 外轮 / 验收 | CC calls / total / 秒 / 外轮 / 验收 |
|---|---|---|
| glm-5.2 / 前端 | 25 / 757,674 / 842.187 / 1 / 通过 | 34 / 2,663,449 / 871.893 / 2 / 通过 |
| glm-5.2 / 小说 | 11 / 210,719 / 360.022 / 1 / 通过 | 20 / 526,214 / 2671.297 / 2 / 通过 |
| deepseek-v4-flash / 前端 | 22 / 423,916 / 392.639 / 1 / 通过 | 30 / 639,183 / 361.006 / 5 / 失败 |
| deepseek-v4-flash / 小说 | 6 / 105,576 / 180.043 / 1 / 通过 | 13 / 274,974 / 841.005 / 1 / 通过 |

来源：[GLM summary第7—10行](../../bench/agent_comparison_20260704/reports_normal_app/summary.md#L7)、[DeepSeek summary第7—10行](../../bench/agent_comparison_20260704/reports_deepseek_v4_flash/summary.md#L7)。GLM报告称“usage监控已修复”；DeepSeek报告明确两者切换同一model标签。两模型之间不应合并成一次均值排名。

#### 实际runner、反馈与终止规则

- 正式入口是 `run_benchmark_normal.py`，而不是早期 `run_benchmark.py`。前者默认模型 `glm-5.2`、支持环境变量切换，最多5外轮，每外轮默认1800秒：[第25—33行](../../bench/agent_comparison_20260704/run_benchmark_normal.py#L25)。没有共同的24次上游请求总预算。
- 每外轮运行结束后执行相同 validator，失败JSON成为下一提示，保存同会话；通过即停，否则超时即停，五外轮耗尽也停：[第64—143行](../../bench/agent_comparison_20260704/run_benchmark_normal.py#L64)。验证通过检查在 timed_out 检查之前，因此“通过”也不完全等于进程正常结束。用时覆盖外层验收，且轮询每30秒，短任务时间精度受轮询影响；不是只计API耗时。
- 文件有两套同名提示生成函数；Python实际采用后面的英文 `build_initial_prompt` / `build_continuation_prompt`：[第593—614行](../../bench/agent_comparison_20260704/run_benchmark_normal.py#L593)。它要求持续交付、自检、禁止前端常驻dev server、验收失败只补修不重启。前面的中文定义不会生效。
- TF指定 `opencode-go` provider profile、同模型标签、`--stream-usage`、`--max-auto-continues 12`、`--max-tokens 20000`、workspace-write/trust、session恢复：[第240—288行](../../bench/agent_comparison_20260704/run_benchmark_normal.py#L240)。12是length/transport自动续写次数配置，不是所有tool/need_result/失败反馈请求的总上限。公开runner未覆盖 thinking、temperature、上下文策略或本地profile具体值。
- CC用自身产品工具/系统提示，`-p --output-format json --permission-mode bypassPermissions --model ...`，首轮指定session-id，后续resume，同stdin传prompt：[第186—239行](../../bench/agent_comparison_20260704/run_benchmark_normal.py#L186)。没有固定版本、系统提示/扩展隔离、显式max_tokens、thinking/temperature或底层请求总数上限；不能宣称只改变harness一个变量。
- 超时杀进程树：[第333—370行](../../bench/agent_comparison_20260704/run_benchmark_normal.py#L333)。1800秒是每个外层CLI运行上限，不是TF单次HTTP timeout。`0eeee19:src/agent_loop.py:504—509` 的实际HTTP client为600秒/connect30秒，另一个provider封装的300秒并非此运行链的直接调用配置；仍不能证明07-05使用相同历史源码。
- 公共provider域名：所审旧runner只保存profile `opencode-go`，没有其公共上游URL；CC依赖机器当时的外部配置，报告也未保存域名。必须标为未知，不能据名称或同model标签猜它们实际走了同域名、同路由或同后端版本。

#### Validator到底证明什么

[analyze_runs.py 第14—105行](../../bench/agent_comparison_20260704/analyze_runs.py#L14)的实际逻辑是：

- 前端：固定九个必需路径存在，`src/components/*.tsx`至少一个，在所找到项目根 `npm run build` exit=0。build上限180秒；没有单独验证 npm install、浏览器console、响应式视觉、localStorage、交互或成本公式。`find_project_root`允许进入子目录寻找package.json，因此并非严格验证直接交付在当前目录。
- 小说：README与chapter-01至05存在，每章≥800非空白字符。没有语义一致性、原创文风、人物弧线、质量/字数等价验收。

因此结果是“相同构建/存在/长度验收过关后的行为与用量”，不能扩写为完整前端功能及文学品质等质。提示要求高于实现验收，这不是判定旧样本无效的理由，而是它支持的结论边界。

#### Usage来源、完整性与对手配置

正式runner的CC usage优先累加 JSON `modelUsage`，否则取 `usage`；`api_calls=num_turns`，`total=input+output`，缓存单列：[第387—423、457—492行](../../bench/agent_comparison_20260704/run_benchmark_normal.py#L387)。多外轮把各CLI返回值相加：[第431—454行](../../bench/agent_comparison_20260704/run_benchmark_normal.py#L431)。没有raw输出无法确认resume返回的是增量还是累计、各模型辅助请求/重试是否包含，也无法校准 `num_turns` 为真正HTTP请求数。

TF取最终session累计，不把每外轮累计快照再次相加：[第431—433、495—520行](../../bench/agent_comparison_20260704/run_benchmark_normal.py#L431)。`0eeee19:src/usage_tracker.py:92—93` 的 `api_calls=len(turns)`；`src/agent_loop.py:673—675` 每模型逻辑轮先加入零usage对象，`1088—1101` 内部HTTP重试不会添加新轮。`1225—1244` 取 final usage更新当前轮；`1285—1295`延后finish直到usage/[DONE]。这确实对应报告所述usage修复，但遇主动截断/客户端断流、usage缺失或HTTP重试时仍没有统一代理的完整性标志与逐请求校验。应区分“最终usage已修好并报告非零”与“所有上游请求已被独立核验完整”。

GLM报告指出CC本轮 `cacheReadInputTokens` / `cacheCreationInputTokens`均上报0，不能推出上游没有内部缓存；TF final stream上报缓存命中。CC与TF缓存字段语义未经统一转换，报告已经注明不直接比较：[技术报告第43—49行](../../bench/agent_comparison_20260704/reports_normal_app/technical_report.md#L43)。同样，CC costUSD是产品自报估价，TF无该值，不构成同一价格体系的账单比较。

`saved_calls`不是对手真实反事实测量。`0eeee19:src/usage_tracker.py:100—114`使用 `max(0, commands - interrupted_turns)`，再乘平均prompt估算少重发输入。报告中的37、12、6不能再当实测节省收益。实际“calls25 vs34”等来自两个运行计数，仍有前述口径差异。

早期 `run_benchmark.py:169`默认3600秒，只跑一次CLI，没有正式runner的外层修复；TF未显式max_tokens。其CC取JSON顶层usage、Pi只保留最后出现的message usage（第344—371行），确有末轮计量局限；正式Pi未报告，因此不能把这项局限套到正式CC/TF表格。`analyze_runs.py:142`还保留“TF无usage”静态说明，是旧分析器文案，不推翻正常runner报告明确记录的usage修复。

支持的结论：这些固定任务与模型标签下，TF相对CC的报告调用和total更低；GLM两题均过相同验收，前端total约少71.55%、小说约少59.95%；DeepSeek小说两者均过，TF total约少61.61%，前端TF通过而CC五外轮未过。CC失败格不能当作等质交付的纯成本比例。每格仅一次、原始产物缺失、版本与路由未冻结，因此不支持普遍优势或机制独立因果收益。

## 和新六题 / 8K / 24请求 / 300秒为什么不能直接比

| 条件 | 旧正式双题 | 新六题测试 | 对解释的影响 |
|---|---|---|---|
| 工作负载 | 完整前端新建、连续小说写作 | Python多文件交付、既有代码修复、幂等持久状态、流式字节协议、跨文件重构、区间边界 | 成功写入可省回合的机会、必须读结果再推理的比例不同 |
| 对手 | Claude Code；Pi未正式计分 | Pi0.87.1、OpenCode1.18.33 | 新结果不能说明对CC优势消失 |
| 模型入口 | glm-5.2 / deepseek-v4-flash标签，profile与CC历史上游未冻结 | 官方 `api.deepseek.com` 的 deepseek-flash，统一代理覆写参数 | 模型标签/路由/后端/延迟不能自动视为相同 |
| 单次输出 | TF请求20000；CC未固定 | 全部请求8192，enabled/high、temperature0 | 长思考与长连续生成更易跨续写边界；需要实验才能确定影响幅度 |
| 总预算 | 每外轮1800秒，最多5外轮；无共同HTTP请求总数限额 | 全运行300秒、最多24上游请求 | 旧TF前端25calls/842秒，DeepSeek前端392秒，GLM小说360秒，已超过新预算；旧成功不意味着新预算必过 |
| 外层反馈 | 每次CLI结束跑validator，失败回投同会话 | 单次进程运行结束才隐藏grade，不回投隐藏失败 | 旧“成功”包括验收器辅助修复，新衡量自主完成闭环 |
| 验收 | 文件、build、章节长度 | 公共测试 + 48隐藏工程行为检查，正常终态另列 | 新测试更深核验其题目的工程契约，但不覆盖旧前端交互/小说品质 |
| 用量 | 客户端统计与modelUsage / session，不统一完整性信号 | 代理每个上游请求记usage，缺失有明确状态 | token数与calls的精确口径、失败/重试覆盖不同 |
| 断流观察 | TF客户端关连接可能没有末尾usage | 客户端断流后代理继续有限读取取usage | 新观察值包含该观测模式真实消耗，不能冒充直接取消连接的生产成本 |
| 隔离 | CC扩展/系统提示版本不冻结 | 单agent、禁网络/委派/外部MCP与用户扩展，保留各自原生机制 | 产品上下文负担与可用工具不同 |
| 重复数 | 每题每模型每agent1次，8格 | 每题每harness2次，36格 | 新样本较多但仍是定向套题，不是领域综合排名 |

新条件不只来自README：[run.py 第79—151行](../../bench/harness_benchmark_20260929/run.py#L79)是单次进程预算与结束后grade；[meter.py 第120—145行](../../bench/harness_benchmark_20260929/meter.py#L120)真正按24/300拒绝新请求、统一model/max_tokens/thinking/high/temperature及stream_usage；[第154—174行](../../bench/harness_benchmark_20260929/meter.py#L154)断流后继续读；[tasks.py 第474—489、546—568行](../../bench/harness_benchmark_20260929/tasks.py#L474)提供公共契约及独立隐藏检查。8192是请求参数，提供商有输出超出该参数的现象，不能冒称硬输出截断。新suite六题分别为receipt_package、durable_inbox、frame_decoder、pricing_refactor、repair_routes、interval_coverage。

这说明新测试对其问题有价值，同时覆盖不到“长篇内容生产 + 大批新文件 + 充足单请求预算”这一旧优势场景。旧测试也无法证明续想对修复/恢复/边界工程工作领先。两类结果可以同时成立。

## 剩余不确定项与最小补证方向

未证实：早期TK模型与参数；所有旧格的实际provider公共域名、精确模型版本、CC客户端版本/扩展/系统提示、思考/max_tokens；07-05逐请求原始usage、resume计数是否增量、断流/重试成本；前端交互/视觉/数学逻辑与小说语义等质；每格运行所对应源码commit。未读取私有配置来猜测这些信息。

如果需要区分“续想退步”与“测试场景变化”，最小补证是保存旧两道真实任务作为独立内容生产分组，先在统一代理上记录三者真实请求与用量，并在同一源码/模型/工具隔离下单独比较8192与20000参数。再单独比较有无相同外层验收反馈。每次只改变一个因素；保留失败、报告任务功能质量与主动验证，而不是只找省token的成功格。此处是验证设计建议，本次没有运行或授权扩大测试。

本轮产出为此审计报告。事实核对使用静态代码、Git diff / show、路径存在性与已保存快照求和，未改写旧材料。
