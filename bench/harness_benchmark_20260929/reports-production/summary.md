# ThinkFlow / Pi / OpenCode 同模型基准 · 2026-09-29

同一官方 DeepSeek Flash，高思考；本次6题、3个harness，共36个有效样本、37次实际尝试。没有使用 Claude Code。
正式套题完整：六题各两次、三个harness。
先看完成质量，再看资源消耗。此小规模本地套题不能推出行业综合排名。

| Harness | 正常交付 | 产物通过 | 检查平均分 | 平均秒 | 中位秒 | 平均API请求 | 总token |
|---|---:|---:|---:|---:|---:|---:|---:|
| thinkflow | 12/12 | 12/12 | 100.0% | 105.38 | 93.38 | 12.92 | 3,845,627 |
| pi | 12/12 | 12/12 | 100.0% | 54.62 | 52.05 | 10.25 | 1,397,532 |
| opencode | 12/12 | 12/12 | 100.0% | 59.90 | 56.31 | 9.58 | 1,732,556 |

**上表为有效样本；原始计划有观察器故障中断，补测一次，不是36次首次全部正常完成。**
- `repair_routes-thinkflow-r2`：UnicodeEncodeError: 'gbk' codec can't encode character '\ufffd' in position 270: illegal multibyte sequence。原始exit=1，即使产物通过也不算正常交付；额外4请求、47,031 token、56.344秒原样保留。仅此装置故障使用同冻源、同题、同参数的独立目录补测，普通模型失败不补测。

完整实际消耗（有效样本加中断尝试）：thinkflow 159请求 / 3,892,658 token；pi 123请求 / 1,397,532 token；opencode 115请求 / 1,732,556 token。资源效率配对使用有效样本，不能忽略这笔额外开销。
全部实际尝试有1次下游断流，该请求完整输出338 token；包含断流前输出，无法全算作观测额外开销。

产物通过要求隐藏检查全部通过且公共测试未被修改；正常交付还要求无超时、退出码0，以及观察到正常终态：ThinkFlow completed、Pi最后assistant stop、OpenCode最后step_finish stop。
该计分口径不额外确认模型主动运行了任务要求的测试，也不等同于完整遵守工作流程；实际测试执行与结果证据须另看过程复核。
检查分数仅为断言通过率，不是业务完成百分比。例如缺失CLI也可能通过“非零退出且未覆盖输出”的错误路径检查，因此以整题通过为主判。

## Token 分解（有效样本）

| Harness | 输入（含缓存） | 缓存命中 | 非缓存输入 | 输出（含推理） | 其中推理 | 提早断流请求 |
|---|---:|---:|---:|---:|---:|---:|
| thinkflow | 3,554,722 | 3,119,360 | 435,362 | 290,905 | 172,318 | 0 |
| pi | 1,257,379 | 1,202,560 | 54,819 | 140,153 | 84,267 | 0 |
| opencode | 1,623,474 | 1,512,064 | 111,410 | 109,082 | 63,743 | 0 |

总token = 输入 + 输出；推理已经包含在输出中。非缓存输入不是账单价格，缓存也不假定免费。
全部实际请求均有完整输入/输出usage，包括重试和附加模型请求；细分字段缺失时标为未知。用量代理在下游断流后继续读取上游以收取末尾usage；若发生断流，消耗包含这一观测行为，不能等同于立即取消连接的成本。

有效样本中的断流请求涉及完整输出token：thinkflow 0、pi 0、opencode 0。这些包含断流前已生成的部分，不能全部当作观测额外开销；当前无法精确切分。

## 每次运行

