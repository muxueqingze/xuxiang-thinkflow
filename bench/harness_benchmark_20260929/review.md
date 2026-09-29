# 定向判分与状态验收 · 2026-09-29

本文记录两阶段验收：先在实验进行中检查三个指定样本，随后在全部 36 次完成后核对最终汇总，最终状态见文末。它不是产品核心的全面审查。没有再次调用模型，没有改动被冻结的运行代码、题目或正式结果，没有重跑替换样本。

## 本次结论

未发现三个指定样本的评分或状态误判。失败样本应保留原样，产物正确和正常结束应分开报告。

| 样本 | 原记录 | 独立核验 | 正常交付 |
|---|---|---|---|
| `receipt_package-thinkflow-r1` | 隐藏检查 6/8，exit 0，completed | 对现有产物调用 `tasks.grade`，全部判分明细与原记录一致 | 否：缺少 CLI 产物 |
| `durable_inbox-thinkflow-r2` | 隐藏检查 0/8，exit 0，max_consecutive_failures | 对现有产物调用 `tasks.grade`，全部判分明细与原记录一致 | 否：实现仍为桩代码，且停止原因异常 |
| `interval_coverage-pi-r2` | 隐藏检查 8/8，exit 3221226505，无超时 | 只核对已有评分、退出记录及公开终态元数据，没有重跑该题隐藏检查 | 否：非零退出，最后 assistant 终态仍为 toolUse |

## 直接证据

### Receipt：遗漏了 cli.py

`artifacts/benchmark-20260929/formal/receipt_package-thinkflow-r1/workspace/receipt_tools/` 只有 `model.py` 和 `__init__.py`，缺少题目明确要求的 `cli.py`。已实际阅读两个现存模块，聚合实现和导出存在。

- `package_and_empty` 同时检查三个包文件是否存在；缺少 `cli.py` 足以解释失败。
- `cli_csv_utf8_and_quoting` 要求 `python -m receipt_tools.cli` 成功生成正确 CSV；入口不存在，失败成立。
- 另外两组 CLI 错误路径检查通过并不能证明 CLI 实现存在：缺失模块本身也会产生非零退出并保留输出。这没有改变整题失败判定；报告不能把 6/8 解读为 CLI 已部分实现。

### Inbox：构造器仍然抛出 NotImplementedError

`artifacts/benchmark-20260929/formal/durable_inbox-thinkflow-r2/workspace/inbox.py` 的构造器保存 path 后直接抛出 `NotImplementedError("Implement durable inbox")`，其余四个方法也仍为桩代码。八条隐藏检查均在建立 Inbox 时失败，0/8 与实物一致。

`result.json` 同时记录 `stopped_reason=max_consecutive_failures`。进程 exit 0 仅表示包装程序正常退出，不能替代任务交付成功。

### Pi coverage：产物通过，执行没有正常收尾

`artifacts/benchmark-20260929/formal/interval_coverage-pi-r2/result.json` 记录产物 8/8、`timed_out=false`、`exit_code=3221226505`（十六进制 `0xC0000409`）。本轮不推断此退出码的具体根因。

只解析 stdout JSONL 的事件类型及终态元数据，没有摘录模型内部思考。最后一个 assistant `message_end` 位于 `stdout.jsonl` 第 3295 行，`stopReason=toolUse`，随后还有 toolResult；没有观察到 assistant 的正常 `stop` 终态。

因此应同时保留“产物验收通过”和“未正常交付”两个事实，不能丢弃该次运行，也不能将它合并为正常成功的效率配对。

## 三种 harness 的终态口径

已检查 `analyze.py::terminal_state` 及其与 `delivery_passed` 的组合：

- ThinkFlow：`stopped_reason=completed`。
- Pi：最后一个公开 assistant `message_end` 为 `stopReason=stop`，且无该消息错误。
- OpenCode：最后一个公开 `step_finish` 为 `reason=stop`，且没有后续公开 error 替代该状态。
- 三者统一再要求产物通过、退出码 0、未超时。

该口径以各自原生公开事件确认结束，比仅检查进程退出码更合适。本次三个样本未发现误判。它仍属于外部可观测状态核对，不宣称能证明 harness 内部没有未报告错误。

## max_tokens 的观测边界

