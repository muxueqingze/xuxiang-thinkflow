# 过程监控与定向复测 · 2026-09-29

所有样本保留；原36次成绩不变。这是缺陷诊断与候选方案消融，不是三harness重新排名。

| 阶段 | 题目/重复 | 正常交付 | 产物 | 停止原因 | 请求 | 秒 | token |
|---|---|---:|---:|---|---:|---:|---:|
| 原版 | pricing_refactor / 1 | 否 | 8/8 | max_run_turns | 24 | 127.28 | 625,988 |
| 原版 | durable_inbox / 1 | 否 | 8/8 | max_consecutive_failures | 9 | 105.78 | 153,695 |
| 协议修复 | pricing_refactor / 1 | 是 | 8/8 | completed | 7 | 63.31 | 83,705 |
| 协议修复 | pricing_refactor / 2 | 否 | 8/8 | max_run_turns | 24 | 131.80 | 625,332 |
| 协议修复 | durable_inbox / 1 | 否 | 8/8 | max_run_turns | 24 | 93.38 | 503,538 |
| 协议修复 | durable_inbox / 2 | 是 | 8/8 | completed | 20 | 133.34 | 631,902 |
| 协议修复 | frame_decoder / 1 | 是 | 8/8 | completed | 21 | 183.03 | 547,772 |
| 协议修复 | receipt_package / 1 | 是 | 8/8 | completed | 14 | 129.88 | 298,291 |
| 状态位置消融 | pricing_refactor / 1 | 否 | 8/8 | max_run_turns | 24 | 136.22 | 585,744 |
| 状态位置消融 | pricing_refactor / 2 | 否 | 8/8 | max_run_turns | 24 | 97.97 | 459,548 |
| 状态位置消融 | durable_inbox / 1 | 是 | 8/8 | completed | 10 | 96.75 | 192,598 |
| 状态位置消融 | durable_inbox / 2 | 否 | 8/8 | max_run_turns | 24 | 92.30 | 478,992 |
| 开放原生文件工具 | pricing_refactor / 1 | 是 | 8/8 | completed | 4 | 51.83 | 43,136 |
| 开放原生文件工具 | pricing_refactor / 2 | 是 | 8/8 | completed | 19 | 138.48 | 564,981 |
| 开放原生文件工具 | durable_inbox / 1 | 是 | 8/8 | completed | 12 | 112.20 | 251,615 |
| 开放原生文件工具 | durable_inbox / 2 | 是 | 8/8 | completed | 9 | 87.52 | 154,324 |

## 同题同次数比较

仅比较pricing_refactor、durable_inbox各两次，frame/receipt不混入。正常交付沿用原口径：产物全过、公共测试未修改、进程成功、无超时、completed且无last_error。它不额外保证模型主动执行了要求的测试。

| 方案 | 正常交付 | 请求 | token | 流关闭前成功文件操作 |
|---|---:|---:|---:|---:|
| 协议修复 | 2/4 | 75 | 1,844,477 | 15 |
| 状态位置消融 | 1/4 | 82 | 1,716,882 | 11 |
| 开放原生文件工具 | 4/4 | 44 | 1,014,056 | 14 |

状态位置消融保留相同状态文本、工具结果、全部reasoning及原提示，只在末条为字符串tool/user时附入该消息；其他形状回退为新增user消息。它没有改善正常收尾，未进入生产，不能认定额外user状态是主要原因。

原生文件工具可见实验只改变首轮schemas：流式仍由原提示优先引导，原生保底始终可用，显式enable/allow/deny不变。对应四次总token比协议修复组少45.0%，这是观察值，不能当作稳定收益或三harness新排名。样本数少、运行时段/缓存不同，且thinking模式的temperature=0不保证确定性。该可用性改动已采纳。

**验证缺口：** 开放原生工具的pricing第1次虽然外部grader 8/8、正常结束，却没有bash调用，也就没有模型主动执行测试的证据。第2次仍花19请求、564,981 token。四次保留14次与输出重叠的流式文件操作（含edit），不能据此声称整体效率问题全部解决。

## 完整用量与来源

本轮诊断合计16次、269请求、6,201,161 token。
输入5,814,070（缓存5,160,832、非缓存653,238），输出387,091，其中推理224,513已计入输出；269/269请求有usage、0个HTTP失败，8次客户端断流通过有限读取取得usage。

统一Flash high、请求8K、300秒/24请求；任务/隐藏验收未改。总token包含缓存输入，不等于现金费用。逐请求记录为权威，运行中的活动字符数不是token估算。

原版核心为e49b717；协议修复与消融的源码指纹在完整记录的experiments/manifest内，消融另含脚本指纹。0f02aaa保留采纳可见性改动之前的修复核心；当前HEAD已采纳可见性，直接在当前HEAD运行message/attached不再等同于历史隐藏schema对照。复现实验必须先恢复对应源码与脚本指纹，使用新的实验目录；报告导出不调用模型。

[诊断与修复说明](../../../docs/benchmark-debug-20260929.md) · [完整逐请求/逐轮记录](recovery-results.json)
