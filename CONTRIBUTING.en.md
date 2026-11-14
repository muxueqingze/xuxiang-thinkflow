# Contributing to ThinkFlow

[简体中文](CONTRIBUTING.md)

Code, documentation, provider integrations, interaction improvements and reproducible task examples are welcome. ThinkFlow aims to complete engineering work with fewer unnecessary model round trips: execute predictable operations continuously, batch independent information requests and provide the information needed for the current task.

## Choose an entry point

| What you need | Where to go |
|---|---|
| Report a bug | [Bug report](https://github.com/muxueqingze/xuxiang-thinkflow/issues/new?template=bug_report.yml): version, environment, actual and expected behavior, and reproduction steps |
| Suggest an improvement | [Feature or contribution proposal](https://github.com/muxueqingze/xuxiang-thinkflow/issues/new?template=feature_request.yml): a concrete use case, the current limitation and the desired outcome; an implementation plan is optional |
| Ask about installation, configuration or usage | Read the [README](README.en.md) and [configuration guide](docs/configuration.md), or open a [usage question](https://github.com/muxueqingze/xuxiang-thinkflow/issues/new?template=question.yml) |
| Report a vulnerability or exposed credential | Use [private vulnerability reporting](https://github.com/muxueqingze/xuxiang-thinkflow/security/advisories/new); see [SECURITY.md](SECURITY.md) |

English and Chinese are both welcome. Search existing [issues and PRs](https://github.com/muxueqingze/xuxiang-thinkflow/issues) and keep related details in the same discussion. Share relevant log excerpts without credentials or personal session data. Blank issues remain available when the forms do not fit.

## Find the implementation

Use the [code map](docs/代码地图.md) and [project instructions](AGENTS.md) to load only the relevant modules and documents.

| Area | Entry points |
|---|---|
| Tools, providers and execution efficiency | [AgentLoop](src/agent_loop.py), [ToolRegistry](src/tool_registry.py), [protocol](PROTOCOL.md) |
| Context, compaction and session recovery | [ConversationContext](src/conversation.py), [SessionStore](src/session.py), [configuration and SDK](docs/configuration.md) |
| CLI and desktop interaction | [CLI guide](docs/cli-interaction.md), [desktop development](desktop/README.md); shared visual implementation: [styles.css](desktop/renderer/styles.css) |
| Task quality and efficiency comparisons | [Existing benchmark entry](bench/efficiency_20261011/README.md); validate artifacts before comparing whole-task requests and usage |

[help wanted](https://github.com/muxueqingze/xuxiang-thinkflow/labels/help%20wanted) marks scoped work where contributions are welcome. [good first issue](https://github.com/muxueqingze/xuxiang-thinkflow/labels/good%20first%20issue) is reserved for small tasks with an entry point, scope and expected result.

## Develop and validate

Clone your fork or this repository, then run these commands from its root with Python 3.12+:

```bash
python -m pip install -e .
python run.py --help
```

An existing project virtual environment is fine. Help, configuration commands and local regression tests do not need model credentials. Configure actual model runs through the [configuration guide](docs/configuration.md).

Desktop source development requires Node.js **22.12+**. Startup, isolated development data and interaction scripts are maintained in the [desktop guide](desktop/README.md). Installer build dependencies are described in [desktop development and builds](docs/desktop.md#开发与构建); most contributions do not need a new installer.

Choose validation for the behavior you changed and record the commands and outcomes in the PR:

| Change | Validation entry |
|---|---|
| Local Python behavior | Select its test module, for example `python -m unittest discover -s tests -p test_workspace_search.py` |
| Core execution, protocol or sessions | Reproduce the issue and cover its direct effects; the consolidated suite is `python tests/run_all.py` |
| Desktop interfaces | `npm --prefix desktop test` |
| Visible interaction | Use the [existing Electron scripts](desktop/README.md) and include the relevant interaction, screenshot or short recording |
| Documentation or forms | Check links, commands, fields and agreement with the implementation |

CI currently runs Python regression and desktop interface tests on Windows with Python 3.12 and Node 22. A first-time external contribution may wait for a maintainer to approve CI. Paid model evaluations are separate; an ordinary PR does not need an API key or a full benchmark rerun. Efficiency claims should identify the model, versions, task, artifact validation and complete usage, including failed attempts.

Use a dedicated directory under project `temp/` for experiments. Stop its processes and remove disposable output when finished; keep reusable scripts and necessary evidence with the relevant tests or documentation.

## Submit and review

The maintainer develops and releases directly from `main`. External contributors prepare changes in their own forks and open PRs targeting this repository's `main`; no repository write access is needed.

1. Small fixes may go straight to a PR. For substantial changes to the tool protocol, defaults, SDK interfaces or session format, discuss the use case and approach in an issue first to align scope early.
2. Keep each PR focused on one problem. Reuse the execution, receipt and persistence interfaces. Prefer existing configuration, tool registration and SDK extension points for specialized behavior.
3. Commit under your own Git identity. Describe the behavioral change in the title, then use the [PR template](.github/pull_request_template.md) to explain the change, why it works and how you verified it.
4. The maintainer reviews CI, the implementation and actual artifacts. Continue follow-up changes in the same PR. The maintainer handles versions, release notes and distribution after merging.

The submitter should understand the implementation and its effects, and report actual validation. Engineering guidance stays replaceable by users; the existing framework continues to own the protocol and execution facts.

## Sources and attribution

Contributions are distributed under the project's [MIT license](LICENSE), with contributor attribution retained. Identify third-party code, assets and new dependencies, and retain their licenses and notices. There is currently no additional CLA or DCO sign-off process, and existing commit authors are not rewritten.
