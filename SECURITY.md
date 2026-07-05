# Security Policy

## Supported Versions

`xuxiang-agent` is currently published as a beta package. Security fixes are
expected to target the latest beta line unless otherwise noted.

| Version | Supported |
|---|---|
| `0.5.0-beta.x` | Yes |
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
