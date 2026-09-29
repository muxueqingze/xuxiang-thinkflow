# ThinkFlow / Pi / OpenCode 同模型基准 · 2026-09-29

同一官方 DeepSeek Flash，高思考；本次6题、3个harness，共36次独立运行。没有使用 Claude Code。
正式套题完整：六题各两次、三个harness。
先看完成质量，再看资源消耗。此小规模本地套题不能推出行业综合排名。

| Harness | 正常交付 | 产物通过 | 检查平均分 | 平均秒 | 中位秒 | 平均API请求 | 总token |
|---|---:|---:|---:|---:|---:|---:|---:|
| thinkflow | 7/12 | 10/12 | 89.6% | 82.00 | 64.05 | 10.17 | 2,540,386 |
| pi | 10/12 | 11/12 | 91.7% | 45.93 | 42.05 | 8.58 | 978,364 |
| opencode | 12/12 | 12/12 | 100.0% | 55.78 | 49.73 | 10.25 | 1,916,744 |

产物通过要求隐藏检查全部通过且公共测试未被修改；正常交付还要求无超时、退出码0，以及观察到正常终态：ThinkFlow completed、Pi最后assistant stop、OpenCode最后step_finish stop。
检查分数仅为断言通过率，不是业务完成百分比。例如缺失CLI也可能通过“非零退出且未覆盖输出”的错误路径检查，因此以整题通过为主判。

## Token 分解

| Harness | 输入（含缓存） | 缓存命中 | 非缓存输入 | 输出（含推理） | 其中推理 | 提早断流请求 |
|---|---:|---:|---:|---:|---:|---:|
| thinkflow | 2,314,905 | 1,950,848 | 364,057 | 225,481 | 150,576 | 2 |
| pi | 860,941 | 815,360 | 45,581 | 117,423 | 68,836 | 0 |
| opencode | 1,805,866 | 1,694,080 | 111,786 | 110,878 | 66,161 | 0 |

总token = 输入 + 输出；推理已经包含在输出中。非缓存输入不是账单价格，缓存也不假定免费。
全部实际请求均有完整输入/输出usage，包括重试和附加模型请求；细分字段缺失时标为未知。用量代理在下游断流后有限drain上游以收取末尾usage；这里是该观测模式的实际消耗，并非直接取消连接的生产成本。

断流请求涉及的完整输出token：thinkflow 5,524、pi 0、opencode 0。这些包含断流前已生成的部分，不能全部当作观测额外开销；当前无法精确切分。

## 每次运行

| 题目 | Harness | 重复 | 检查 | 正常交付 | 执行状态 | 秒 | API | token |
|---|---|---:|---:|---|---|---:|---:|---:|
| durable_inbox | opencode | 1 | 8/8 | 是 | exit=0/stop | 58.12 | 8 | 115,419 |
| durable_inbox | opencode | 2 | 8/8 | 是 | exit=0/stop | 87.95 | 13 | 255,773 |
| durable_inbox | pi | 1 | 8/8 | 是 | exit=0/stop | 58.45 | 14 | 169,494 |
| durable_inbox | pi | 2 | 8/8 | 是 | exit=0/stop | 67.81 | 11 | 124,944 |
| durable_inbox | thinkflow | 1 | 8/8 | 是 | exit=0/completed | 125.56 | 17 | 472,836 |
| durable_inbox | thinkflow | 2 | 0/8 | 否 | exit=0/max_consecutive_failures | 55.50 | 6 | 68,541 |
| frame_decoder | opencode | 1 | 8/8 | 是 | exit=0/stop | 106.81 | 16 | 376,415 |
| frame_decoder | opencode | 2 | 8/8 | 是 | exit=0/stop | 47.66 | 6 | 77,154 |
| frame_decoder | pi | 1 | 8/8 | 是 | exit=0/stop | 57.86 | 10 | 106,846 |
| frame_decoder | pi | 2 | 0/8 | 否 | exit=0/length | 38.25 | 3 | 15,168 |
| frame_decoder | thinkflow | 1 | 8/8 | 否 | exit=0/max_consecutive_failures | 111.70 | 10 | 221,555 |
| frame_decoder | thinkflow | 2 | 8/8 | 是 | exit=0/completed | 59.09 | 6 | 62,304 |
| interval_coverage | opencode | 1 | 8/8 | 是 | exit=0/stop | 31.81 | 7 | 79,426 |
| interval_coverage | opencode | 2 | 8/8 | 是 | exit=0/stop | 35.23 | 9 | 102,532 |
| interval_coverage | pi | 1 | 8/8 | 是 | exit=0/stop | 26.38 | 6 | 35,819 |
| interval_coverage | pi | 2 | 8/8 | 否 | exit=3221226505/toolUse | 17.02 | 4 | 15,651 |
| interval_coverage | thinkflow | 1 | 8/8 | 是 | exit=0/completed | 28.88 | 4 | 23,953 |
| interval_coverage | thinkflow | 2 | 8/8 | 是 | exit=0/completed | 61.31 | 9 | 126,922 |
| pricing_refactor | opencode | 1 | 8/8 | 是 | exit=0/stop | 70.86 | 11 | 208,064 |
| pricing_refactor | opencode | 2 | 8/8 | 是 | exit=0/stop | 50.22 | 8 | 113,239 |
| pricing_refactor | pi | 1 | 8/8 | 是 | exit=0/stop | 65.11 | 10 | 121,334 |
| pricing_refactor | pi | 2 | 8/8 | 是 | exit=0/stop | 62.11 | 9 | 101,014 |
| pricing_refactor | thinkflow | 1 | 8/8 | 是 | exit=0/completed | 174.44 | 19 | 598,055 |
| pricing_refactor | thinkflow | 2 | 8/8 | 否 | exit=0/max_run_turns | 135.23 | 24 | 567,499 |
| receipt_package | opencode | 1 | 8/8 | 是 | exit=0/stop | 31.56 | 8 | 89,063 |
| receipt_package | opencode | 2 | 8/8 | 是 | exit=0/stop | 46.58 | 13 | 163,518 |
| receipt_package | pi | 1 | 8/8 | 是 | exit=0/stop | 34.84 | 8 | 55,625 |
| receipt_package | pi | 2 | 8/8 | 是 | exit=0/stop | 40.72 | 8 | 69,735 |
| receipt_package | thinkflow | 1 | 6/8 | 否 | exit=0/completed | 44.06 | 4 | 35,994 |
| receipt_package | thinkflow | 2 | 8/8 | 否 | exit=0/max_consecutive_failures | 89.36 | 11 | 227,730 |
| repair_routes | opencode | 1 | 8/8 | 是 | exit=0/stop | 53.34 | 10 | 145,488 |
| repair_routes | opencode | 2 | 8/8 | 是 | exit=0/stop | 49.23 | 14 | 190,653 |
| repair_routes | pi | 1 | 8/8 | 是 | exit=0/stop | 39.19 | 9 | 71,768 |
| repair_routes | pi | 2 | 8/8 | 是 | exit=0/stop | 43.38 | 11 | 90,966 |
| repair_routes | thinkflow | 1 | 8/8 | 是 | exit=0/completed | 32.12 | 5 | 47,693 |
| repair_routes | thinkflow | 2 | 8/8 | 是 | exit=0/completed | 66.78 | 7 | 87,304 |

