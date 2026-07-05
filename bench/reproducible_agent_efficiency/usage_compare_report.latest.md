# TK vs CC Same Prompt Usage Report

| Metric | ThinkFlow/TK | Claude Code/CC |
|---|---:|---:|
| API calls | 7 | 104 |
| Prompt tokens | 124,515 | 2,246,442 |
| Cached tokens | 92,672 | 2,231,040 |
| Effective input | 31,843 | 15,402 |
| Completion tokens | 33,344 | 61,155 |
| Total tokens | 157,859 | 2,307,597 |
| Cache hit ratio | 74.43% | 99.31% |

## Paths
- TK output dir: `bench_compare_latest/tk`
- CC requested output dir: `bench_compare_latest/cc`
- CC actual output dir: `<external-workspace>/agent-efficiency-lab`

## Build
- TK requested dir exists: `True`, build passed: `True`
- CC requested dir exists: `False`
- CC actual dir exists: `True`, build passed: `True`

## Notes
- CC usage was captured through the 8765 Anthropic-to-OpenAI bridge after confirming bridge logging with a smoke request.
- CC did not follow the requested output directory in this run; it created `<external-workspace>/agent-efficiency-lab` instead of `bench_compare_latest/cc`.
- Because CC output path did not match the task constraint, usage is comparable as a cost sample, but delivery compliance is not equivalent.