已读取 `artifacts/benchmark-20260929/offline-parameters/parameters.json`：两个捕获的上游请求体均为 `max_tokens=8192`、`thinking.type=enabled`、`reasoning_effort=high`、`temperature=0`。对应 `pi/meter.json` 和 `opencode/meter.json` 各记录一次成功的本地 fixture 请求。这是已有离线参数证据，本轮未发送新的请求。

此前已直接核对正式 `frame_decoder-opencode-r1` 第 3 个请求：提供商报告 `completion_tokens=11817`，其中 `reasoning_tokens=11703`，`finish_reason=tool_calls`，请求 HTTP 200 且完整结束。原始用量不应截断到 8192，也不应为此删除样本。

可支持的表述是“统一请求 max_tokens=8192”；不能将其称作本次提供商实际遵守的输出硬上限。原因尚未确认，不归咎于某个 harness。全部超限观测应保留并统一报告；结论是同请求参数及共同本地预算下的实测，不是严格相同实际计算量下的因果比较。

## 隔离与结论范围

每次运行具有独立工作目录，但目录位于同一父 Git 仓库的 `artifacts/` 下，并非独立 Git 根或操作系统沙箱。自动个人配置和上下文入口已按实验配置禁用；不得将这一点扩大成完全没有父项目元信息的保证。尚无本次核验证据证明发生了跨目录上下文污染。

最终汇总核对如下；未重复上面已经完成的两个样本重评分。

## 最终汇总验收：完整报告可交付

正式 36 次运行完成后，独立读取 `reports/results.json`、`reports/requests.json`、`reports/summary.md`，并与正式目录中的 plan、逐次 result、逐次 meter 对照。未调用报告器的汇总函数，直接从每条原始 usage 重新相加。

核对通过：

- 恰好 36 个唯一运行 ID，且为六题 × 三个 harness × 两次重复的完整组合；每个 harness 恰好 12 次。
- 发布的逐次结果保留正式 result 的所有字段及原值；发布的 meter 与对应正式 meter 完全相同。没有跳过低分、异常退出或异常终态样本。
- 共 **348 个实际请求**，ThinkFlow 122、Pi 103、OpenCode 123。各运行请求序号连续且无重复；全部 HTTP 200、finished，无 transport_error，输入、输出、缓存与推理用量均完整。
- 每条提供商 `total_tokens` 均等于输入加输出；缓存包含在输入中，推理包含在输出中，没有额外相加。
- 正常交付状态重新对照公开终态及进程结果，与最终报告一致。七次未正常交付全部保留在逐次表和失败项中。

| Harness | 正常交付 | 产物通过 | 请求数 | 总 token | 平均秒 |
|---|---:|---:|---:|---:|---:|
| ThinkFlow | 7/12 | 10/12 | 122 | 2,540,386 | 82.00375 |
| Pi | 10/12 | 11/12 | 103 | 978,364 | 45.926 |
| OpenCode | 12/12 | 12/12 | 123 | 1,916,744 | 55.782833333333336 |

全体总用量为 **5,435,494 token**。ThinkFlow 对 Pi 的正常成功配对为 5 对，总 token 比 2.513054246078209、用时比 1.839906064920151；对 OpenCode 为 7 对，总 token 比 1.5445862576409328、用时比 1.5831292524865135。配对条件及集合求和后相除的结果均与报告一致。

三个超过请求 max_tokens 的观测均保留：`frame_decoder-opencode-r1` 第 3 请求 11,817 输出、`durable_inbox-opencode-r2` 第 4 请求 9,352 输出、`pricing_refactor-opencode-r1` 第 3 请求 9,017 输出。报告对请求参数、实际提供商用量、断流观测模式和目录隔离边界的限定正确，没有将此实验包装成严格等实际计算量或行业综合排名。

### 单个已发布成功产物的复验

实际阅读 `solutions.json` 中 `repair_routes-opencode-r1` 的实现，再执行：

```text
python -B bench/harness_benchmark_20260929/regrade.py repair_routes-opencode-r1
```

工具从非执行 JSON 数据恢复到临时目录，并核对题目验收器与冻结 hash 一致，返回 **8/8，通过**。该结果证明本次抽查的已发布成功产物可离线复核；没有重跑其余成功样本或全套 288 组检查，临时目录由工具退出时清理。

本次最终核对没有发现阻塞交付的问题。完整本地报告可交付；结论必须保留当前文档中的样本规模、原生配置、失败状态、缓存、断流计量、输出超限和隔离边界。生产核心未在本轮审查中修改，本文件不构成生产核心全面验收。
