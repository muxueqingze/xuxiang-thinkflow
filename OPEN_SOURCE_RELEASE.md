# Xuxiang ThinkFlow v0.5 Open Source Release Checklist

This file records the intended release shape for the `0.5.0-beta.1` open-source release.

## Names

- Chinese name: 续想
- English name: ThinkFlow
- CLI commands: `thinkflow`, `xuxiang`
- GitHub repository: `xuxiang-thinkflow`
- npm package: `xuxiang-agent`
- Package version: `0.5.0-beta.1`
- Author: 沐雪清泽

## Release Shapes

### npm package

The npm package is the user-facing CLI package. It should contain:

- `bin/`
- `scripts/`
- `src/*.py`
- `run.py`
- `requirements.txt`
- `README.md`
- `README.en.md`
- `LICENSE`
- `CITATION.cff`
- `SECURITY.md`
- `PROTOCOL.md`
- `DESIGN.md`
- `config.example.json`
- selected `docs/*.md`

It must not contain:

- `.env`
- `config.json`
- `.thinkflow/`
- `release/`
- `dist/`
- `build/`
- raw benchmark workspaces
- logs
- `node_modules`
- Python cache files

npm registry install:

```bash
npm install -g xuxiang-agent@beta
```

Publish command:

```bash
npm publish --tag beta
```

### GitHub source tree

The GitHub repository is the full engineering package. It should contain:

- source code
- tests
- docs
- benchmark scripts
- clean prompts
- curated benchmark reports
- reference screenshots
- packaging metadata
- CI workflow

It should not track generated release artifacts. Use GitHub Releases for tarballs and archives.

## Technical Documents

- `docs/predictable-tool-calls.md`: theoretical route.
- `docs/xuxiang-streaming-agent.md`: implementation route, state machine, queue, ledger, recovery, and benchmark evidence.
- `PROTOCOL.md`: canonical `tf-*` command protocol.
- `DESIGN.md`: architecture and implementation notes.

## Verification Gates

Run before publishing:

```bash
python tests/run_all.py
python -m compileall -q src tests bench run.py
node bin/thinkflow.js --help
npm pack --dry-run --json
git diff --check
```

The npm dry-run file list must exclude secrets, local config, generated artifacts, and raw benchmark workspaces.

## Secret and Privacy Checks

Before a public release, scan:

- current tracked files
- npm package dry-run file list
- full git history, if publishing an existing repository
- GitHub Actions workflow files
- release assets

Keywords and patterns to check:

- `sk-`
- `apiKey`
- `api_key`
- `password`
- `token`
- `Bearer`
- common email domains
- phone-number-like strings
- local absolute paths
- provider-specific private endpoints

If an already-public repository history contains private data, prefer rotating any exposed credential and recreating the public repository from a clean single commit.

## Benchmark Assets

Curated benchmark material lives in:

```text
bench/agent_comparison_20260704/
```

Publish only scripts, clean prompts, curated reports, and reference screenshots. Do not publish generated `runs*/`, raw logs, `node_modules`, local sessions, or temporary workspaces.

## Attribution and Contact

The project is currently published under the online name `沐雪清泽`.

It is an experimental agent harness and technical-route validation project, not a commercial service commitment or a statement on behalf of any company or institution.

For citations, reproduction questions, engineering collaboration, or security issues, prefer GitHub Issues / Discussions or the contact methods listed on the author's GitHub profile.
