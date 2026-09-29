# 2026-09-29 基准差距排查接力

目标：解释 ThinkFlow 正常交付 7/12、用量偏高的具体执行链；监控有代表性的运行，再修有证据的执行器缺陷。原36次报告与产物保留，不用新样本替换旧成绩。

## 单元与验收

1. 只读逐轮诊断：输入正式事件、请求usage及源码；输出故障分类与run/turn证据。两名代理分别核查协议链和失败/高消耗时间线，不修改代码。
2. 过程观测：复用现有计量代理，增加按轮工具输入摘要、成功/失败、请求用量、思考活动量、上下文体积和实时状态。只记录活动量，不保存或显示思考正文；默认不调用模型。先用原生产核心做两个有代表性的真实复现，固定8K参数，不改题目和验收器。
3. 定向修复：优先协议误路由保底、计划回执引用负担、Windows命令契约。每项先有最小失败fixture再改；不降低权限、读取版本检查、未知副作用停止或证据要求。保留独立变更边界，失败可逐项回退。
4. 节点验证：离线边界回归；同配置定向真实运行观察问题是否消失。必要时单独改变输出预算验证8K影响，明确是敏感性样本。独立新上下文审查只覆盖本轮变更和证据。

## 已确认

- `durable_inbox-thinkflow-r2` 原生调用 tf-write/tf-bash、`frame_decoder-thinkflow-r1` 原生调用 tf-edit 会进入未知工具；现有原生保底只由文本解析错误开启，误通道不能触发。
- `receipt_package-thinkflow-r2` 还存在cmd与Unix命令差异及生成测试自身错误，不能全部归因协议。
- 长思考、重复历史、计划引用和缓存的既有证据见原基准diagnosis；尚未证明某条提示或单一机制导致全部用量差距。

## 当前状态

过程观测已实现：trace.jsonl记录关键事件、命令摘要/hash、错误、每轮usage；live.json每秒更新阶段/活动量；父runner每8秒显示真实已完成请求token和待usage数。最终快照强制落盘，运行中不把预置completed当实际结束。监控只留思考字符计数，不留思考正文。

第一批修复已落：已知tf-*名称/标签片段误入原生通道时明确拒绝执行并开放既有保底；length优先于native开始事件，丢弃未结束的原生批次而保留已执行文本操作；续写上限显式未完成；历史unknown回执重放仍立即停；成功回执提供稳定receipt:N、存储canonical id。shell工具描述明确cmd.exe语义，提示不再按行数强制额外流程。

离线恢复6项、短引用11项、既有fallback5项通过；新增恢复组在原实现先复现失败。原parser/core通过；完整176项中175项通过，唯一失败是manifest装置fixture遗漏新增monitor.py，补齐夹具后该项定向通过；桌面12项通过。独立审查补测了完整+截断混合native批次、已成功text不重放和deny边界，并发现监控最终写盘故障可掩盖成功结果的P2，等待真实样本冻结结束后修。

原版新增监控样本 `debug-baseline-pricing/pricing_refactor-thinkflow-r1`：127.281秒、24请求、625,988 token、产物8/8，但max_run_turns。第3轮输出8192触顶；第4轮公共测试通过但测试发现命令因tests不是package失败；第7/9/15/16/18/21轮的native名称为`tf-edit id=`或`tf-write id=`、参数对象为空，均未知工具。第12轮公共及新增测试成功，第13轮仍有不同边界验证，不能全算重复。第19~24轮可见打印cwd/脚本、print(1)、创建再删除placeholder，任务没有正常结束。

第二个原版inbox样本：105.781秒、9请求、153,695 token、8/8但max_consecutive_failures；T7~9连续native tf-write未知，其中一次甚至携带cmd意图。runner旧done中的passed仅代表产物通过，现已同时打印stopped_reason，避免误看成正常交付。

第一批修复后的pricing两次分别7轮83,705token正常结束、24轮625,332token轮数上限；inbox两次24轮503,538token轮数上限、20轮631,902token正常结束，产物均8/8。不能只展示第一条好结果。inbox第一次T10~22可见连续echo/print空转，说明协议修复尚不能解决整体效率。

新假设：每轮原生tool结果后额外追加role=user运行状态，可能影响工具续轮行为。代码与无网络消息结构验证确认当前确有该额外user；官方示例没有此做法，但尚无因果证据。准备只改变状态位置的实验：相同状态文本附入最后已有tool/user内容，保留system、完整reasoning、工具、预算、任务和验收。实验入口status_ablation.py需显式--run，独立目录及额外脚本指纹，不改变生产AgentLoop。

官方DeepSeek约束复核：[Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/)要求带tools请求完整回传历史reasoning_content，包含未调用工具的轮；不能盲删以换低token。thinking模式temperature会被忽略，统一请求temperature=0不意味着输出确定。

真实调用继续使用已有DPAPI凭据和本机计量代理，子进程仅持有本地令牌。产物归 `artifacts/benchmark-20260929/` 的独立新实验目录。

回退：原正式报告及e49b717运行基线不动；新增诊断入口默认inert。每项修复失败时保留日志，只撤回该项新增改动，不覆盖已有工作。
