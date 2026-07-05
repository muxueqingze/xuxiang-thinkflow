from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
RUNS = ROOT / "runs"
REPORTS = ROOT / "reports"


FRONTEND_REQUIRED = [
    "package.json",
    "index.html",
    "src/main.tsx",
    "src/App.tsx",
    "src/types.ts",
    "src/data/initialRuns.ts",
    "src/utils/metrics.ts",
    "src/styles.css",
    "README.md",
]

NOVEL_REQUIRED = [
    "README.md",
    "chapter-01.md",
    "chapter-02.md",
    "chapter-03.md",
    "chapter-04.md",
    "chapter-05.md",
]


def load_metrics() -> list[dict[str, Any]]:
    records = []
    for path in sorted(RUNS.glob("*/*/metrics.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            data = {
                "task": path.parent.parent.name,
                "agent": path.parent.name,
                "metrics_error": str(exc),
            }
        data["validation"] = validate_run(path.parent, data.get("task", ""))
        records.append(data)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return records


def validate_run(run_dir: Path, task: str) -> dict[str, Any]:
    if task == "frontend_app":
        return validate_frontend(run_dir)
    if task == "novel_five_chapters":
        return validate_novel(run_dir)
    return {"status": "unknown_task", "ok": False}


def validate_frontend(run_dir: Path) -> dict[str, Any]:
    project_root = find_project_root(run_dir)
    missing = [rel for rel in FRONTEND_REQUIRED if not (project_root / rel).exists()]
    component_files = list((project_root / "src" / "components").glob("*.tsx")) if (project_root / "src" / "components").exists() else []
    build = None
    if not missing and (project_root / "package.json").exists():
        build = run_command([npm_command(), "run", "build"], cwd=project_root, timeout=180)
    return {
        "ok": not missing and bool(component_files) and (build or {}).get("exit_code") == 0,
        "project_root": str(project_root),
        "missing_required_files": missing,
        "component_count": len(component_files),
        "npm_build": build,
    }


def find_project_root(run_dir: Path) -> Path:
    if (run_dir / "package.json").exists():
        return run_dir
    candidates = [p for p in run_dir.iterdir() if p.is_dir() and (p / "package.json").exists()]
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        return sorted(candidates, key=lambda p: p.name)[0]
    return run_dir


def validate_novel(run_dir: Path) -> dict[str, Any]:
    missing = [rel for rel in NOVEL_REQUIRED if not (run_dir / rel).exists()]
    chapters = []
    for rel in NOVEL_REQUIRED[1:]:
        path = run_dir / rel
        if path.exists():
            text = path.read_text(encoding="utf-8", errors="replace")
            chapters.append({
                "path": rel,
                "chars": len(text),
                "non_ws_chars": len("".join(text.split())),
            })
    return {
        "ok": not missing and len(chapters) == 5 and all(c["non_ws_chars"] >= 800 for c in chapters),
        "missing_required_files": missing,
        "chapters": chapters,
        "total_non_ws_chars": sum(c["non_ws_chars"] for c in chapters),
    }


def run_command(cmd: list[str], cwd: Path, timeout: int) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout,
        )
        return {
            "command": cmd,
            "exit_code": proc.returncode,
            "stdout_tail": proc.stdout[-4000:],
            "stderr_tail": proc.stderr[-4000:],
        }
    except subprocess.TimeoutExpired:
        return {"command": cmd, "exit_code": "timeout"}


def npm_command() -> str:
    return "npm.cmd" if __import__("os").name == "nt" else "npm"


def write_reports(records: list[dict[str, Any]]) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "metrics.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# Agent Comparison Summary", ""]
    lines.append("## Metric Scope")
    lines.append("")
    lines.append("- `valid` is produced by task-specific validation, not by process exit code.")
    lines.append("- Frontend validation requires required files, component files, and `npm run build` success.")
    lines.append("- Novel validation requires `README.md` plus `chapter-01.md` through `chapter-05.md`, each with at least 800 non-whitespace characters.")
    lines.append("- Token fields are reported only when the agent/provider exposes usage data. In this run, ThinkFlow's provider did not emit token usage despite `--stream-usage`, so its token fields are `n/a` in the summary.")
    lines.append("")
    lines.append("| task | agent | valid | exit | seconds | api_calls | input | output | total | files | bytes | notes |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|")
    for record in records:
        usage = record.get("usage", {}) or {}
        artifacts = record.get("artifacts", {}) or {}
        validation = record.get("validation", {}) or {}
        notes = ""
        missing = validation.get("missing_required_files") or []
        if missing:
            notes = "missing " + ", ".join(missing[:4])
            if len(missing) > 4:
                notes += f" +{len(missing) - 4}"
        build = validation.get("npm_build")
        if build and build.get("exit_code") not in (0, None):
            notes = (notes + "; " if notes else "") + f"build={build.get('exit_code')}"
        token_usage_unavailable = (
            record.get("agent") == "thinkflow"
            and usage.get("api_calls")
            and usage.get("total_tokens") in (0, None)
        )
        if token_usage_unavailable:
            notes = (notes + "; " if notes else "") + "token usage unavailable from provider"
        lines.append(
            "| {task} | {agent} | {valid} | {exit_code} | {seconds} | {api} | {inp} | {out} | {total} | {files} | {bytes} | {notes} |".format(
                task=record.get("task"),
                agent=record.get("agent"),
                valid="yes" if validation.get("ok") else "no",
                exit_code=record.get("exit_code"),
                seconds=record.get("duration_seconds"),
                api=usage.get("api_calls"),
                inp="n/a" if token_usage_unavailable else usage.get("input_tokens"),
                out="n/a" if token_usage_unavailable else usage.get("output_tokens"),
                total="n/a" if token_usage_unavailable else usage.get("total_tokens"),
                files=artifacts.get("file_count"),
                bytes=artifacts.get("total_bytes"),
                notes=notes,
            )
        )
    lines.append("")
    lines.append("## Generated Project Paths")
    lines.append("")
    for record in records:
        validation = record.get("validation", {}) or {}
        project_root = validation.get("project_root") or record.get("cwd")
        lines.append(f"- `{record.get('task')}` / `{record.get('agent')}`: `{project_root}`")
    (REPORTS / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    records = load_metrics()
    write_reports(records)
    print(REPORTS / "summary.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
