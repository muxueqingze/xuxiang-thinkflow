# DeepSeek V4 Flash Benchmark Report

生成时间：2026-07-05

## 结论

本轮把 Claude Code 与 ThinkFlow 都切到 `deepseek-v4-flash`，沿用同一套 prompt、同一套独立验收器、同一套 5 个 outer turns 上限。

结果：ThinkFlow 两个任务都通过；Claude Code 小说任务通过，但前端任务在 5 轮内未通过验收。

| task | agent | 验收 | 轮次 | 秒 | API calls | input | output | total | reported cache read | commands | saved calls | cost |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| frontend_app | claude-code | fail | 5 | 361.006 | 30 | 629,712 | 9,471 | 639,183 | reported 0 / not comparable | n/a | n/a | 3.385335 |
| frontend_app | thinkflow | pass | 1 | 392.639 | 22 | 381,253 | 42,663 | 423,916 | 348,928 | 13 | 12 | n/a |
| novel_five_chapters | claude-code | pass | 1 | 841.005 | 13 | 265,585 | 9,389 | 274,974 | reported 0 / not comparable | n/a | n/a | 1.56265 |
| novel_five_chapters | thinkflow | pass | 1 | 180.043 | 6 | 95,240 | 10,336 | 105,576 | 63,232 | 6 | 6 | n/a |

## 关键观察

- 前端任务：Claude Code 消耗 30 calls / 639,183 tokens 后仍未通过验收，缺 `src/main.tsx`、`src/App.tsx`、`src/utils/metrics.ts`、`src/styles.css`、`README.md`。ThinkFlow 22 calls / 423,916 tokens 通过。
- 小说任务：两者都通过。ThinkFlow 相比 Claude Code API calls 减少约 53.85%，total tokens 减少约 61.61%，耗时减少约 78.59%。
- Claude Code 在 deepseek-v4-flash 下依旧上报 `cacheReadInputTokens: 0` / `cacheCreationInputTokens: 0`。这仍然是“上报值”，不适合作为和 ThinkFlow `cached_tokens` 的直接比较。

## 实验口径

- runner: `bench/agent_comparison_20260704/run_benchmark_normal.py`
- model: `deepseek-v4-flash`
- runs dir: `bench/agent_comparison_20260704/runs_deepseek_v4_flash`
- reports dir: `bench/agent_comparison_20260704/reports_deepseek_v4_flash`
- 前端验收：必需文件齐全、有组件文件、`npm run build` 成功。
- 小说验收：`README.md` 与 `chapter-01.md` 到 `chapter-05.md` 齐全，每章至少 800 个非空白字符。
- Claude Code prompt 通过 stdin 传入，避免 Windows 命令行长度限制。
- ThinkFlow 使用 `--stream-usage` 读取 final usage chunk。

## 产物路径

- frontend_app / claude-code: `bench\agent_comparison_20260704\runs_deepseek_v4_flash\frontend_app\claude-code`
- frontend_app / thinkflow: `bench\agent_comparison_20260704\runs_deepseek_v4_flash\frontend_app\thinkflow`
- novel_five_chapters / claude-code: `bench\agent_comparison_20260704\runs_deepseek_v4_flash\novel_five_chapters\claude-code`
- novel_five_chapters / thinkflow: `bench\agent_comparison_20260704\runs_deepseek_v4_flash\novel_five_chapters\thinkflow`
