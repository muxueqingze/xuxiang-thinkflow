"""Analyze saved same-prompt benchmark run records.

The script is deliberately offline: it reads JSON files in runs/ and writes a
Markdown summary. It never calls an agent or model provider.
"""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
RUNS = ROOT / "runs"
REPORTS = ROOT / "reports"
SUMMARY = REPORTS / "summary.md"


def load_runs() -> list[dict]:
    records: list[dict] = []
    for path in sorted(RUNS.glob("*.json")):
        with path.open("r", encoding="utf-8-sig") as f:
            record = json.load(f)
        record["_file"] = path.name
        records.append(record)
    return records


def usage(record: dict, key: str) -> int | float:
    return (record.get("usage") or {}).get(key, 0) or 0


def cached_free(record: dict) -> int | float:
    effective = usage(record, "effective_input_tokens")
    if not effective:
        effective = max(0, usage(record, "prompt_tokens") - usage(record, "cached_tokens"))
    return effective + usage(record, "completion_tokens")


def row(record: dict) -> list[str]:
    return [
        str(record.get("agent_id", "")),
        str(record.get("tier", "")),
        str(record.get("model", "")),
        str(usage(record, "api_calls")),
        str(usage(record, "prompt_tokens")),
        str(usage(record, "cached_tokens")),
        str(usage(record, "completion_tokens")),
        str(usage(record, "total_tokens")),
        str(cached_free(record)),
        "yes" if record.get("build_passed") else "no",
        "yes" if record.get("delivery_compliant") else "no",
    ]


def markdown(records: list[dict]) -> str:
    headers = [
        "agent",
        "tier",
        "model",
        "api_calls",
        "prompt",
        "cached",
        "completion",
        "total",
        "cached_free",
        "build",
        "delivery",
    ]
    lines = ["# Controlled Same-Prompt Benchmark Summary", ""]
    if not records:
        lines.append("No run records found in `runs/*.json`.")
        return "\n".join(lines) + "\n"

    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join("---" for _ in headers) + " |")
    for record in records:
        lines.append("| " + " | ".join(row(record)) + " |")

    valid = [r for r in records if r.get("build_passed") and r.get("delivery_compliant")]
    if len(valid) >= 2:
        best_calls = min(valid, key=lambda r: usage(r, "api_calls"))
        best_tokens = min(valid, key=lambda r: usage(r, "total_tokens"))
        best_cached_free = min(valid, key=cached_free)
        lines.extend([
            "",
            "## Winners Among Delivery-Compliant Runs",
            "",
            f"- Fewest API calls: `{best_calls.get('agent_id')}`",
            f"- Fewest total tokens: `{best_tokens.get('agent_id')}`",
            f"- Fewest cached-free tokens: `{best_cached_free.get('agent_id')}`",
        ])

    return "\n".join(lines) + "\n"


def main() -> int:
    records = load_runs()
    REPORTS.mkdir(parents=True, exist_ok=True)
    text = markdown(records)
    with SUMMARY.open("w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
