# Security Policy

## Supported Versions

The current development source is on `codex/thinkflow-desktop-v06` (0.8.0 development).
The npm preview channel `next` provides 0.8.0 at commit `6d85c06`; `latest` provides 0.5.1.
Security fixes target the current development line; publication is a separate step.

| Version | Supported |
|---|---|
| Current development source | Yes |
| `0.8.0` npm preview | Source updates are tracked on the development branch |
| `0.5.1` npm stable | Upgrade to `next` for the 0.8.0 preview |
| older versions | No |

## Reporting a Vulnerability

Please do not publish exploit details in a public issue before the issue has
been reviewed.

For security-sensitive reports, use the contact methods listed on the author's
GitHub profile. For non-sensitive security hardening suggestions, GitHub Issues
or Discussions are preferred.

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
