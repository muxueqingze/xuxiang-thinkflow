# 交互改造接力

当前分发已进入 [0.9.1](release-0.9.1.md)：npm `next` 提供 CLI，GitHub 提供 Windows 安装版与便携 ZIP。下文保留交互节点的实现和验证；本次补丁修复桌面 Python 运行时及截断日志恢复，没有调整界面布局或操作方式。

更新：2026-11-11。基线 `969377c`；本交互节点已完成实现、本机集中验证及独立功能/审美定向验收，交付源码至既有 [PR #1](https://github.com/muxueqingze/xuxiang-thinkflow/pull/1)。用户体验待实际使用反馈。

- 已实现：工作区级 xterm / ConPTY、Git 多文件行内差异与新旧行号、多文件标签及阅读位置、全屏设置和字段搜索、真实工具/技能目录。输入区直接选择模型、权限和思考档位；上下文浮层、原位授权、单一尾部活动反馈和可拖拽面板使用统一动效。CLI 共用原文 FIFO、停止暂停、退出等待保存及本地工具详情。
- 已验证：parser/core + 300 项 unittest、Node 15 项；真实 Electron 六组原工作流、九组面板、四组输入细节，以及思考请求、Git 行内展开和文件标签专用验证。DeepSeek、OpenAI 兼容、Anthropic 的真实请求组装已抓取核对。均为本地假模型，没有付费模型调用。见[验证与保留](reviews/interaction-20261111.md)。
- 独立质检提出的窄窗布局、JSON 输入、终端背景、菜单焦点、长命令滚动、设置搜索、活动标签定位与技能正文定位问题均已修复，原失败项复核通过，见[独立报告](reviews/interaction-qa-20261111.md)。
- 关键决定：CSS 视觉权威入口仍为 `desktop/renderer/styles.css`；没有前端框架迁移。未知模型窗口不绘制占用百分比；上下文浮层明确使用最近真实请求估算和独立的整理阈值。菜单打开不请求模型。
- 参考实物：官方 ZCode `b95ebaffeab6` 完整源码及隔离 Electron 已实跑，覆盖设置、模型/思考、权限、Git 与终端。研究过程和保留入口见[源码对照](research/zcode-interaction-20261111.md)，CLI 见[用法](cli-interaction.md)。
- 下一步：用户试用当前安装包；后续效率实验继续按[效率接力](efficiency-handoff.md)进行。0.9 系列已包含本交互节点，旧 0.8.0 包保留历史内容。