| 题目 | Harness | 重复 | 检查 | 正常交付 | 执行状态 | 秒 | API | token |
|---|---|---:|---:|---|---|---:|---:|---:|
| durable_inbox | opencode | 1 | 8/8 | 是 | exit=0/stop | 84.02 | 8 | 145,193 |
| durable_inbox | opencode | 2 | 8/8 | 是 | exit=0/stop | 66.28 | 9 | 149,369 |
| durable_inbox | pi | 1 | 8/8 | 是 | exit=0/stop | 69.78 | 10 | 139,994 |
| durable_inbox | pi | 2 | 8/8 | 是 | exit=0/stop | 76.12 | 12 | 181,379 |
| durable_inbox | thinkflow | 1 | 8/8 | 是 | exit=0/completed | 115.91 | 14 | 364,545 |
| durable_inbox | thinkflow | 2 | 8/8 | 是 | exit=0/completed | 81.38 | 10 | 195,234 |
| frame_decoder | opencode | 1 | 8/8 | 是 | exit=0/stop | 100.38 | 15 | 317,507 |
| frame_decoder | opencode | 2 | 8/8 | 是 | exit=0/stop | 63.08 | 11 | 166,741 |
| frame_decoder | pi | 1 | 8/8 | 是 | exit=0/stop | 61.70 | 13 | 162,935 |
| frame_decoder | pi | 2 | 8/8 | 是 | exit=0/stop | 86.22 | 18 | 266,535 |
| frame_decoder | thinkflow | 1 | 8/8 | 是 | exit=0/completed | 184.26 | 19 | 677,938 |
| frame_decoder | thinkflow | 2 | 8/8 | 是 | exit=0/completed | 146.28 | 17 | 543,595 |
| interval_coverage | opencode | 1 | 8/8 | 是 | exit=0/stop | 45.75 | 5 | 58,993 |
| interval_coverage | opencode | 2 | 8/8 | 是 | exit=0/stop | 37.94 | 7 | 78,605 |
| interval_coverage | pi | 1 | 8/8 | 是 | exit=0/stop | 25.97 | 6 | 33,809 |
| interval_coverage | pi | 2 | 8/8 | 是 | exit=0/stop | 34.22 | 7 | 41,256 |
| interval_coverage | thinkflow | 1 | 8/8 | 是 | exit=0/completed | 72.98 | 6 | 95,326 |
| interval_coverage | thinkflow | 2 | 8/8 | 是 | exit=0/completed | 68.59 | 10 | 154,322 |
| pricing_refactor | opencode | 1 | 8/8 | 是 | exit=0/stop | 55.86 | 11 | 151,657 |
| pricing_refactor | opencode | 2 | 8/8 | 是 | exit=0/stop | 59.08 | 10 | 140,659 |
| pricing_refactor | pi | 1 | 8/8 | 是 | exit=0/stop | 69.83 | 11 | 161,872 |
| pricing_refactor | pi | 2 | 8/8 | 是 | exit=0/stop | 55.08 | 10 | 109,438 |
| pricing_refactor | thinkflow | 1 | 8/8 | 是 | exit=0/completed | 136.56 | 11 | 346,067 |
| pricing_refactor | thinkflow | 2 | 8/8 | 是 | exit=0/completed | 105.39 | 7 | 178,674 |
| receipt_package | opencode | 1 | 8/8 | 是 | exit=0/stop | 41.91 | 8 | 84,770 |
| receipt_package | opencode | 2 | 8/8 | 是 | exit=0/stop | 56.77 | 10 | 149,735 |
| receipt_package | pi | 1 | 8/8 | 是 | exit=0/stop | 35.92 | 8 | 52,364 |
| receipt_package | pi | 2 | 8/8 | 是 | exit=0/stop | 45.59 | 9 | 75,939 |
| receipt_package | thinkflow | 1 | 8/8 | 是 | exit=0/completed | 64.31 | 11 | 169,997 |
| receipt_package | thinkflow | 2 | 8/8 | 是 | exit=0/completed | 131.73 | 24 | 635,185 |
| repair_routes | opencode | 1 | 8/8 | 是 | exit=0/stop | 52.05 | 11 | 157,189 |
| repair_routes | opencode | 2 | 8/8 | 是 | exit=0/stop | 55.70 | 10 | 132,138 |
| repair_routes | pi | 1 | 8/8 | 是 | exit=0/stop | 49.02 | 11 | 104,196 |
| repair_routes | pi | 2 | 8/8 | 是 | exit=0/stop | 45.98 | 8 | 67,815 |
| repair_routes | thinkflow | 1 | 8/8 | 是 | exit=0/completed | 77.19 | 10 | 180,114 |
| repair_routes | thinkflow | 2 | 8/8 | 是 | exit=0/completed | 80.02 | 16 | 304,630 |

## 同题同重复且双方都成功的配对

| 对手 | 配对数 | ThinkFlow/对手 总token | 输出token | 用时 |
|---|---:|---:|---:|---:|
| pi | 12 | 2.752 | 2.076 | 1.929 |
| opencode | 12 | 2.220 | 2.667 | 1.759 |

比值是成功配对集合总量之比，不是逐对比值的平均；小于1表示ThinkFlow消耗更少。失败样本仍保留在主表，不能用失败的低消耗包装效率。

## 失败项

本组完整通过；题目仍可能存在难度上限，不能据此宣称真实大仓库任务全部可靠。

## 无额外预算的生产配置

逐请求校验：客户端与转发体均未携带 max_tokens、max_completion_tokens、max_output_tokens；消息哈希一致。没有额外上下文压缩/清理，没有总时限、请求数或续写次数预算。
模型能力信息来自官方 /v1/models：DeepSeek-V4.1-Flash，1,048,576 上下文、393,216 最大输出。这是能力元数据；请求未将它们设置成预算。省略输出字段仍由服务端决定默认输出长度，不代表服务端物理容量无限。

## 范围与复现

- 保留各自原生系统提示与工具协议。自动压缩和历史清理关闭。ThinkFlow使用cmd.exe；Pi/OpenCode使用Git Bash。比较的是这些固定配置的整体harness，不单独归因于流式执行。
- 每次新会话、固定种子交错顺序，题目、代码、版本与参数均在正式执行前冻结；没有按成绩重试或人工修改产物。提供商缓存不能强制清空，缓存量单列。
- 各次运行使用独立工作目录，但位于同一父Git仓库的artifacts下，未做独立Git根或操作系统隔离。已关闭个人配置/自动上下文入口并要求限当前目录；原生项目元信息可能受父Git项目识别影响，不声称完全无父项目上下文。
- 六题、两次重复样本有限；只覆盖标准库软件工程功能与边界，不覆盖前端审美、超大仓库、长期协作、MCP或安全隔离。
- 隐藏验收在独立进程，未向被测模型提供答案；这不是防恶意作弊的操作系统沙箱。
- `results.json`保存全部评分与汇总，`requests.json`保存逐请求原始usage，`solutions.json`以非执行数据保存Python产物，供离线复核；真实凭据与完整模型思考未发布。
- 运行方法与参数见本目录README；`analyze.py`只读取已有结果，不请求模型。
