# 供 Pro 终审的材料说明

状态：待 Pro 终审。两轮专家是功能与交付节点的定向检查，不等同于版本级全面审查。

请审查续想 ThinkFlow v0.7 的源码、测试、协议和桌面交付。它的核心主张是：可预测副作用的成功确认不必形成模型回合边界；信息型工具、失败与显式结果依赖必须反馈。请以实现而非文档口号判断。

先读 README.md、DESIGN.md、PROTOCOL.md、docs/harness-roadmap.md，再检查 src/agent_loop.py、parser.py、executor.py、context.py、desktop_service.py、tool_registry.py、interfaces.py，以及desktop主进程/preload/renderer。已有两轮报告和修复表位于docs/reviews。

重点核查：两个工具通道是否遵循一致失败/去重/授权语义；取消、EOF、断连、重试和恢复能否造成漏写/重写/误报成功；执行意图与回执的持久化时序；上下文与账本预算是否丢关键观察；权限与真实执行能力的边界；桌面状态与后端是否一致；测试是否覆盖真实契约。请区分已复现问题、可由代码证明的问题与需要本机实验的假设。

v0.7 特别关注会话/工作区隔离与恢复、归档数据兼容、草稿持久化、待发队列和发送回执不确定时的处理。主会话的真实 Electron 测试与本轮独立交互验收见 `docs/v0.7-handoff.md` 和 `docs/reviews/v0.7-ux.md`。参考 Codex 的交互不代表拥有其跨会话并行、终端或 Git 编辑能力。

输出问题优先，给P0–P3、位置、触发条件、影响和最小修法。不要因为提出harness新方向就要求一次实现全部生态。特别审视：相比原生批量工具调用，续想的可证明收益是否被夸大；哪些任务适合该方案，哪些不适合。

材料包含源码与必要测试。Windows包可独立启动，本地SSE测试不代表真实商业provider已经验收；macOS/Linux、真实掉电与长时负载未验证。不要将待验证项写成已通过。
