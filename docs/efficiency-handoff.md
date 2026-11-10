# ThinkFlow 执行效率 · 当前接力

更新：2026-11-10。当前节点完成原始要求留存、增量摘录与按范围recall。目标是长任务压缩后仍按原要求交付；不增加摘要模型调用，默认工程指引保持精简。分支`codex/thinkflow-efficient-execution`，推送到既有PR head `codex/thinkflow-desktop-v06`。

本轮节点（2026-11-11）：完整工具输出留存、短receipt引用、恢复/fork与CLI无自动保存生命周期已完成；独立节点及query增量验收通过。真实DS首次只按offset恢复，用8请求触限、产物正确；补字面query后同场景3请求正常交付，总token由至少68,565变为5,487。两轮均保留，见[输出留存与证据](output-recall-20261111.md)。范围read与精确edit的同SHA观察覆盖设计已盘点，后续再落地。

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

已完成[成熟harness参照与方向](harness-direction-20261110.md)：核对Pi 1.0.1/固定上游提交、当前OpenCode及Anthropic官方资料。无付费调用的本地探针确认Pi保留输出尾部和完整日志，范围读取返回307字符而当前ThinkFlow返回128,993字符；这是组件行为，不是任务token节省比例。

顺序：完整输出归档与会话引用 → 范围读取及编辑基线契约 → 文本/原生统一投影与实际上下文预算 → 完整长任务与Pi对照。先解决进程8万字符丢尾和账本8千字符裁剪，归属使用实际SessionStore；恢复、fork和清理一起设计。语义摘要保留为按总调用与质量判定的可选对照，不机械排除必要的一次调用。自动ID仍须先解决镜像和合法重复身份。
