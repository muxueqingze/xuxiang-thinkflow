# Xuxiang ThinkFlow

Language: [中文](README.md) | [English](README.en.md)

[![CI](https://github.com/muxueqingze/xuxiang-thinkflow/actions/workflows/ci.yml/badge.svg)](https://github.com/muxueqingze/xuxiang-thinkflow/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.5.0--beta.2-orange.svg)](https://github.com/muxueqingze/xuxiang-thinkflow/releases/tag/v0.5.0-beta.2)

> Predictable tool calls do not have to interrupt model reasoning.

Xuxiang ThinkFlow is an experimental streaming tool-call agent harness. It explores one technical claim: not every tool call should become a model-turn boundary.

Tools such as `read`, `grep`, `web`, and `bash` can return new information or trigger high-risk side effects, so ThinkFlow still handles them through blocking, confirmation, and feedback paths. Predictable side-effect tools such as `write`, `append`, `mkdir`, `touch`, `copy`, and `edit` can be emitted as structured `tf-*` commands during the model stream. A local parser, FIFO queue, executor, and command ledger execute them without stopping the model unless a failure or explicit `need_result` requires feedback.

## Core Idea

Traditional agent harnesses often use this loop:

```text
model reasoning
-> provider tool_call
-> harness interrupts the model
-> tool executes
-> harness calls the model again with the result
```

ThinkFlow separates predictable side effects from information-bearing calls:

```text
model stream
-> tf-* command parser
-> single-worker FIFO queue
-> executor + security policy
-> command ledger
-> success: keep streaming
-> failure / need_result: interrupt and feed back
```

This does not remove feedback. It distinguishes feedback types: information tools produce new reasoning input; predictable side-effect tools usually produce execution receipts.

## Installation

The current version is a beta prerelease and is published on the npm registry:

```bash
npm install -g xuxiang-agent@beta
thinkflow --help
xuxiang --help
```

The GitHub Release tarball remains available for archival installs and reproducibility.

Requirements:

- Node.js 18+
- Python 3.12+
- An OpenAI-compatible or Anthropic-compatible model endpoint
- A local `config.json` or equivalent `THINKFLOW_*` environment variables

If multiple Python versions are installed:

```bash
set THINKFLOW_PYTHON=C:\Path\To\Python312\python.exe
npm install -g xuxiang-agent@beta
```

Development install:

```bash
git clone https://github.com/muxueqingze/xuxiang-thinkflow.git
cd xuxiang-thinkflow
python -m pip install -e .
thinkflow --help
```

## Quick Start

Create a secret-free config template:

```bash
thinkflow --init-config
thinkflow --doctor --config config.json
```

OpenAI-compatible example:

```bash
python -m src.cli ^
  --provider openai ^
  --base-url https://your-openai-compatible-host ^
  --model your-model ^
  --api-key YOUR_KEY ^
  --verbose
```

Environment-variable example:

```bash
set THINKFLOW_PROVIDER=openai
set THINKFLOW_BASE_URL=https://your-openai-compatible-host
set THINKFLOW_MODEL=your-model
set THINKFLOW_API_KEY=...
thinkflow --prompt "Create hello.py"
```

## Providers and Model Discovery

ThinkFlow is provider-neutral. You can define named provider profiles in `config.json`, let ThinkFlow query an OpenAI-compatible `/models` endpoint, and select a model by preference:

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

List models:

```bash
thinkflow --config config.json --list-models
```

In the interactive session, `/models`, `/model`, `/thinking`, and `/resume` open inline selectors.

## Common Commands

```bash
thinkflow --init-config
thinkflow --doctor --config config.json
thinkflow --config config.json --prompt "Create hello.py"
thinkflow --config config.json --resume
thinkflow --config config.json --sandbox balanced
```

Interactive slash commands include:

- `/model`: switch model or provider
- `/models`: list configured or discovered models
- `/thinking`: switch thinking level
- `/resume`: select and replay a previous session
- `/new`: start a new session
- `/ctx`: inspect context usage
- `/usage` / `/savings`: inspect token usage and estimated savings
- `/tools`: inspect tool routing and risk
- `/security` / `/sandbox`: inspect or change security mode
- `/compact`: compact old context
- `/btw`: append a note while the model is running
- `/cancel` or `Esc`: request cancellation

## Documents

- [Predictable Tool Calls Do Not Need to Interrupt Model Reasoning](docs/predictable-tool-calls.md)
- [Experimental Xuxiang Agent and Its Open Technical Route](docs/xuxiang-streaming-agent.md)
- [Protocol](PROTOCOL.md)
- [Design](DESIGN.md)
- [Open Source Release Checklist](OPEN_SOURCE_RELEASE.md)
- [Citation Metadata](CITATION.cff)
- [Security Policy](SECURITY.md)

## Benchmark

The repository includes two benchmark areas:

- `bench/reproducible_agent_efficiency/`: early reproducible efficiency experiment.
- `bench/agent_comparison_20260704/`: same-prompt comparison between Claude Code and ThinkFlow, with `glm-5.2` and `deepseek-v4-flash` runs.

Core reports:

- `bench/agent_comparison_20260704/reports_normal_app/summary.md`
- `bench/agent_comparison_20260704/reports_normal_app/technical_report.md`
- `bench/agent_comparison_20260704/reports_deepseek_v4_flash/summary.md`
- `bench/agent_comparison_20260704/reports_deepseek_v4_flash/technical_report.md`

Raw runs, logs, `node_modules`, local sessions, and build artifacts are intentionally excluded from the repository and npm package.

## Current Capabilities

- Canonical `tf-*` protocol; legacy bare `<write>` tags are not executed by default.
- Dual parser for thinking/text streams.
- Single-worker FIFO execution queue for side-effect tools.
- Command ledger, session snapshots, and `/resume`.
- OpenAI-compatible and Anthropic-compatible adapters.
- Native tool registry for read, grep, glob, bash, write, append, edit, web, skills, and more.
- Automatic context compaction.
- Provider profiles and model discovery.
- Windows-friendly CLI, tests, and CI.

## Safety Defaults

ThinkFlow is conservative by default:

- File tools are scoped to the current working directory.
- Reads reject `.env`, private keys, npm/PyPI credentials, and other common secret files.
- Bash defaults to the `safe` policy, with timeouts and output truncation.
- Bash child processes do not inherit API keys by default.

Higher-permission modes must be enabled explicitly:

```bash
thinkflow --allow-outside-cwd --allow-sensitive-paths --bash-policy unrestricted
thinkflow --sandbox open
thinkflow --trust-workspace
```

## Verification

```bash
python tests/run_all.py
python -m compileall -q src tests bench run.py
python run.py --help
python run.py --doctor --config config.example.json
npm pack --dry-run --json
```

## Attribution and Contact

This project is published under the online name **沐雪清泽**.

Xuxiang ThinkFlow is an experimental agent harness and technical-route validation project. It is not a commercial service commitment and does not represent any company or institution.

For citation, benchmark reproduction, engineering collaboration, or security reports, use GitHub Issues / Discussions or the public contact methods listed on the author's GitHub profile.
