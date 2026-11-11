# Desktop / CLI 交互节点验证

2026-11-11，基线 `969377c`。当前节点把 ZCode 的实际交互落实到 ThinkFlow 已有内核，没有迁移前端框架；视觉权威入口仍为 `desktop/renderer/styles.css`。

## 实现与运行证据

| 单元 | 实际验证 |
| --- | --- |
| Python 内核与桌面服务 | `python -B tests/run_all.py`：parser/core 与 300 项 unittest 全部通过，unittest 耗时 135.932 秒 |
| Electron 设置和传输契约 | `npm --prefix desktop test`：15 项通过 |
| 原有完整任务流程 | `electron-workbench.cjs`：连接/模型列表、冻结文件引用、流式写入/计划、diff/回退冲突、丢失回执、队列停止/重启/恢复及窄窗六组通过；[结果](../evidence/interaction-20261111/workflow.json) |
| 新工作区面板 | `electron-panels.cjs`：共九组通过，包含实际 PowerShell 输入、关闭进程、跨会话保留、真实 Git 三类差异、设置/JSON 同步、拖拽/快速反转、主题及键盘；[结果](../evidence/interaction-20261111/panels.json)、[工作台](../evidence/interaction-20261111/workbench-light.png) |
| 输入区细节 | `electron-composer.cjs`：四组通过，菜单就地选择、上下文和长命令授权；[结果](../evidence/interaction-20261111/composer.json) |
| 思考强度 | `test_cli_interaction.py` 直接核对 13 份实际请求组装；`electron-thinking.cjs` 经本地 HTTP/SSE 核对 6 份请求。分别覆盖 DeepSeek 开关/强度、通用 OpenAI 默认/显式 effort、Anthropic 预算及无效预算保留；[请求参数](../evidence/interaction-20261111/thinking.json) |
| Git 行内差异 | `electron-git-inline.cjs`：两个真实临时仓库，多文件同时展开、行号、刷新保留 DOM、隐藏重开与工作区切换通过 |
| 文件标签 | `electron-file-tabs.cjs`：两份真实文件的独立快照/滚动、刷新、异步读取、引用入口、同工作区新会话保留及跨工作区清空通过 |
| 独立功能和审美 | 审查者独立运行 Electron、实际查看宽/窄窗截图及 DOM，所列问题均定向复核通过；[完整报告](interaction-qa-20261111.md) |

默认 CLI 和全屏 TUI 另用 Windows ConPTY 实际键序核对多行输入、粘贴、队列和退出。全部模型为本机夹具；没有付费 API 请求。菜单操作和配置保存不额外调用模型。

## 一次集成失败与修正

最终整套检查首次出现 43 个 `AttributeError: 'FakeAgent' object has no attribute 'config'`：桌面状态新增实际 provider 的思考字段，四份旧测试工厂共用的 FakeAgent 没有该接口。测试替身改用真实 `build_agent_config` 接收对应配置；运行代码没有添加缺失属性兜底。桌面服务 18 项及导航/输入回执/交叉审查 31 项定向通过，随后上表完整 300 项通过。

独立检查发现的窄窗、焦点、滚动及设置字段问题，原始画面、修复和复核在独立报告按阶段保存，不重复归入内核故障。

## GitHub Windows runner 的两处差异

`d34ef25` 的 [PR 检查](https://github.com/muxueqingze/xuxiang-thinkflow/actions/runs/42983581066)执行 300 项后发现两处失败：Git 根目录的长/短路径字符串不同，以及英文 Windows 中的中文终端输出变成 `??`。前者改用 `Path.samefile` 核对目录实体；后者保留中文断言，修正新建 PowerShell 的 Console 输入/输出及原生管道编码为 UTF-8。

真实 ConPTY 定向复现确认：把该独立终端的输出编码设为 437 时 `BEFORE=??`，改为 UTF-8 后 `AFTER=尺寸`。修复后的 Git/终端两项共 7.152 秒通过，核对 `65001/65001/65001`、中文、resize、输入、回放、退出码及进程树清理。默认编码随系统 locale 变化的依据见 [Console.OutputEncoding](https://learn.microsoft.com/en-us/dotnet/api/system.console.outputencoding?view=netframework-4.8.1)，原生管道变量见 [PowerShell 5.1 OutputEncoding](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_preference_variables?view=powershell-5.1#outputencoding)。仅初始化应用新建的终端，不修改系统配置或用户 profile。

## 保留与清理

正式回归脚本位于 `tests/` 和 `desktop/tests/`；测试默认不进入正常应用流程。必要截图/JSON 位于 `docs/evidence/interaction-20261111/`，官方参考截图在 `docs/evidence/zcode-reference-20261111/`。本轮专属 profile、临时仓库、模型服务与临时日志在保存上述证据后关闭并回收。

ZCode 完整源码留在 ThinkFlow 同级 `ZCode/`，其依赖和生成的桌面运行资源供后续对照复用；独立启动脚本和本机假模型入口见 [reference README](../research/zcode-reference/README.md)，默认不启动。新增运行依赖为 Windows `pywinpty>=3.1.2`、`@xterm/xterm@6.0.0`、`@xterm/addon-fit@0.11.0`；没有新增持久 PATH。

当前交付为源码，npm 与旧 Windows 便携包保持原发布版本。GitHub 检查见 [PR #1](https://github.com/muxueqingze/xuxiang-thinkflow/pull/1/checks)，本报告是本交互单元验证；版本级待 Pro 终审。
