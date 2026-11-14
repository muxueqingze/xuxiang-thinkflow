> Version 0.10.0 adds predictable streaming plan updates, ignore-aware project search, in-run steering, and optional phase context. See [release notes](docs/release-0.10.0.md), [desktop guide](docs/desktop.md) and [architecture](DESIGN.md).

# Xuxiang ThinkFlow

Language: [中文](README.md) | [English](README.en.md)

[![CI](https://github.com/muxueqingze/xuxiang-thinkflow/actions/workflows/ci.yml/badge.svg)](https://github.com/muxueqingze/xuxiang-thinkflow/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.10.0-blue.svg)](https://github.com/muxueqingze/xuxiang-thinkflow/releases)

> Predictable tool calls do not have to interrupt model reasoning.

Xuxiang ThinkFlow is an experimental streaming tool-call agent harness. It explores one technical claim: not every tool call should become a model-turn boundary.

Version 0.10.0 admits predictable plan updates to the same stream, respects project ignore rules during search, and adds steering at complete tool-batch boundaries. Optional phase context reuses the existing plan; no summary request is added. With no steering input, enabling the queue hook leaves request bodies and HTTP counts unchanged. [Changes and validation](docs/release-0.10.0.md)

Tools such as `read`, `grep`, `web`, and `bash` can return new information or trigger high-risk side effects, so ThinkFlow still handles them through blocking, confirmation, and feedback paths. Predictable side-effect tools such as `write`, `append`, `mkdir`, `touch`, `copy`, and `edit` can be emitted as structured `tf-*` commands during the model stream. A local parser, FIFO queue, executor, and command ledger execute them without stopping the model unless a failure or explicit `need_result` requires feedback.

In the 2026-11-10 Go DS Flash comparison (three tasks, two repetitions), both harnesses completed all six deliveries. ThinkFlow used 34 requests and 178,804 tokens; Pi used 48 requests and at least 459,820 tokens: at least 61.11% fewer total tokens and 29.17% fewer request attempts. One Pi request has unknown usage and remains included as an attempt.

The 2026-11-10 source defaults to the streaming protocol. Independent reads and checks are declared together and returned in one feedback batch. `tf-python` runs multiline code through stdin, using the existing execution and cancellation path. `--native-tools` or the desktop setting enables native compatibility and extensions; explicit configuration is preserved. See the [current comparison](docs/efficiency-results-20261110.md) and [diagnosis](docs/efficiency-direction-20261109.md).

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

Information tools produce new reasoning input; predictable side-effect tools usually produce execution receipts. Declare independent reads in one generation, then decide from the complete batch. Once changes are known, emit file operations and their checks in the same generation. The existing FIFO preserves order. Read only relevant information, keep first results and real errors, and avoid repeated checks after success.

Current source keeps original user requests separate from the compacted view. The current input stays verbatim; earlier requests can be retrieved by reference and character range with `recall`. Repeated compaction retains structured entries instead of clipping the previous summary again, without a summarization API call. Recall instructions are added only when available. In two DS Flash recovery probes, both new artifacts passed all nine independent checks; the old compactor's artifacts passed four and five. See the [implementation and evidence](docs/long-context-20261110.md) for quality results and raw usage. Included in the 0.9 series.

Tool output is also retained with its session. Long process output keeps a bounded tail preview and a complete text reference, including output received before cancellation or timeout. `recall` supports literal keyword lookup; restored sessions and forks retain their own backing files. A one-use output recovery probe improved from reaching the eight-request limit to completing in three requests. See the [implementation and live evidence](docs/output-recall-20261111.md). Short results stay unchanged; this capability is included in the 0.9 series.

`read` now accepts line ranges. A function or configuration section can be edited exactly after reading that section; consecutive edits update its version and observed positions. Independent reads stay batched, and whole-file replacement keeps the complete-read contract. See [range reads and continuous editing](docs/read-ranges-20261111.md) for implementation and validation. Included in the 0.9 series.

Current source also exposes layered user/project configuration, setting origins, `thinkflow config show|get|set|unset|path`, and invocation-only `--set` overrides. Desktop settings include an advanced runtime editor. Public `create_agent` and `load_configuration` APIs share the core without importing the CLI. Optional input and recent-history token budgets use the prepared request and local compaction. See [configuration and embedding](docs/configuration.md).

The 2026-11-11 review follow-up unifies exact read projection across text and both native protocols, converts history at send time when switching providers, and retains other independent reads after one fails. CLI/SDK sessions persist execution receipts during a run; configured commands retain full output, frozen attachment input is available through `request:N:input`, and usage separates actual HTTP attempts from logical turns. A fixed trace sends 5,710 fewer JSON characters in one request with the same request count. See [implementation and validation](docs/reviews/pro-implementation-20261111.md). These updates are included in the 0.9 series.

## Installation

`main` is the active development and release branch. See [contributing](CONTRIBUTING.md), [repository management](docs/repository-management.md), or [report a vulnerability privately](https://github.com/muxueqingze/xuxiang-thinkflow/security/advisories/new).

Windows desktop: download the [installer](https://github.com/muxueqingze/xuxiang-thinkflow/releases/download/v0.10.0/ThinkFlow-0.10.0-Windows-x64-Setup.exe), install for your user and open ThinkFlow from the Start menu. It includes Electron/Python and needs no administrator access, Node.js or Python installation. The [portable ZIP](https://github.com/muxueqingze/xuxiang-thinkflow/releases/download/v0.10.0/ThinkFlow-0.10.0-Windows-x64.zip) runs after extracting the complete folder.

**0.10.0** is now the default npm version. npm installs the CLI only:

```bash
npm install -g xuxiang-agent
thinkflow --help
xuxiang --help
```

For an exact version, use `npm install -g xuxiang-agent@0.10.0`. Since 2026-11-14, both `latest` and `next` point to 0.10.0; no channel suffix is required for the default install. See the [release notes](docs/release-0.10.0.md) for installation, upgrades and included changes.

CLI requirements:

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

`thinkflow` opens the default terminal interface; `thinkflow --tui` opens the full-screen interface. Enter submits; Ctrl+J or Alt+Enter inserts a newline. `/model`, `/thinking`, and `/resume` offer inline selectors in the default interface; `/models` lists models. [CLI guide](docs/cli-interaction.md)

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
- `/btw`, `/steer`: add instructions to the current run after the current response and tool batch
- `/queue`: inspect, edit, remove, and resume queued input
- `/cmds`: inspect local tool records and archived output
- `/cancel` or `Esc`: stop the active run and pause queued input while preserving the draft

## Documents

- [Predictable Tool Calls Do Not Need to Interrupt Model Reasoning](docs/predictable-tool-calls.md)
- [Experimental Xuxiang Agent and Its Open Technical Route](docs/xuxiang-streaming-agent.md)
- [Protocol](PROTOCOL.md)
- [Design](DESIGN.md)
- [Open Source Release Checklist](OPEN_SOURCE_RELEASE.md)
- [Citation Metadata](CITATION.cff)
- [Security Policy](SECURITY.md)

## System prompts

The core always injects the tool protocol. Recommended engineering guidance is optional: replace it with `--system-prompt my-guidance.md` or `system_prompt`, or disable it with `--no-system-prompt` / `use_builtin_system_prompt=false` and an empty custom prompt. Workspace rules are controlled separately by `context.enabled`. The desktop offers Recommended, Custom and Off; keeping the recommendation is the default. `--print-system-prompt` shows the actual composed prompt. Both layers are maintained separately in `src/prompts.py`.

The 61.11% benchmark remains pinned to source commit 40dfa30; the subsequent prompt separation preserves those historical results.

The concise engineering default is retained. An [optional guidance example](docs/prompts/engineering-guidance.md) covers scoped information gathering, shared implementations, contract-based design, meaningful assertions and inspection of deliverables; load it with `--system-prompt docs/prompts/engineering-guidance.md`. The trials did not establish a consistent benefit from expanded guidance. The [engineering improvement record](docs/engineering-quality-20261110.md) retains all trial results, corrected runtime defects and the next steps for retrieving original requirements and execution output in long sessions.

## Benchmark

See the [2026-11-10 streaming/batch comparison](docs/efficiency-results-20261110.md) for the measured streaming implementation. The [2026-11-09 native-mode results](docs/efficiency-results-20261109.md) and the older six-task report below remain separate historical comparisons.

The 2026-09-29 [production configuration benchmark](bench/harness_benchmark_20260929/reports-production/README.md) omits client output budgets and disables automatic history compaction and experiment run/request/continuation limits. ThinkFlow (91e4911), Pi 0.87.1 and OpenCode 1.18.33 used official DeepSeek Flash high on the same six tasks twice. Each completed 12/12 valid samples. Total tokens were 3,845,627, 1,397,532 and 1,732,556; mean times were 105.38, 54.62 and 59.90 seconds. **That earlier ThinkFlow version had no overall efficiency advantage on those six tasks.** Claude Code was not used.

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
- **Bundled `thinkflow` skill ships with the package**: the full harness guide (protocol, tool flows, ledger, working methods) is pulled on demand as a skill, while the system prompt contains the execution contract; native mode provides on-demand `read_skill("thinkflow")`; a user skill with the same name overrides the bundled one.
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

## Current desktop source

The desktop now exposes model, permission, and reasoning controls beside the composer, with context details on demand, full-screen searchable settings, real tool and skill catalogs, inline Git patches, multiple file tabs, and workspace-scoped PowerShell terminals. Panels resize without losing state. The default CLI and full-screen TUI share verbatim input queues and await cancellation and snapshot saving on exit. Reasoning controls map to DeepSeek effort, explicit OpenAI-compatible effort, or Anthropic token budgets. [Desktop guide](docs/desktop.md) · [Reference study](docs/research/zcode-interaction-20261111.md) · [Current interaction milestone](docs/interaction-handoff.md). Published npm and older portable builds are separate from these source changes.
