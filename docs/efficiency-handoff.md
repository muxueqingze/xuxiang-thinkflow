# ThinkFlow 执行效率 · 当前接力

更新：2026-11-10。当前节点完成原始要求留存、增量摘录与按范围recall。目标是长任务压缩后仍按原要求交付；不增加摘要模型调用，默认工程指引保持精简。分支`codex/thinkflow-efficient-execution`，推送到既有PR head `codex/thinkflow-desktop-v06`。

## 当前成果

- `src/conversation.py`独立记录真实用户要求及request引用；当前执行输入完整保留一次，桌面附件展开与用户原文分开保存。原文与摘要状态随snapshot、fork和配置切换保存。
- `src/compaction.py`沿用结构化摘要条目，只摘录新增历史；两种provider的工具调用/结果保持配对。压缩后默认开放recall，文本/原生共用回取实现，独立信息仍同批反馈。
- 默认tf-*连续执行、tf-python、精确正文去重与可替换工程指引保持。协议由核心注入；用户自定义或关闭工程指引不移除协议。可选方法见[工程示例](prompts/engineering-guidance.md)。
- 本轮实现、复现、保留位置和边界见[长程上下文记录](long-context-20261110.md)，代码入口见[代码地图](代码地图.md)。

## 证据

- 全套parser 19/core 82/unittest 259项通过。独立验收覆盖真实桌面状态链、100次连续压缩、300次混合历史及JSON恢复。原生recall不给正文的P1已修复并按双provider原失败路径复核；之后显式load_tools的过滤问题以同一接口检查复核。见[定向审查](reviews/long-context-20261110.md)。
- Go DS Flash两种恢复场景、旧/新压缩方案共4份产物：独立9项复核由4/9、5/9改善为9/9、9/9。API请求6→5、5→7；token 18,290→≥22,752、15,930→39,496。该探针证明要求保真收益，不更新Pi效率结论。全部源码、用量与原始判题见[证据](evidence/long-context-20261110.json)。
- 历史三题对照固定于`40dfa30`：双方各6份完整交付，ThinkFlow 34请求/178,804token，Pi 48请求/至少459,820token，总token至少减少61.11%。原件与全部探索记录保留，见[效率结果](efficiency-results-20261110.md)、[工程质量记录](engineering-quality-20261110.md)。
- 之前GitHub失败为浅克隆缺基线与两处测试竞态，已修复；`ee48f51`的push/PR CI通过。后续最终提交由CI运行现有完整检查，原因见[CI记录](ci-failures-20261110.md)。

## 交付与保留

[PR #1](https://github.com/muxueqingze/xuxiang-thinkflow/pull/1)已转为正式审查，未合并。npm `xuxiang-agent@0.8.0`已发布`next`通道，GitHub [v0.8.0预发布](https://github.com/muxueqingze/xuxiang-thinkflow/releases/tag/v0.8.0)仍对应`6d85c06`；两者不包含本轮新源码，`latest`仍为0.5.1。发布校验见[记录](evidence/npm-0.8.0.json)，Windows便携包仍是旧构建，v0.8版本级待Pro终审。

本轮每个真实样本和离线产物复核结束即清理临时目录，客户端和测试进程已结束；只保留`bench/long_context_20261110/run.py`、正式回归和docs证据。历史benchmark工作区/计量/事件继续作为复用证据保留在`bench/efficiency_20261011/artifacts/`；原接手备份在`.git/thinkflow-pending-20261109.patch`及`.zip`。无新增依赖、PATH或Codex登录改动，密钥不进入项目。

## 下一步

完整工具输出持久归档与按范围回取：已复现进程8万字符上限丢尾部，以及已反馈账本再次8千字符裁剪。按实际SessionStore归属设计恢复、fork、导出与清理，验证尾部诊断、取消/超时；不要用agent.session_id代替分支存储身份。完整长任务与Pi的新对照在该能力可用后再做。自动ID仍须先解决镜像和合法重复身份，不直接放宽现有去重。
