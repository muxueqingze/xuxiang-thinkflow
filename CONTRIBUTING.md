# 参与 ThinkFlow

[English](CONTRIBUTING.en.md)

欢迎代码、文档、模型适配、交互改进与可复现的任务样本。ThinkFlow希望用更少的无效往返完成同样或更好的工程工作：可预测操作连续执行，独立信息调用合批，模型获得当前任务所需的最小充分信息。

## 找到合适的入口

| 你想做什么 | 入口与需要的信息 |
|---|---|
| 报告问题 | [问题反馈](https://github.com/muxueqingze/xuxiang-thinkflow/issues/new?template=bug_report.yml)：版本、运行环境、实际与预期结果，能复现的步骤 |
| 提出改进或贡献方向 | [改进建议](https://github.com/muxueqingze/xuxiang-thinkflow/issues/new?template=feature_request.yml)：具体使用场景、当前限制和希望达到的效果；实现方案选填 |
| 询问安装、配置或用法 | 先看[使用说明](README.md)与[配置指南](docs/configuration.md)，仍有疑问可发[使用提问](https://github.com/muxueqingze/xuxiang-thinkflow/issues/new?template=question.yml) |
| 报告可利用漏洞或密钥泄露 | 使用[私密漏洞报告](https://github.com/muxueqingze/xuxiang-thinkflow/security/advisories/new)，范围见[安全策略](SECURITY.md) |

中文或英文均可。提交前搜一下现有[问题与PR](https://github.com/muxueqingze/xuxiang-thinkflow/issues)，相关补充留在同一讨论里。公开日志只放与问题有关的片段，去掉密钥和个人会话内容。没有合适表单时也可以直接创建空白Issue。

## 从哪里开始改

[代码地图](docs/代码地图.md)按任务指向实现；[项目约定](AGENTS.md)说明工作方式。只读本次涉及的模块与资料。

| 贡献方向 | 实现与说明入口 |
|---|---|
| 工具调用、模型适配、执行效率 | [AgentLoop](src/agent_loop.py)、[工具注册表](src/tool_registry.py)、[协议](PROTOCOL.md) |
| 上下文、压缩、会话与恢复 | [ConversationContext](src/conversation.py)、[SessionStore](src/session.py)、[配置与SDK](docs/configuration.md) |
| CLI与桌面交互 | [CLI指南](docs/cli-interaction.md)、[桌面开发](desktop/README.md)；共享视觉入口是[styles.css](desktop/renderer/styles.css) |
| 任务质量与效率对照 | [现有评测入口](bench/efficiency_20261011/README.md)；先核对产物，再比较完整任务的请求与用量 |

已明确范围、欢迎协助的问题会标记为 [help wanted](https://github.com/muxueqingze/xuxiang-thinkflow/labels/help%20wanted)。[good first issue](https://github.com/muxueqingze/xuxiang-thinkflow/labels/good%20first%20issue)用于入口、范围和预期结果都已写清的小任务。

## 开发与验证

从自己的fork或本仓库获取代码后，在仓库根目录使用Python 3.12+安装：

```bash
python -m pip install -e .
python run.py --help
```

可以使用已有的项目虚拟环境。只运行帮助、配置命令和本地回归不需要模型key；实际模型任务按[配置指南](docs/configuration.md)设置。

桌面源码开发需要Node.js **22.12+**，启动、隔离开发数据与现有交互脚本统一见[桌面开发入口](desktop/README.md)。桌面安装包的构建依赖见[开发与构建](docs/desktop.md#开发与构建)，普通贡献无需先打安装包。

按修改的行为选择验证，PR记录实际命令和结果：

| 修改范围 | 验证入口 |
|---|---|
| Python局部行为 | 选择对应测试，例如 `python -m unittest discover -s tests -p test_workspace_search.py` |
| 核心执行、协议或会话链路 | 复现原问题并覆盖直接影响；集中回归入口为 `python tests/run_all.py` |
| 桌面接口 | `npm --prefix desktop test` |
| 可见交互 | 使用[已有Electron脚本](desktop/README.md)，并附对应操作路径、截图或短录屏 |
| 文档或表单 | 核对链接、命令、字段和实际行为 |

现有CI在Windows上使用Python 3.12和Node 22，执行Python回归与桌面接口测试。首次外部贡献的CI可能显示等待批准，由维护者处理。付费模型评测单独运行，普通PR不要求提供key或重跑全部基准。效率结论注明模型、版本、任务、验证结果与完整用量，保留失败尝试的成本。

实验使用项目`temp/`中的专属目录，结束后清理本轮进程和不再复用的输出；可复现脚本与必要证据归入对应测试或文档位置。

## 提交与审查

维护者直接在`main`开发、发布。外部贡献在自己的fork中准备改动，向本仓库`main`提交PR；无需申请写入权限。

1. 小修可以直接提PR。涉及工具协议、默认行为、SDK接口或会话格式的大改动，建议先用Issue说明场景和方案，尽早对齐范围。
2. 一个PR解决一个明确问题。复用现有执行、回执和持久化接口；专用行为优先通过已有配置、工具注册和SDK扩展。
3. 用自己的Git身份提交。标题说明具体行为变化；按[PR模板](.github/pull_request_template.md)说明改了什么、为什么有效、实际怎样验证。
4. 维护者结合CI与代码、实际产物审查。需要补充时继续更新原PR；合并后维护者统一处理版本、发布说明和分发。

提交人应能解释实现及其影响，验证写实际结果。默认指引仍可被用户替换，框架协议和执行事实由原有代码链维护。

## 来源与署名

贡献按项目的[MIT许可证](LICENSE)分发，保留贡献者署名。引用第三方代码、素材或新增依赖时，说明来源并保留相应许可证与署名。当前不要求额外CLA或DCO签署流程，也不回改已有提交的作者。
