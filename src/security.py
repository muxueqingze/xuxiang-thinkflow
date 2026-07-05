"""
ThinkFlow Security Policy

默认安全策略面向开源开箱即用：限制文件操作在 cwd 内，阻止常见密钥文件读取，
并拦截明显危险的 shell 命令。高级用户可以显式放开。
"""

import fnmatch
import os
import re
from dataclasses import dataclass, field
from typing import Optional


DEFAULT_SECRET_PATTERNS = (
    ".env",
    ".env.*",
    "config.json",
    "config.local.json",
    "config.*.local.json",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "id_rsa",
    "id_ed25519",
    ".npmrc",
    ".pypirc",
    "credentials.json",
    "secrets.*",
)

SENSITIVE_ASSIGNMENT_RE = re.compile(
    r'(?i)(?P<prefix>["\']?(?:api[_-]?key|token|secret|password|access[_-]?token|refresh[_-]?token|bearer[_-]?token)["\']?\s*[:=]\s*)(?P<quote>["\']?)(?P<value>[^"\'\s,}]+)(?P=quote)'
)

DEFAULT_DANGEROUS_COMMANDS = (
    r"\brm\s+-rf\s+[/~]?",
    r"\bRemove-Item\b.*\b-Recurse\b.*\b-Force\b",
    r"\bdel\b.*\s/(?:s|q)\b",
    r"\bformat\b\s+[A-Za-z]:",
    r"\bshutdown\b",
    r"\breboot\b",
    r"\breg\s+(?:add|delete|import)\b",
    r"\bSet-ExecutionPolicy\b",
    r"\bmkfs\.",
    r":\(\)\s*\{",
)

DEFAULT_ENV_PASSTHROUGH = (
    "PATH",
    "PATHEXT",
    "SystemRoot",
    "WINDIR",
    "COMSPEC",
    "HOME",
    "USERPROFILE",
    "TEMP",
    "TMP",
    "SHELL",
    "TERM",
)


def normalize_security_profile(profile: str) -> str:
    value = (profile or "balanced").strip().lower()
    aliases = {
        "readonly": "read-only",
        "read_only": "read-only",
        "read-only": "read-only",
        "locked": "locked",
        "balanced": "balanced",
        "workspace-write": "balanced",
        "workspace_write": "balanced",
        "workspace": "balanced",
        "open": "open",
        "danger-full-access": "open",
        "danger_full_access": "open",
        "full-access": "open",
        "full_access": "open",
        "unrestricted": "open",
    }
    return aliases.get(value, value)


