# ThinkFlow 执行效率 · 当前接力

更新：2026-11-10。分支`codex/thinkflow-efficient-execution`。工程质量首轮`297fbcf`三题实测未通过完整交付（0/3，20请求/258157token）；已追加孤立工具结束标签恢复，收敛可选指引，准备新冻结组复验。历史工程对照继续固定在`40dfa30`：双方各6份完整交付，ThinkFlow34请求/178804token，Pi48请求/至少459820token，总token至少减少61.11%，请求减少29.17%。

## 当前实现

- 可选工程指引包含先找再写、单一权威、契约内实现、有效断言、实际产物与短接力；方法可关闭，协议仍核心注入。具体来源与后续方向见[工程改进记录](engineering-quality-20261110.md)。
- 只有完整合法命令占用id；专用Python使用UTF-8而shell环境保持；压缩边界保留OpenAI与Anthropic工具调用/结果整组。
- `src/prompts.py`分别维护执行协议与推荐工程指引；AgentLoop请求入口默认注入协议，用户自定义或关闭指引均保留协议。原生协议按已注册且实际开放的工具生成。
- CLI的system_prompt/--system-prompt替换推荐指引，--no-system-prompt关闭工程/用户指引；工作区规则独立由context.enabled控制。桌面提供推荐／自定义／关闭，沿用原配置字段，--print-system-prompt显示实际合成结果。
- 默认tf-*连续执行；独立read/bash/python集中声明、整批反馈；显式need_result/confirm、真实失败、取消和unknown保持原边界。
- tf-python复用stdin执行器；首次结果完整保留，精确正文去重，必要reasoning不删改。没有新增验收门禁、batch语言或并行调度器。
- CLI/桌面保留原生开关，配置保存与provider切换指引一致；DeepSeek文本模式显式tool_choice=none。
- 空正文stop及DSML控制开闭标记进入原错误反馈，文件正文/围栏/inline例子不误执行；Pi观察器按最后一轮状态结束，同时保留历史错误事件。

## 结果与验证

- 最终ThinkFlow六份来自go-stream-final-20261110，Pi六份来自go-stream-batch-final-20261110。Pi receipt-r2连接失败后实际恢复完成，旧观察器误标accepted=false；原件保留。1条ConnectError没有usage，所以Pi总量用下限。
- 12份最终实现、最后修改后的公开/附加检查、收尾、原始用量及54份公开源码数据已独立核验。每个对应样本TF总token均更低；恢复成本全部计入。
- 工程质量节点完整parser19/core82/249项unittest通过；独立定向复核拒绝/修正/镜像编号、真实Unicode子进程与shell隔离、两种Anthropic压缩切点及普通用户文本。见[工程审查](reviews/engineering-quality-20261110.md)和[完整日志](evidence/efficiency-20261110/engineering-quality-full.txt)。
- 独立复核关闭/自定义、snapshot恢复、桌面替换Agent、实际工具名单与双provider schema一致；唯一P2已修复并复核。见[分层审查](reviews/prompt-layers-20261110.md)及[完整运行日志](evidence/efficiency-20261110/prompt-layers-full.txt)。本节点不重跑付费效率基准，61.11%仍对应原冻结源码。
- [结果报告](efficiency-results-20261110.md)、[方向评估](efficiency-direction-20261109.md)、[最终审查](reviews/efficiency-final-20261110.md)、[机器计量](evidence/efficiency-20261110/comparison.json)。

## 交付与保留

GitHub交付入口为既有草稿[PR #1](https://github.com/muxueqingze/xuxiang-thinkflow/pull/1)，head为codex/thinkflow-desktop-v06；源码、双语README、PROTOCOL 1.6、架构与实测口径一同更新，CI以PR检查为准。当前功能节点自评通过；v0.8版本级待Pro终审。没有合并整个草稿或发布npm/Release，便携包仍是旧构建。

18次探索任务和6次单请求回放另存[探索证据](evidence/efficiency-20261110/exploration.json)，不替换最终值或重复计费。历史11-09及原六题/前端/小说报告保留。原始workspace、meter、events、harness、manifest留在本机bench/efficiency_20261011/artifacts/供复现；公开源码是[惰性JSON](evidence/efficiency-20261110/workspaces.json)。

全部本轮隔离profile及bytecode已回收，清单在evidence对应cleanup文件；实验与测试进程均结束。baseline/为复用输入；接手前备份仍在.git/thinkflow-pending-20261109.patch及.zip。无新依赖、PATH、Codex登录改动，密钥未进入项目。
