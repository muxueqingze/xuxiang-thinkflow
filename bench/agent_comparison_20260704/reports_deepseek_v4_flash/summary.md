# Normal Application Same-Session Benchmark

Stop condition: each agent continues in the same session. After every outer turn, an independent validator checks the artifacts. Passing validation stops the cell; otherwise the validation failure is sent back as the next prompt.

| task | agent | artifact_validation | turns | seconds | api_calls | commands | saved_calls | input | output | total | reported_cache_read | cost_usd | files | bytes | stop | notes |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| frontend_app | claude-code | fail | 5 | 361.006 | 30 | n/a | n/a | 629712 | 9471 | 639183 | reported 0 / not comparable | 3.385335 | 23 | 38472 | max_outer_turns | missing src/main.tsx, src/App.tsx, src/utils/metrics.ts, src/styles.css |
| frontend_app | thinkflow | pass | 1 | 392.639 | 22 | 13 | 12 | 381253 | 42663 | 423916 | 348928 | n/a | 32 | 312226 | validation_passed |  |
| novel_five_chapters | claude-code | pass | 1 | 841.005 | 13 | n/a | n/a | 265585 | 9389 | 274974 | reported 0 / not comparable | 1.56265 | 11 | 58293 | validation_passed |  |
| novel_five_chapters | thinkflow | pass | 1 | 180.043 | 6 | 6 | 6 | 95240 | 10336 | 105576 | 63232 | n/a | 12 | 232970 | validation_passed |  |

## Project Paths

- `frontend_app` / `claude-code`: `bench\agent_comparison_20260704\runs_deepseek_v4_flash\frontend_app\claude-code`
- `frontend_app` / `thinkflow`: `bench\agent_comparison_20260704\runs_deepseek_v4_flash\frontend_app\thinkflow`
- `novel_five_chapters` / `claude-code`: `bench\agent_comparison_20260704\runs_deepseek_v4_flash\novel_five_chapters\claude-code`
- `novel_five_chapters` / `thinkflow`: `bench\agent_comparison_20260704\runs_deepseek_v4_flash\novel_five_chapters\thinkflow`
