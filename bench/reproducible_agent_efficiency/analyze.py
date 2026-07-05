"""Analyze the fixed TK/CC same-prompt benchmark snapshot.

This script intentionally uses only the saved JSON fixture so the case can be
rechecked without calling either agent.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPORT = ROOT / "usage_compare_report.latest.json"


def load_report() -> dict:
    with REPORT.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def metric(row: dict, key: str) -> int | float:
    value = row.get(key)
    if value is None:
        raise KeyError(f"missing metric: {key}")
    return value


def main() -> int:
    data = load_report()
    tk = data["tk"]
    cc = data["cc"]

    checks = {
        "api_calls": metric(tk, "api_calls") < metric(cc, "api_calls"),
        "total_tokens": metric(tk, "total_tokens") < metric(cc, "total_tokens"),
        "completion_tokens": metric(tk, "completion_tokens") < metric(cc, "completion_tokens"),
        "cached_free_cost": (
            metric(tk, "effective_input_tokens") + metric(tk, "completion_tokens")
        ) < (
            metric(cc, "effective_input_tokens") + metric(cc, "completion_tokens")
        ),
    }

    print("ThinkFlow/TK vs Claude Code/CC fixed benchmark")
    print(f"TK calls: {tk['api_calls']} | CC calls: {cc['api_calls']}")
    print(f"TK total tokens: {tk['total_tokens']} | CC total tokens: {cc['total_tokens']}")
    print(
        "cached-free tokens: "
        f"TK={tk['effective_input_tokens'] + tk['completion_tokens']} "
        f"CC={cc['effective_input_tokens'] + cc['completion_tokens']}"
    )

    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        print("FAILED checks: " + ", ".join(failed))
        return 1

    print("PASS: TK keeps the cost advantage on this fixed snapshot.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