@dataclass
class SecurityPolicy:
    """Executor security policy."""

    allowed_roots: Optional[list[str]] = None
    allow_sensitive_paths: bool = False
    secret_patterns: tuple[str, ...] = DEFAULT_SECRET_PATTERNS
    bash_policy: str = "safe"  # off / safe / unrestricted
    approval_mode: str = "auto"  # auto / approve_all / request_all
    read_only: bool = False
    dangerous_command_patterns: tuple[str, ...] = DEFAULT_DANGEROUS_COMMANDS
    bash_timeout_seconds: float = 120.0
    max_bash_output_chars: int = 80_000
    env_passthrough: list[str] = field(default_factory=lambda: list(DEFAULT_ENV_PASSTHROUGH))

    @classmethod
    def from_config(cls, config: dict, cwd: str) -> "SecurityPolicy":
        security = config.get("security", {}) or {}
        profile = normalize_security_profile(security.get("profile", "balanced"))
        if profile == "read-only":
            defaults = {
                "allowed_roots": [cwd],
                "allow_sensitive_paths": False,
                "bash_policy": "off",
                "approval_mode": "request_all",
                "env_passthrough": DEFAULT_ENV_PASSTHROUGH,
                "read_only": True,
            }
        elif profile == "locked":
            defaults = {
                "allowed_roots": [cwd],
                "allow_sensitive_paths": False,
                "bash_policy": "off",
                "approval_mode": "request_all",
                "env_passthrough": DEFAULT_ENV_PASSTHROUGH,
                "read_only": False,
            }
        elif profile == "open":
            defaults = {
                "allowed_roots": None,
                "allow_sensitive_paths": True,
                "bash_policy": "unrestricted",
                "env_passthrough": ["*"],
                "approval_mode": "approve_all",
                "read_only": False,
            }
        elif profile == "balanced":
            defaults = {
                "allowed_roots": [cwd],
                "allow_sensitive_paths": False,
                "bash_policy": "safe",
                "approval_mode": "auto",
                "env_passthrough": DEFAULT_ENV_PASSTHROUGH,
                "read_only": False,
            }
        else:
            raise ValueError(f"unknown security.profile: {profile}")

        if "allowed_roots" in security:
            roots = security.get("allowed_roots")
        else:
            roots = defaults["allowed_roots"]
        return cls(
            allowed_roots=roots,
            allow_sensitive_paths=bool(security.get("allow_sensitive_paths", defaults["allow_sensitive_paths"])),
            bash_policy=security.get("bash_policy", defaults["bash_policy"]),
            approval_mode=security.get("approval_mode", defaults["approval_mode"]),
            read_only=bool(security.get("read_only", defaults["read_only"])),
            bash_timeout_seconds=float(security.get("bash_timeout_seconds", 120.0)),
            max_bash_output_chars=int(security.get("max_bash_output_chars", 80_000)),
            env_passthrough=list(security.get("env_passthrough", defaults["env_passthrough"])),
        )

    def normalized_roots(self, cwd: str) -> Optional[list[str]]:
        if self.allowed_roots is None:
            return None
        roots = []
        for root in self.allowed_roots:
            expanded = os.path.expanduser(root)
            if not os.path.isabs(expanded):
                expanded = os.path.join(cwd, expanded)
            roots.append(os.path.abspath(expanded))
        return roots

    def check_write_allowed(self, operation: str):
        if self.read_only and operation in {"write", "append", "mkdir", "touch", "copy", "edit", "bash"}:
            raise PermissionError(f"read-only permission mode blocks side-effect operation: {operation}")

    def check_path(self, path: str, allowed_roots: Optional[list[str]], operation: str):
        self.check_write_allowed(operation)
        if allowed_roots is not None:
            allowed = False
            for root in allowed_roots:
                try:
                    if os.path.commonpath([path, root]) == root:
                        allowed = True
                        break
                except ValueError:
                    continue
            if not allowed:
                raise PermissionError(f"路径不在允许范围内: {path}")

        if operation == "read" and not self.allow_sensitive_paths:
            basename = os.path.basename(path)
            for pattern in self.secret_patterns:
                if fnmatch.fnmatch(basename, pattern):
                    raise PermissionError(
                        f"拒绝读取疑似密钥文件: {basename}。如确需读取，请显式开启 allow_sensitive_paths。"
                    )

    def check_bash(self, cmd: str):
        self.check_write_allowed("bash")
        if self.bash_policy == "off":
            raise PermissionError("bash 工具已被 security.bash_policy=off 禁用")
        if self.bash_policy == "unrestricted":
            return
        if self.bash_policy != "safe":
            raise PermissionError(f"未知 bash_policy: {self.bash_policy}")

        for pattern in self.dangerous_command_patterns:
            if re.search(pattern, cmd, flags=re.IGNORECASE):
                raise PermissionError(f"拒绝执行危险命令: {cmd}")

    def command_env(self) -> dict:
        if "*" in self.env_passthrough:
            return os.environ.copy()
        env = {}
        for name in self.env_passthrough:
            if name in os.environ:
                env[name] = os.environ[name]
        return env

    def redact_text(self, text: str) -> str:
        """Redact obvious secret assignments before returning text to the model."""
        return SENSITIVE_ASSIGNMENT_RE.sub(r"\g<prefix>\g<quote>[REDACTED]\g<quote>", text)

