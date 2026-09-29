# 续想 ThinkFlow

Language: [中文](README.md) | [English](README.en.md)

[![CI](https://github.com/muxueqingze/xuxiang-thinkflow/actions/workflows/ci.yml/badge.svg)](https://github.com/muxueqingze/xuxiang-thinkflow/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.8.0-blue.svg)](https://github.com/muxueqingze/xuxiang-thinkflow/releases)

> 可预测式工具调用不需要打断模型的推理与思考。

续想 ThinkFlow 是一个实验性流式工具调用 agent harness。它验证一个核心判断：并不是所有工具调用都应该成为模型回合边界。

对于 `read`、`grep`、`web`、`bash` 这类会带来新信息或高风险副作用的工具，续想仍然使用阻塞、确认、反馈路径。对于 `write`、`append`、`mkdir`、`touch`、`copy`、`edit` 这类可预测副作用工具，续想允许模型在连续 streaming 中写出结构化 `tf-*` 命令，由本地状态机、FIFO 队列、执行器和 ledger 接管执行。成功执行只是回执，不必打断模型正在进行的推理。

## 核心路线

传统 agent 的典型工具链路是：

```text
模型推理
-> 输出 tool_call
-> harness 打断模型
-> 执行工具
-> 带着结果重新调用模型
```

续想把可预测副作用工具改成：

```text
model stream
-> tf-* command parser
-> single-worker FIFO queue
-> executor + security policy
-> command ledger
-> success: keep streaming
-> failure / need_result: interrupt and feed back
```

这不是取消反馈，而是区分反馈类型：信息型工具的结果是新的推理输入；可预测副作用工具的成功结果通常只是执行确认。

## v0.8：完整任务工作流

这一版把输入接收、文件变更和失败恢复连成完整工作流：

- **后端持久队列**：稳定提交ID、内容指纹、精确回执查询；丢ACK不重复执行，停止/失败/重启后暂停。
- **文件工作台**：目录搜索、文本预览、带版本的文件引用、逐次diff和检查当前版本后的回退。
- **受保护的写入**：SHA256读取基线，拒绝覆盖外部新改动；确定冲突可重新读取，副作用未知则停止当前模型循环等待核对。
- **任务与证据**：持久计划、验收条件、成功工具回执引用；完成标记仍须核验语义。
- **真实DeepSeek Flash适配**：连接诊断、模型列表、思考开关，以及跨原生工具轮保留必要的reasoning字段。

真实Flash测试已覆盖连续写入、读改写与验证、外部冲突恢复；三次独立文件写入均在同一次模型流结束前完成。详细流程见[桌面指南](docs/desktop.md)，本轮状态及证据见[v0.8接力](docs/v0.8-handoff.md)。当前不安排Pro终审。

## v0.7 基础：会话工作台

以连续对话为中心，参考 Codex 的会话导航和快捷操作：搜索、置顶、重命名、归档、最近工作区、重启恢复、独立草稿、可折叠账本与命令面板。Enter 发送，Shift+Enter 换行；运行中的补充消息进入待发队列，成功后继续，停止/失败/重启后暂停。具体使用与边界见 [桌面指南](docs/desktop.md)，进展与验证见 [v0.7 接力](docs/v0.7-handoff.md)。

v0.7 已通过源码与 Windows 独立包的本地 SSE 流程、11组交互和两种窗口尺寸的压力验证。独立专家发现的长授权/队列布局问题已修复并复核，见 [本轮验收](docs/reviews/v0.7-ux.md)。真实端点体验与版本级 Pro 终审仍待。

## v0.6 基础：桌面与可靠执行

新增 **Windows 桌面端**：选择工作区、模型设置、会话恢复/分支、流式消息、执行账本、用量、单次授权、停止与导出。独立文件夹包含运行时，无需用户安装Python/Node；使用方式见 [桌面指南](docs/desktop.md)。

内核继续与CLI共用：信息型工具强制反馈、后台失败及时中断、取消清理、native工具分片正确合并、完整命令恢复、有限运行。桌面执行前后记录意图和回执，异常退出不自动重放未知副作用。

2026-09-29：两轮独立专家对抗及原失败项复核完成，功能/视觉定向验收通过；独立Windows包已实测。未验证真实商业端点、长时负载和多平台，待用户试玩与Pro版本终审。报告见 [第1轮](docs/reviews/round-1.md)、[第2轮及最终复核](docs/reviews/round-2.md)。

[架构](DESIGN.md) · [harness能力地图与扩展建议](docs/harness-roadmap.md) · [v0.6 接力与验证状态](docs/v0.6-handoff.md)

## 安装

当前源码版本 **0.8.0**，尚未发布到 npm。registry 中的 0.5.1 仍可通过下面的命令安装：

```bash
npm install -g xuxiang-agent
thinkflow --help
xuxiang --help
```

历史 GitHub Release 保留旧版 tarball。0.8.0 请从当前源码安装或使用本地 Windows 桌面包。

要求：

- Node.js 18+
- Python 3.12+
- 一个 OpenAI-compatible 或 Anthropic-compatible 模型端点
- 本地 `config.json` 或 `THINKFLOW_*` 环境变量

如果本机有多个 Python 版本：

```bash
set THINKFLOW_PYTHON=C:\Path\To\Python312\python.exe
npm install -g xuxiang-agent
```

开发安装：

```bash
git clone https://github.com/muxueqingze/xuxiang-thinkflow.git
cd xuxiang-thinkflow
python -m pip install -e .
thinkflow --help
```

## 快速开始

生成不含密钥的配置模板：

```bash
thinkflow --version
thinkflow --init-config
thinkflow --doctor --config config.json
```

OpenAI-compatible 示例：

```bash
python -m src.cli ^
  --provider openai ^
  --base-url https://your-openai-compatible-host ^
  --model your-model ^
  --api-key YOUR_KEY ^
  --verbose
```

环境变量示例：

```bash
set THINKFLOW_PROVIDER=openai
set THINKFLOW_BASE_URL=https://your-openai-compatible-host
set THINKFLOW_MODEL=your-model
set THINKFLOW_API_KEY=...
thinkflow --prompt "创建 hello.py"
```

## Provider 与模型发现

续想不绑定任何模型供应商。可以在 `config.json` 里配置多个命名 provider，让 agent 根据 URL/key 动态读取该 provider 的模型列表，并按偏好自动选择：

```json
{
  "active_provider": "my-provider",
  "providers": {
    "my-provider": {
      "provider": "openai",
      "base_url": "https://your-openai-compatible-host",
      "api_path": "",
      "api_key": "",
      "model": "auto",
      "model_preference": [
        "glm-5.2",
        "kimi",
        "deepseek"
      ],
      "model_discovery": {
        "enabled": true,
        "path": "/v1/models",
        "timeout_seconds": 20
      }
    }
  }
}
```

查看模型：

```bash
thinkflow --config config.json --list-models
```

交互会话里可用 `/models`、`/model`、`/thinking`、`/resume` 打开行内选择器，方向键选择、Enter 确认、Esc 取消。

## 常用命令

```bash
thinkflow --version
thinkflow --init-config
thinkflow --doctor --config config.json
thinkflow --config config.json --prompt "创建 hello.py"
thinkflow --config config.json --resume
thinkflow --config config.json --sandbox balanced
```

交互命令包括：

- `/model`：切换模型或 provider
- `/models`：查看模型目录
- `/thinking`：切换思考强度
- `/resume`：选择历史会话并回放上下文
- `/new`：开始新会话
- `/ctx`：查看上下文占用
- `/usage` / `/savings`：查看用量与节省估算
- `/tools`：查看工具分流与风险
- `/security` / `/sandbox`：查看或切换安全模式
- `/compact`：手动压缩旧上下文
- `/btw`：运行中追加旁注
- `/cancel` 或 `Esc`：请求打断当前模型请求

## 技术路线

如果想深入理解续想的技术路线和设计思路，请阅读以下两篇论文：

1. **《可预测式工具调用不需要打断模型的推理与思考》** — 核心理论判断，阐述为什么可预测副作用工具的成功反馈不应成为模型推理的强制中断点。
   📄 [阅读全文](docs/predictable-tool-calls.md)

2. **《实验性续想 agent 及其技术路线已经开源》** — 实现层面的设计文档，从 tf-* 协议、parser 状态机、FIFO 队列到执行器和 ledger 的完整链路说明。
   📄 [阅读全文](docs/xuxiang-streaming-agent.md)

## 其他文档

- [命令协议](PROTOCOL.md)
- [架构设计](DESIGN.md)
- [开源发布清单](OPEN_SOURCE_RELEASE.md)
- [引用元数据](CITATION.cff)
- [安全策略](SECURITY.md)

## Benchmark

仓库包含两组 benchmark 材料：

- `bench/reproducible_agent_efficiency/`：早期可重复效率实验。
- `bench/agent_comparison_20260704/`：Claude Code 与续想在同 prompt 下的对照实验，包含 `glm-5.2` 与 `deepseek-v4-flash` 两轮结果。

核心报告：

- `bench/agent_comparison_20260704/reports_normal_app/summary.md`
- `bench/agent_comparison_20260704/reports_normal_app/technical_report.md`
- `bench/agent_comparison_20260704/reports_deepseek_v4_flash/summary.md`
- `bench/agent_comparison_20260704/reports_deepseek_v4_flash/technical_report.md`

raw runs、日志、`node_modules`、本地 session 与构建产物不进入仓库和 npm 包。

## 当前能力

- canonical `tf-*` 协议，默认不执行旧式裸 `<write>` 标签。
- thinking/text 双 parser，支持模型把命令写在不同流通道中。
- FIFO 单 worker 工具队列，避免并发写入互相覆盖。
- 命令 ledger、session 快照、`/resume` 历史恢复。
- OpenAI-compatible 与 Anthropic-compatible 适配。
- 原生工具注册层：read、grep、glob、bash、write、append、edit、web、skills 等。
- **内置 `thinkflow` skill 随包分发**：完整 harness 使用指南（协议、分流、ledger、工作方法）以 skill 形式按需拉取，系统提示词只保留协议骨架与 `read_skill("thinkflow")` 指引；用户同名 skill 可覆盖内置。
- 自动上下文压缩，避免长会话无限增长。
- 模型目录发现与 provider profile。
- Windows 友好的 CLI、测试与 CI。

## 安全默认值

续想默认按保守策略运行：

- 文件工具默认只能访问当前 `cwd` 内路径。
- read 默认拒绝 `.env`、私钥、npm/PyPI 凭证等常见密钥文件。
- bash 默认使用 `safe` 策略，并设置超时和输出截断；命令过滤不等于操作系统沙箱。桌面平衡模式会逐次询问高风险调用。
- bash 子进程默认不继承 API key。

需要更高权限时必须显式打开：

```bash
thinkflow --allow-outside-cwd --allow-sensitive-paths --bash-policy unrestricted
thinkflow --sandbox open
thinkflow --trust-workspace
```

## 验证

```bash
python tests/run_all.py
python -m compileall -q src tests bench run.py
python run.py --help
python run.py --doctor --config config.example.json
npm pack --dry-run --json
```

## 署名与联系

本项目目前以网络署名“沐雪清泽”发布。续想 ThinkFlow 是实验性 agent harness 与技术路线验证项目，不是商业服务承诺，也不代表任何公司或机构立场。

引用、复现实验、工程合作或安全报告，请优先使用 GitHub Issues / Discussions，或通过作者 GitHub 主页公开的联系方式联系。
