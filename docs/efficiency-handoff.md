# ThinkFlow 执行效率 · 当前接力

更新：2026-11-10。分支`codex/thinkflow-efficient-execution`。工程质量节点已收口：保留四处确定的harness修复；新增扩展指引未证明稳定收益，默认恢复5fdffac精简正文，扩展方法留作可选示例/skill。历史工程对照继续固定在`40dfa30`：双方各6份完整交付，ThinkFlow34请求/178804token，Pi48请求/至少459820token，总token至少减少61.11%，请求减少29.17%。

## 当前实现

- 扩展工程原则包括先找再写、单一权威、契约内实现、有效断言、实际产物与短接力，保留为[可选示例](prompts/engineering-guidance.md)和skill；默认保持精简，协议仍核心注入。具体来源及全部试跑见[工程改进记录](engineering-quality-20261110.md)。
- 只有完整合法命令占用id；专用Python使用UTF-8而shell环境保持；压缩保留两种API工具调用/结果整组；孤立tf结束标记进入原协议恢复链，成功操作不重放。
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
- 孤立closer追加后37项定向与独立CLI/headless复核通过，63ca6c5的push/PR CI通过。两轮真实试跑共42请求/415705token：首组0/3完整交付；第二组生产判题3/3、完整交付1/3。所有样本和成本保留，不据此宣称新增指引节省或提高质量。
- 独立复核关闭/自定义、snapshot恢复、桌面替换Agent、实际工具名单与双provider schema一致；唯一P2已修复并复核。见[分层审查](reviews/prompt-layers-20261110.md)及[完整运行日志](evidence/efficiency-20261110/prompt-layers-full.txt)。本节点不重跑付费效率基准，61.11%仍对应原冻结源码。
- [结果报告](efficiency-results-20261110.md)、[方向评估](efficiency-direction-20261109.md)、[最终审查](reviews/efficiency-final-20261110.md)、[机器计量](evidence/efficiency-20261110/comparison.json)。

## 交付与保留

GitHub源码入口为既有草稿[PR #1](https://github.com/muxueqingze/xuxiang-thinkflow/pull/1)，head为codex/thinkflow-desktop-v06。按主人本次明确授权，0.8.0 CLI已发布至npm的next通道，并创建[GitHub预发布](https://github.com/muxueqingze/xuxiang-thinkflow/releases/tag/v0.8.0)，tag指向6d85c06。两处提供同一份已安装验证的tarball，校验见[发布记录](evidence/npm-0.8.0.json)。latest仍为0.5.1；草稿未合并，Windows便携包仍是旧构建，v0.8版本级待Pro终审。

18次探索任务和6次单请求回放另存[探索证据](evidence/efficiency-20261110/exploration.json)，不替换最终值或重复计费。历史11-09及原六题/前端/小说报告保留。原始workspace、meter、events、harness、manifest留在本机bench/efficiency_20261011/artifacts/供复现；公开源码是[惰性JSON](evidence/efficiency-20261110/workspaces.json)。

全部本轮隔离profile及bytecode已回收，清单在evidence对应cleanup文件；实验与测试进程均结束。baseline/为复用输入；接手前备份仍在.git/thinkflow-pending-20261109.patch及.zip。无新依赖、PATH、Codex登录改动，密钥未进入项目。

下一阶段明确方向：用户原始要求/修订独立于压缩摘要保存；超长命令输出和已引用回执可按需回取原文。原文生命周期与会话存储统一设计，验收覆盖长消息中部约束、二次压缩/恢复、尾部诊断及取消/超时。自动ID保留为需先解决镜像/合法重复身份的设计项。
