# Security Policy

## Supported Versions

The current source is on `main`. npm `latest` and `next`, and the Windows
desktop release, provide 0.10.0. Security fixes target `main` and the current release.

| Version | Supported |
|---|---|
| `main` | Yes |
| `0.10.0` CLI / Desktop | Yes |
| Earlier releases | Upgrade to 0.10.0 |

## Reporting a Vulnerability

Please do not publish exploit details in a public issue before the issue has
been reviewed.

For security-sensitive reports, use GitHub's
[private vulnerability report](https://github.com/muxueqingze/xuxiang-thinkflow/security/advisories/new).
Ordinary bugs and feature suggestions belong in GitHub Issues.

Useful report details include:

- affected version or commit
- operating system and Python/Node versions
- minimal reproduction steps
- expected behavior and actual behavior
- whether the issue requires a malicious model output, a malicious workspace,
  or a malicious third-party URL

## Scope

ThinkFlow executes local file and shell tools under configurable policies. The
default release posture is conservative:

- file tools are scoped to the current working directory by default
- sensitive files such as `.env` and private keys are blocked by default
- bash defaults to the safe policy
- API keys are not inherited by bash subprocesses by default

Reports about escaping these defaults, leaking secrets, unsafe path handling,
or unsafe command execution are in scope.

## Desktop boundary

The renderer has no Node integration and uses an allowlisted preload API. The
Python service communicates through private stdio; there is no local HTTP server.
Model content is treated as text, not executable HTML. Keys are encrypted with
Electron safeStorage and are excluded from snapshots, exports and public state.

Tool policies are application-level checks, not an OS sandbox. Shell commands
can have effects beyond file-tool path checks. Desktop balanced mode asks once
per high-risk operation; approval does not alter future permissions.

Cancellation is not rollback. Crash-interrupted or cancelled operations may
have partially completed; unresolved journal entries require workspace review
before continuing. The journal does not guarantee exactly-once execution after
power loss, and concurrent external writers can still change workspace files.
