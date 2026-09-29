# ThinkFlow / Pi / OpenCode 同模型基准 · 2026-09-29

同一官方 DeepSeek Flash，高思考；本次6题、3个harness，共36次独立运行。没有使用 Claude Code。
正式套题完整：六题各两次、三个harness。
先看完成质量，再看资源消耗。此小规模本地套题不能推出行业综合排名。

| Harness | 正常交付 | 产物通过 | 检查平均分 | 平均秒 | 中位秒 | 平均API请求 | 总token |
|---|---:|---:|---:|---:|---:|---:|---:|
| thinkflow | 12/12 | 12/12 | 100.0% | 76.89 | 74.25 | 9.00 | 2,123,456 |
| pi | 10/12 | 10/12 | 86.5% | 49.33 | 43.20 | 9.50 | 1,264,618 |
| opencode | 12/12 | 12/12 | 100.0% | 60.37 | 55.87 | 10.00 | 1,898,716 |

产物通过要求隐藏检查全部通过且公共测试未被修改；正常交付还要求无超时、退出码0，以及观察到正常终态：ThinkFlow completed、Pi最后assistant stop、OpenCode最后step_finish stop。
该计分口径不额外确认模型主动运行了任务要求的测试，也不等同于完整遵守工作流程；实际测试执行与结果证据须另看过程复核。
检查分数仅为断言通过率，不是业务完成百分比。例如缺失CLI也可能通过“非零退出且未覆盖输出”的错误路径检查，因此以整题通过为主判。

## Token 分解

| Harness | 输入（含缓存） | 缓存命中 | 非缓存输入 | 输出（含推理） | 其中推理 | 提早断流请求 |
|---|---:|---:|---:|---:|---:|---:|
| thinkflow | 1,918,537 | 1,624,448 | 294,089 | 204,919 | 125,495 | 0 |
| pi | 1,139,247 | 1,087,488 | 51,759 | 125,371 | 73,973 | 0 |
| opencode | 1,781,451 | 1,670,016 | 111,435 | 117,265 | 70,578 | 0 |

总token = 输入 + 输出；推理已经包含在输出中。非缓存输入不是账单价格，缓存也不假定免费。
全部实际请求均有完整输入/输出usage，包括重试和附加模型请求；细分字段缺失时标为未知。用量代理在下游断流后有限drain上游以收取末尾usage；这里是该观测模式的实际消耗，并非直接取消连接的生产成本。

断流请求涉及的完整输出token：thinkflow 0、pi 0、opencode 0。这些包含断流前已生成的部分，不能全部当作观测额外开销；当前无法精确切分。

## 每次运行

| 题目 | Harness | 重复 | 检查 | 正常交付 | 执行状态 | 秒 | API | token |
|---|---|---:|---:|---|---|---:|---:|---:|
| durable_inbox | opencode | 1 | 8/8 | 是 | exit=0/stop | 56.38 | 7 | 108,483 |
| durable_inbox | opencode | 2 | 8/8 | 是 | exit=0/stop | 82.77 | 14 | 264,342 |
| durable_inbox | pi | 1 | 0/8 | 否 | exit=0/length | 36.91 | 3 | 15,216 |
| durable_inbox | pi | 2 | 8/8 | 是 | exit=0/stop | 99.05 | 22 | 425,331 |
| durable_inbox | thinkflow | 1 | 8/8 | 是 | exit=0/completed | 119.33 | 16 | 387,318 |
| durable_inbox | thinkflow | 2 | 8/8 | 是 | exit=0/completed | 89.36 | 12 | 251,672 |
| frame_decoder | opencode | 1 | 8/8 | 是 | exit=0/stop | 83.44 | 11 | 216,433 |
| frame_decoder | opencode | 2 | 8/8 | 是 | exit=0/stop | 84.34 | 13 | 231,303 |
| frame_decoder | pi | 1 | 8/8 | 是 | exit=0/stop | 62.36 | 10 | 110,359 |
| frame_decoder | pi | 2 | 8/8 | 是 | exit=0/stop | 72.84 | 14 | 197,762 |
| frame_decoder | thinkflow | 1 | 8/8 | 是 | exit=0/completed | 111.59 | 13 | 329,284 |
| frame_decoder | thinkflow | 2 | 8/8 | 是 | exit=0/completed | 151.23 | 16 | 413,557 |
| interval_coverage | opencode | 1 | 8/8 | 是 | exit=0/stop | 31.34 | 7 | 75,233 |
| interval_coverage | opencode | 2 | 8/8 | 是 | exit=0/stop | 44.00 | 7 | 75,918 |
| interval_coverage | pi | 1 | 8/8 | 是 | exit=0/stop | 24.88 | 5 | 26,665 |
| interval_coverage | pi | 2 | 8/8 | 是 | exit=0/stop | 30.73 | 8 | 44,258 |
| interval_coverage | thinkflow | 1 | 8/8 | 是 | exit=0/completed | 37.27 | 6 | 56,611 |
| interval_coverage | thinkflow | 2 | 8/8 | 是 | exit=0/completed | 60.27 | 7 | 93,159 |
| pricing_refactor | opencode | 1 | 8/8 | 是 | exit=0/stop | 66.62 | 15 | 234,560 |
| pricing_refactor | opencode | 2 | 8/8 | 是 | exit=0/stop | 91.17 | 11 | 217,613 |
| pricing_refactor | pi | 1 | 3/8 | 否 | exit=0/length | 38.70 | 3 | 15,938 |
| pricing_refactor | pi | 2 | 8/8 | 是 | exit=0/stop | 53.36 | 8 | 81,387 |
| pricing_refactor | thinkflow | 1 | 8/8 | 是 | exit=0/completed | 91.34 | 11 | 252,978 |
| pricing_refactor | thinkflow | 2 | 8/8 | 是 | exit=0/completed | 86.80 | 7 | 107,128 |
| receipt_package | opencode | 1 | 8/8 | 是 | exit=0/stop | 55.36 | 10 | 137,958 |
| receipt_package | opencode | 2 | 8/8 | 是 | exit=0/stop | 29.38 | 5 | 56,696 |
| receipt_package | pi | 1 | 8/8 | 是 | exit=0/stop | 38.25 | 9 | 70,963 |
| receipt_package | pi | 2 | 8/8 | 是 | exit=0/stop | 38.53 | 11 | 84,802 |
| receipt_package | thinkflow | 1 | 8/8 | 是 | exit=0/completed | 61.70 | 8 | 127,210 |
| receipt_package | thinkflow | 2 | 8/8 | 是 | exit=0/completed | 25.94 | 3 | 20,997 |
| repair_routes | opencode | 1 | 8/8 | 是 | exit=0/stop | 53.84 | 11 | 153,813 |
| repair_routes | opencode | 2 | 8/8 | 是 | exit=0/stop | 45.81 | 9 | 126,364 |
| repair_routes | pi | 1 | 8/8 | 是 | exit=0/stop | 48.61 | 11 | 98,482 |
| repair_routes | pi | 2 | 8/8 | 是 | exit=0/stop | 47.69 | 10 | 93,455 |
| repair_routes | thinkflow | 1 | 8/8 | 是 | exit=0/completed | 39.17 | 5 | 41,863 |
| repair_routes | thinkflow | 2 | 8/8 | 是 | exit=0/completed | 48.62 | 4 | 41,679 |

