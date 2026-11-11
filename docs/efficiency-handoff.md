# ThinkFlow 执行效率 · 当前接力

更新：2026-11-11。当前新增Pi式配置分层、无模型配置命令、公共运行时工厂，以及按实际待发输入触发的可选预算。最近的 Desktop / CLI 交互节点已实现全屏设置、真实思考档位、工作区终端、Git 行内差异与文件标签，状态和验证集中在[交互接力](interaction-handoff.md)。目标是可组合的基础设施harness，减少重复请求且保持质量；默认工程指引不扩写。分支`codex/thinkflow-efficient-execution`，推送到既有PR head `codex/thinkflow-desktop-v06`。

本轮两个完整单元独立验收通过。[输出留存](output-recall-20261111.md)覆盖取消、恢复、fork、CLI无自动保存和导出索引；[范围读取](read-ranges-20261111.md)将同版本已观察片段与exact edit接起来，整文件操作沿用全文基线。协议1.9由底层注入，工程方法仍可推荐/自定义/关闭。

最新节点的[配置与SDK](configuration.md)及[独立验收](reviews/configuration-20261111.md)已完成。本地parser/core及291项unittest、桌面15项测试通过；实际Electron完成保存/重启/恢复默认。本轮无付费模型调用，新增预算默认0，旧策略保持。

## 当前成果

- `src/conversation.py`独立记录真实用户要求及request引用；当前执行输入完整保留一次，桌面附件展开与用户原文分开保存。原文与摘要状态随snapshot、fork和配置切换保存。
- `src/compaction.py`沿用结构化摘要条目，只摘录新增历史；两种provider的工具调用/结果保持配对。压缩后默认开放recall，文本/原生共用回取实现，独立信息仍同批反馈。
- `src/output_capture.py`/`output_store.py`保存完整模型可见输出，`recall.py`统一字符范围与字面搜索；持久根使用实际SessionStore，分支原文独立，临时原文随agent关闭清理。
- `src/file_observations.py`分块读取目标行并计算整文件版本，精确编辑仅用实际已读范围；连续编辑维护位置，保留独立read合批与可预测修改不中断。
- `src/configuration.py`处理CLI配置层级与来源，`config_cli.py`按字段稀疏保存；`runtime.py`共享真实配置构造/预览/Agent工厂。桌面14个额外运行项保持平铺保存，不丢常用表单之外的配置。
- `src/context_budget.py`按实际messages/system/tools估算输入；可选触发只做一次本地整理，近期预算保留末条与完整工具批次，reported usage仍独立记录。预算压缩后的账本起点问题已修复并以真实流式写入复核。
- 默认tf-*连续执行、tf-python、精确正文去重与可替换工程指引保持。协议由核心注入；用户自定义或关闭工程指引不移除协议。可选方法见[工程示例](prompts/engineering-guidance.md)。
- 本轮实现、复现、保留位置和边界见[长程上下文记录](long-context-20261110.md)，代码入口见[代码地图](代码地图.md)。

## 证据

- 本轮完整parser/core及277项unittest通过；之后两个范围P2按原失败路径复核。独立验收覆盖输出生命周期及查询、90组读取切片、2,000次连续范围更新。见[输出审查](reviews/output-recall-20261111.md)、[范围审查](reviews/read-ranges-20261111.md)。
- 真实DS一次性输出恢复：仅offset的首次8请求触限、产物正确；补query后3请求正常交付，已报总token由≥68,565变为5,487。范围编辑复用既有定价题，6请求、≥22,583token、独立8/8通过；修改前没有全文重读，后续两次自编检查失败造成回读，完整保留且未为分数重跑。三次实验合计17请求，3次缺usage。
- Go DS Flash两种恢复场景、旧/新压缩方案共4份产物：独立9项复核由4/9、5/9改善为9/9、9/9。API请求6→5、5→7；token 18,290→≥22,752、15,930→39,496。该探针证明要求保真收益，不更新Pi效率结论。全部源码、用量与原始判题见[证据](evidence/long-context-20261110.json)。
- 历史三题对照固定于`40dfa30`：双方各6份完整交付，ThinkFlow 34请求/178,804token，Pi 48请求/至少459,820token，总token至少减少61.11%。原件与全部探索记录保留，见[效率结果](efficiency-results-20261110.md)、[工程质量记录](engineering-quality-20261110.md)。
- 最新输出单元的CI曾因Windows长/短目录别名断言失败；`38fc9a1`改用samefile，push/PR均通过。范围最终提交继续由CI检查，原因及原始链接见[CI记录](ci-failures-20261110.md)。

## 交付与保留

[PR #1](https://github.com/muxueqingze/xuxiang-thinkflow/pull/1)已转为正式审查，未合并。npm `xuxiang-agent@0.8.0`已发布`next`通道，GitHub [v0.8.0预发布](https://github.com/muxueqingze/xuxiang-thinkflow/releases/tag/v0.8.0)仍对应`6d85c06`；两者不包含本轮新源码，`latest`仍为0.5.1。发布校验见[记录](evidence/npm-0.8.0.json)，Windows便携包仍是旧构建，v0.8版本级待Pro终审。

每轮实验结束即清理临时工作区并关闭客户端/进程。保留`bench/output_recovery_20261111/`、`bench/range_edit_20261111/`复现脚本，正式回归与docs证据；前一轮长上下文和历史benchmark材料仍按原用途保留。无新增依赖、PATH或Codex登录改动，密钥不进入项目。

## 下一步

沿[成熟harness方向](harness-direction-20261110.md)继续统一文本及两种原生路径的正文投影；当前读取精确去重主要在OpenAI原生路径，不能把本次可选输入估算写成三种格式已统一。依据实际任务选择预算，再接语义摘要的可选对照。

之后用跨阶段、至少两次压缩、恢复/分支的完整长任务与Pi对照，计入全部失败和恢复。定价探针的两次自编检查失败仅保留了错误而非脚本，具体断言原因未知；复现入口已补完整工具正文，下一轮按证据减少无效检查，不关闭必要验收。语义摘要按总调用与质量作为可选对照；自动ID仍须先解决镜像和合法重复身份。
