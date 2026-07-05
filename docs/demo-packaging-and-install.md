# 续想 ThinkFlow v0.5 beta Packaging and Install Guide

Date: 2026-07-01
Status: v0.5 beta open-source packaging guide

## Functional Package

The functional package is the artifact normal users should install. It exposes global `thinkflow` and `xuxiang` commands through npm, while the runtime remains the Python core.

Requirements:

- Node.js 18+
- Python 3.12+
- Network access to the model provider endpoint
- A local config containing provider/base_url/model/api_key, or equivalent `THINKFLOW_*` environment variables

Install:

```bash
npm install -g https://github.com/muxueqingze/xuxiang-thinkflow/releases/download/v0.5.0-beta.1/xuxiang-agent-0.5.0-beta.1.tgz
thinkflow --init-home
thinkflow --init-config
```

If multiple Python versions are installed:

```bash
set THINKFLOW_PYTHON=C:\Path\To\Python312\python.exe
npm install -g https://github.com/muxueqingze/xuxiang-thinkflow/releases/download/v0.5.0-beta.1/xuxiang-agent-0.5.0-beta.1.tgz
```

Launch from any project directory:

```bash
cd D:\path\to\project
thinkflow
```

## Full Source Package

The full source tree is for development, audits, and reproducible experiments. It should include:

- `src/`, `tests/`, `docs/`
- `bench/reproducible_agent_efficiency/`
- `README.md`, `PROTOCOL.md`, `DESIGN.md`
- `config.example.json`, `pyproject.toml`, `package.json`
- no local `config.json`, no `.env`, no `.thinkflow/`, no release/build output, no `node_modules`

Install for development:

```bash
python -m pip install -e .
thinkflow --help
```

## Context Files

续想 injects the built-in harness system prompt by default. It then appends context files when present:

Global context:

```text
~/.thinkflow/AGENTS.md
~/.thinkflow/context.md
```

Workspace context:

```text
./AGENTS.md
./agents.md
```

Workspace context is discovered from the current directory upward until the git root. Use `--no-context` to disable file context injection, `--context-file path/to/file.md` to add extra files, and `--no-system-prompt` to disable the system prompt entirely.

## Permission Modes

| Mode | Alias | File access | Bash | Approval |
|---|---|---|---|---|
| `read-only` | `readonly` | read inside cwd only | off | request_all |
| `workspace-write` | `balanced`, `workspace` | read/write inside cwd | safe | auto |
| `danger-full-access` | `open`, `unrestricted` | unrestricted | unrestricted | approve_all |

`danger-full-access` is the highest-permission mode. It ignores the cwd sandbox, allows sensitive paths, uses unrestricted bash, and approves tool execution through policy.

## Verification

Run before publishing:

```bash
python -m compileall -q src tests run.py
python tests\run_all.py
node bin\thinkflow.js --help
npm pack --dry-run --json
```

For Python package checks:

```bash
python -m pip install build
python -m build
python -m pip install --force-reinstall dist\xuxiang_agent-0.5.0b1-py3-none-any.whl
thinkflow --help
```

For npm package checks, confirm the dry-run file list excludes local secrets and generated artifacts:

```text
config.json
.env
.thinkflow/
release/
dist/
build/
bench_agent_efficiency/
bench_compare_latest/
bench_web_compare/
node_modules/
```