## 同题同重复且双方都成功的配对

| 对手 | 配对数 | ThinkFlow/对手 总token | 输出token | 用时 |
|---|---:|---:|---:|---:|
| pi | 10 | 1.202 | 1.448 | 1.379 |
| opencode | 12 | 1.118 | 1.747 | 1.274 |

比值是成功配对集合总量之比，不是逐对比值的平均；小于1表示ThinkFlow消耗更少。失败样本仍保留在主表，不能用失败的低消耗包装效率。

## 失败项

- `durable_inbox-pi-r1`：duplicate_identity_and_conflict: NotImplementedError: Implement durable inbox；fifo_and_running_reservation: NotImplementedError: Implement durable inbox；finish_rules_and_idempotency: NotImplementedError: Implement durable inbox；restart_recovers_without_replay: NotImplementedError: Implement durable inbox；save_failure_preserves_memory_then_retries: NotImplementedError: Implement durable inbox；corrupt_document_preserved: NotImplementedError: Implement durable inbox；payload_validation: NotImplementedError: Implement durable inbox；detached_payloads_and_parent_creation: NotImplementedError: Implement durable inbox；观察到的终态 length
- `pricing_refactor-pi-r1`：decimal_discount_and_bulk: ModuleNotFoundError: No module named 'pricing'；report_aggregation_order_and_zero: AssertionError: ；monetary_validation: ModuleNotFoundError: No module named 'pricing'；shared_runtime_dependency: ModuleNotFoundError: No module named 'pricing'；deterministic_money_cases: ModuleNotFoundError: No module named 'pricing'；观察到的终态 length

## 提供商输出上限观测

统一请求max_tokens=8192，实际有2个请求的提供商completion_tokens报告超过该值。
- thinkflow：0个请求，涉及0次运行。
- pi：0个请求，涉及0次运行。
- opencode：2个请求，涉及2次运行。

原始usage完整保留，未截断或剔除。原因未确认，不归咎于特定harness；实验统一的是请求参数和本地时间/请求次数预算，不能声称提供商实际输出预算被严格强制相同。

## 范围与复现

- 保留各自原生系统提示、工具协议与压缩策略。ThinkFlow使用cmd.exe；Pi/OpenCode使用Git Bash。比较的是这些固定配置的整体harness，不单独归因于流式执行。
- 每次新会话、固定种子交错顺序，题目、代码、版本与参数均在正式执行前冻结；没有按成绩重试或人工修改产物。提供商缓存不能强制清空，缓存量单列。
- 各次运行使用独立工作目录，但位于同一父Git仓库的artifacts下，未做独立Git根或操作系统隔离。已关闭个人配置/自动上下文入口并要求限当前目录；原生项目元信息可能受父Git项目识别影响，不声称完全无父项目上下文。
- 六题、两次重复样本有限；只覆盖标准库软件工程功能与边界，不覆盖前端审美、超大仓库、长期协作、MCP或安全隔离。
- 隐藏验收在独立进程，未向被测模型提供答案；这不是防恶意作弊的操作系统沙箱。
- `results.json`保存全部评分与汇总，`requests.json`保存逐请求原始usage，`solutions.json`以非执行数据保存Python产物，供离线复核；真实凭据与完整模型思考未发布。
- 运行方法与参数见本目录README；`analyze.py`只读取已有结果，不请求模型。
