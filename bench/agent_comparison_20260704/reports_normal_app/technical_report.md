# ThinkFlow vs Claude Code Benchmark Report

生成时间：2026-07-05

## 结论

本轮只比较 Claude Code 与 ThinkFlow，不包含 Pi。四个正式测试单元均通过产物验收，ThinkFlow 的 usage 监控已修复，token 不再是 0。

| task | agent | 验收 | 轮次 | 秒 | API calls | input | output | total | reported cache read | commands | saved calls | cost |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| frontend_app | claude-code | pass | 2 | 871.893 | 34 | 2,631,316 | 32,133 | 2,663,449 | reported 0 / not comparable | n/a | n/a | 13.959905 |
| frontend_app | thinkflow | pass | 1 | 842.187 | 25 | 727,273 | 30,401 | 757,674 | 462,166 | 39 | 37 | n/a |
| novel_five_chapters | claude-code | pass | 2 | 2671.297 | 20 | 510,228 | 15,986 | 526,214 | reported 0 / not comparable | n/a | n/a | 2.95079 |
| novel_five_chapters | thinkflow | pass | 1 | 360.022 | 11 | 187,158 | 23,561 | 210,719 | 152,128 | 1 | 0 | n/a |

## 对比摘要

前端任务中，ThinkFlow 相对 Claude Code：

- API calls：25 vs 34，减少约 26.47%。
- total tokens：757,674 vs 2,663,449，减少约 71.55%。
- input tokens：727,273 vs 2,631,316，减少约 72.36%。
- 用时：842.187s vs 871.893s，略快约 3.41%。

小说任务中，ThinkFlow 相对 Claude Code：

- API calls：11 vs 20，减少 45%。
- total tokens：210,719 vs 526,214，减少约 59.95%。
- input tokens：187,158 vs 510,228，减少约 63.32%。
- 用时：360.022s vs 2671.297s，减少约 86.52%。

## 实验口径

- 两个 agent 使用同一任务 prompt。
- 每个测试单元在独立目录运行。
- 每轮结束后运行独立验收器；验收失败则把失败信息投回同一会话继续。
- 前端验收：必需文件齐全、有组件文件、`npm run build` 成功。
- 小说验收：`README.md` 与 `chapter-01.md` 到 `chapter-05.md` 齐全，每章至少 800 个非空白字符。
- Claude Code prompt 通过 stdin 传入，避免 Windows 命令行长度限制。
- ThinkFlow 使用 `--stream-usage`，并修复了 OpenAI-compatible final usage chunk 被提前丢弃的问题。
- 单轮默认超时为 1800 秒，超时时会杀掉整棵进程树。

## 缓存字段说明

表里的 `reported cache read` 只表示各自工具链上报出来的缓存读取 token，不等于底层一定没有缓存。

- Claude Code 的 JSON `modelUsage` 在本轮四个 turn 中均上报 `cacheReadInputTokens: 0` 和 `cacheCreationInputTokens: 0`。因此报告只能写“Claude Code 本轮上报缓存读取为 0”，不能据此断言服务端完全没有内部缓存。
- ThinkFlow 的缓存字段来自 OpenAI-compatible stream 的 final usage chunk，并写入 session usage。前端任务上报 `462,166` cache read tokens，小说任务上报 `152,128`。
- 所以缓存列不能单独拿来当两者缓存策略优劣的结论；更可比的是 total tokens、input tokens、API calls、产物验收结果和耗时。

## 重要问题与修复

本轮开始前发现三类会污染实验的数据问题：

1. 旧 prompt 文件曾出现 mojibake 乱码，导致模型接收的任务文本不干净。已新增 `clean_prompts.py`，重新生成干净 prompt。
2. ThinkFlow 原先在 OpenAI-compatible 流里遇到 `finish_reason` 就提前结束，没有继续读 final usage chunk。已修复，现在 ThinkFlow token 数据可用。
3. Claude Code 长 prompt 直接塞命令行会触发 Windows `The command line is too long.`。已改为 stdin 输入。

## 产物路径

- frontend_app / claude-code: `bench\agent_comparison_20260704\runs_normal_app\frontend_app\claude-code`
- frontend_app / thinkflow: `bench\agent_comparison_20260704\runs_normal_app\frontend_app\thinkflow`
- novel_five_chapters / claude-code: `bench\agent_comparison_20260704\runs_normal_app\novel_five_chapters\claude-code`
- novel_five_chapters / thinkflow: `bench\agent_comparison_20260704\runs_normal_app\novel_five_chapters\thinkflow`

## 可复现入口

- runner: `bench/agent_comparison_20260704/run_benchmark_normal.py`
- prompt source: `bench/agent_comparison_20260704/clean_prompts.py`
- raw metrics: `bench/agent_comparison_20260704/reports_normal_app/metrics.json`
- summary: `bench/agent_comparison_20260704/reports_normal_app/summary.md`