## 同题同重复且双方都成功的配对

| 对手 | 配对数 | ThinkFlow/对手 总token | 输出token | 用时 |
|---|---:|---:|---:|---:|
| pi | 5 | 2.513 | 1.921 | 1.840 |
| opencode | 7 | 1.545 | 2.256 | 1.583 |

比值是成功配对集合总量之比，不是逐对比值的平均；小于1表示ThinkFlow消耗更少。失败样本仍保留在主表，不能用失败的低消耗包装效率。

## 失败项

- `frame_decoder-thinkflow-r1`：观察到的终态 max_consecutive_failures
- `pricing_refactor-thinkflow-r2`：观察到的终态 max_run_turns
- `frame_decoder-pi-r2`：utf8_every_byte: NotImplementedError: Implement incremental decoder；multiple_frames_embedded_delimiters: NotImplementedError: Implement incremental decoder；maximum_zero_and_invalid_constructor: NotImplementedError: Implement incremental decoder；illegal_headers_poison: NotImplementedError: Implement incremental decoder；comma_and_utf8_errors: NotImplementedError: Implement incremental decoder；oversize_rejected_before_payload: NotImplementedError: Implement incremental decoder；eof_closed_and_type_errors: NotImplementedError: Implement incremental decoder；deterministic_arbitrary_chunks: NotImplementedError: Implement incremental decoder；观察到的终态 length
- `durable_inbox-thinkflow-r2`：duplicate_identity_and_conflict: NotImplementedError: Implement durable inbox；fifo_and_running_reservation: NotImplementedError: Implement durable inbox；finish_rules_and_idempotency: NotImplementedError: Implement durable inbox；restart_recovers_without_replay: NotImplementedError: Implement durable inbox；save_failure_preserves_memory_then_retries: NotImplementedError: Implement durable inbox；corrupt_document_preserved: NotImplementedError: Implement durable inbox；payload_validation: NotImplementedError: Implement durable inbox；detached_payloads_and_parent_creation: NotImplementedError: Implement durable inbox；观察到的终态 max_consecutive_failures
- `interval_coverage-pi-r2`：进程退出码 3221226505；观察到的终态 toolUse
- `receipt_package-thinkflow-r1`：package_and_empty: AssertionError: ；cli_csv_utf8_and_quoting: AssertionError:
- `receipt_package-thinkflow-r2`：观察到的终态 max_consecutive_failures

## 提供商输出上限观测

统一请求max_tokens=8192，实际有3个请求的提供商completion_tokens报告超过该值。
- thinkflow：0个请求，涉及0次运行。
- pi：0个请求，涉及0次运行。
- opencode：3个请求，涉及3次运行。

原始usage完整保留，未截断或剔除。原因未确认，不归咎于特定harness；实验统一的是请求参数和本地时间/请求次数预算，不能声称提供商实际输出预算被严格强制相同。

## 范围与复现

- 保留各自原生系统提示、工具协议与压缩策略。ThinkFlow使用cmd.exe；Pi/OpenCode使用Git Bash。比较的是这些固定配置的整体harness，不单独归因于流式执行。
- 每次新会话、固定种子交错顺序，题目、代码、版本与参数均在正式执行前冻结；没有按成绩重试或人工修改产物。提供商缓存不能强制清空，缓存量单列。
- 各次运行使用独立工作目录，但位于同一父Git仓库的artifacts下，未做独立Git根或操作系统隔离。已关闭个人配置/自动上下文入口并要求限当前目录；原生项目元信息可能受父Git项目识别影响，不声称完全无父项目上下文。
- 六题、两次重复样本有限；只覆盖标准库软件工程功能与边界，不覆盖前端审美、超大仓库、长期协作、MCP或安全隔离。
- 隐藏验收在独立进程，未向被测模型提供答案；这不是防恶意作弊的操作系统沙箱。
- `results.json`保存全部评分与汇总，`requests.json`保存逐请求原始usage，`solutions.json`以非执行数据保存Python产物，供离线复核；真实凭据与完整模型思考未发布。
- 运行方法与参数见本目录README；`analyze.py`只读取已有结果，不请求模型。
