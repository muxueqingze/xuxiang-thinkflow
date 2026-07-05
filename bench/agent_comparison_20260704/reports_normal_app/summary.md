# Normal Application Same-Session Benchmark

Stop condition: each agent continues in the same session. After every outer turn, an independent validator checks the artifacts. Passing validation stops the cell; otherwise the validation failure is sent back as the next prompt.

| task | agent | artifact_validation | turns | seconds | api_calls | commands | saved_calls | input | output | total | reported_cache_read | cost_usd | files | bytes | stop | notes |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| frontend_app | claude-code | pass | 2 | 871.893 | 34 | n/a | n/a | 2631316 | 32133 | 2663449 | reported 0 / not comparable | 13.959905 | 35 | 134352 | validation_passed |  |
| frontend_app | thinkflow | pass | 1 | 842.187 | 25 | 39 | 37 | 727273 | 30401 | 757674 | 462166 | n/a | 32 | 425789 | validation_passed |  |
| novel_five_chapters | claude-code | pass | 2 | 2671.297 | 20 | n/a | n/a | 510228 | 15986 | 526214 | reported 0 / not comparable | 2.95079 | 14 | 82210 | validation_passed |  |
| novel_five_chapters | thinkflow | pass | 1 | 360.022 | 11 | 1 | 0 | 187158 | 23561 | 210719 | 152128 | n/a | 12 | 258503 | validation_passed |  |

## Project Paths

- `frontend_app` / `claude-code`: `bench\agent_comparison_20260704\runs_normal_app\frontend_app\claude-code`
- `frontend_app` / `thinkflow`: `bench\agent_comparison_20260704\runs_normal_app\frontend_app\thinkflow`
- `novel_five_chapters` / `claude-code`: `bench\agent_comparison_20260704\runs_normal_app\novel_five_chapters\claude-code`
- `novel_five_chapters` / `thinkflow`: `bench\agent_comparison_20260704\runs_normal_app\novel_five_chapters\thinkflow`
