> Source version 0.8.0 adds durable backend input admission and queues, exact receipt lookup, file browsing and revision-bound attachments, recorded diffs and guarded restoration, task plans with execution evidence, and verified DeepSeek Flash thinking/tool compatibility. Predictable writes continue during the model stream; unknown side effects stop automatic continuation. This source version has not been published to npm; the existing registry release is 0.5.1. See [desktop guide](docs/desktop.md), [handoff and evidence](docs/v0.8-handoff.md), [architecture](DESIGN.md) and [roadmap](docs/harness-roadmap.md).

# Xuxiang ThinkFlow

Language: [中文](README.md) | [English](README.en.md)

[![CI](https://github.com/muxueqingze/xuxiang-thinkflow/actions/workflows/ci.yml/badge.svg)](https://github.com/muxueqingze/xuxiang-thinkflow/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.8.0-blue.svg)](https://github.com/muxueqingze/xuxiang-thinkflow/releases)

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

The public npm version is 0.5.1; source version 0.8.0 has not been published yet:

```bash
npm install -g xuxiang-agent
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
npm install -g xuxiang-agent
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

The latest [production configuration benchmark](bench/harness_benchmark_20260929/reports-production/README.md) omits client output budgets and disables automatic history compaction and experiment run/request/continuation limits. ThinkFlow (91e4911), Pi 0.87.1 and OpenCode 1.18.33 used official DeepSeek Flash high on the same six tasks twice. Each completed 12/12 valid samples. Total tokens were 3,845,627, 1,397,532 and 1,732,556; mean times were 105.38, 54.62 and 59.90 seconds. **ThinkFlow still has no overall efficiency advantage.** Claude Code was not used.

One of the original 36 attempts was interrupted by a Windows observer logging error. Its record and 47,031 tokens remain preserved; one separate recovery used identical frozen conditions. All 37 actual attempts consumed 397 requests and 7,022,746 tokens. This is not a claim that all first attempts completed normally. Every request omitted output budget fields, 17 responses exceeded the previous 8K setting, and no length stops occurred. Saved test execution evidence was reviewed for all valid samples; it does not prove sufficient coverage.

ThinkFlow retained 25 file operations overlapping their streams. Protocol recovery, repeated test revisions and low-value probes still added substantial input retransmission costs; see the [case audit](docs/reviews/production-cost-cases.md). The [previous restricted rerun](bench/harness_benchmark_20260929/reports-rerun/README.md), [original round](bench/harness_benchmark_20260929/reports/summary.md) and [historical audit](docs/benchmark-history-diagnosis.md) remain unchanged. This suite is not a regression test of the old frontend/novel workloads. The existing 0.8 portable binary has not been rebuilt with these later source patches.

Historical benchmark materials remain available; their results must not be mixed with the new suite:

- `bench/reproducible_agent_efficiency/`: early reproducible efficiency experiment.
- `bench/agent_comparison_20260704/`: same-prompt comparison between Claude Code and ThinkFlow, with `glm-5.2` and `deepseek-v4-flash` runs.

Historical reports:

- `bench/agent_comparison_20260704/reports_normal_app/summary.md`
- `bench/agent_comparison_20260704/reports_normal_app/technical_report.md`
- `bench/agent_comparison_20260704/reports_deepseek_v4_flash/summary.md`
- `bench/agent_comparison_20260704/reports_deepseek_v4_flash/technical_report.md`

Full model transcripts, `node_modules`, local sessions and build artifacts remain excluded. The new suite publishes sanitized scores, provider usage and generated code as inert data for offline regrading.

## Current Capabilities

- Canonical `tf-*` protocol; legacy bare `<write>` tags are not executed by default.
- Dual parser for thinking/text streams.
- Single-worker FIFO execution queue for side-effect tools.
- Command ledger, session snapshots, and `/resume`.
- OpenAI-compatible and Anthropic-compatible adapters.
- Native tool registry for read, grep, glob, bash, write, append, edit, web, skills, and more.
- **Bundled `thinkflow` skill ships with the package**: the full harness guide (protocol, tool flows, ledger, working methods) is pulled on demand as a skill, while the system prompt keeps only the protocol skeleton plus a `read_skill("thinkflow")` hint; a user skill with the same name overrides the bundled one.
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
