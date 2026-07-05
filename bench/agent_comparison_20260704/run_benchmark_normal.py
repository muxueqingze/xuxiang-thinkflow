from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from .analyze_runs import validate_run
    from .clean_prompts import task_prompt, write_prompts
    from .run_benchmark import PROJECT_ROOT, inspect_artifacts
except ImportError:
    from analyze_runs import validate_run
    from clean_prompts import task_prompt, write_prompts
    from run_benchmark import PROJECT_ROOT, inspect_artifacts


ROOT = Path(__file__).resolve().parent
RUNS = ROOT / os.environ.get("BENCH_RUNS_DIR", "runs_normal_app")
REPORTS = ROOT / os.environ.get("BENCH_REPORTS_DIR", "reports_normal_app")
BENCH_MODEL = os.environ.get("BENCH_MODEL", "glm-5.2")

AGENTS = ["claude-code", "thinkflow"]
TASKS = ["frontend_app", "novel_five_chapters"]
MAX_OUTER_TURNS = 5
TURN_TIMEOUT_SECONDS = int(os.environ.get("BENCH_TURN_TIMEOUT_SECONDS", "1800"))


@dataclass
class ProcResult:
    command: list[str]
    display_command: list[str]
    duration_seconds: float
    exit_code: int | str
    stdout: str
    stderr: str
    timed_out: bool = False
    stdin_text: str | None = None


def main(argv: list[str]) -> int:
    write_prompts()
    tasks = [argv[1]] if len(argv) > 1 else TASKS
    agents = [argv[2]] if len(argv) > 2 else AGENTS
    RUNS.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)

    records = []
    for task in tasks:
        for agent in agents:
            print(f"RUN normal_app {task}/{agent}", flush=True)
            records.append(run_agent(task, agent))
            write_report(load_all_metrics())
    return 0


def run_agent(task: str, agent: str) -> dict[str, Any]:
    run_dir = RUNS / task / agent
    prepare_run_dir(run_dir)
    session_id = str(uuid.uuid4())
    session_path = run_dir / "thinkflow-session.json"
    base_prompt = build_initial_prompt(task)
    (run_dir / "initial_prompt.md").write_text(base_prompt, encoding="utf-8")

    turns: list[dict[str, Any]] = []
    started = time.time()
    final_validation: dict[str, Any] = {"ok": False}
    stopped_reason = "max_outer_turns"

    for turn_index in range(1, MAX_OUTER_TURNS + 1):
        prompt = base_prompt if turn_index == 1 else build_continuation_prompt(task, final_validation)
        prompt_path = run_dir / f"turn-{turn_index:02d}-prompt.md"
        prompt_path.write_text(prompt, encoding="utf-8")

        proc = run_turn(
            task=task,
            agent=agent,
            run_dir=run_dir,
            turn_index=turn_index,
            prompt_path=prompt_path,
            session_id=session_id,
            session_path=session_path,
        )
        (run_dir / f"turn-{turn_index:02d}-stdout.log").write_text(proc.stdout, encoding="utf-8", errors="replace")
        (run_dir / f"turn-{turn_index:02d}-stderr.log").write_text(proc.stderr, encoding="utf-8", errors="replace")

        final_validation = validate_run(run_dir, task)
        turn_record = {
            "turn": turn_index,
            "command": proc.display_command,
            "duration_seconds": round(proc.duration_seconds, 3),
            "exit_code": proc.exit_code,
            "timed_out": proc.timed_out,
            "stdout_bytes": len(proc.stdout.encode("utf-8", errors="replace")),
            "stderr_bytes": len(proc.stderr.encode("utf-8", errors="replace")),
            "usage": extract_turn_usage(agent, proc.stdout, session_path),
            "validation": final_validation,
        }
        turns.append(turn_record)
        (run_dir / "turns.json").write_text(json.dumps(turns, ensure_ascii=False, indent=2), encoding="utf-8")

        print(
            f"TURN_DONE {task}/{agent} turn={turn_index} exit={proc.exit_code} "
            f"valid={final_validation.get('ok')} duration={proc.duration_seconds:.1f}s",
            flush=True,
        )
        if final_validation.get("ok"):
            stopped_reason = "validation_passed"
            break
        if proc.timed_out:
            stopped_reason = "turn_timeout"
            break

    ended = time.time()
    metrics = {
        "benchmark": "normal_app_same_session",
        "model": BENCH_MODEL,
        "task": task,
        "agent": agent,
        "cwd": str(run_dir),
        "session_id": session_id if agent == "claude-code" else None,
        "session_path": str(session_path) if agent == "thinkflow" else None,
        "started_at": started,
        "ended_at": ended,
        "duration_seconds": round(ended - started, 3),
        "outer_turns": len(turns),
        "stopped_reason": stopped_reason,
        "exit_code": turns[-1]["exit_code"] if turns else None,
        "valid": bool(final_validation.get("ok")),
        "validation": final_validation,
        "usage": aggregate_usage(agent, turns, session_path),
        "turns": turns,
        "artifacts": inspect_artifacts(run_dir),
    }
    (run_dir / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    return metrics


def prepare_run_dir(run_dir: Path) -> None:
    if run_dir.exists():
        archive = run_dir.with_name(f"{run_dir.name}_old_{time.strftime('%Y%m%d_%H%M%S')}")
        shutil.move(str(run_dir), str(archive))
    run_dir.mkdir(parents=True, exist_ok=True)


def build_initial_prompt(task: str) -> str:
    shared = (
        "这是一次正常应用场景 benchmark，请在当前目录完成任务。\n"
        "要求：\n"
        "1. 不要只汇报下一步计划，必须持续执行到任务完整完成。\n"
        "2. 所有产物必须直接写在当前目录，不要创建包裹当前项目的上层目录。\n"
        "3. 如果需要验证，前端任务只运行 npm run build；不要启动 npm run dev、vite dev server 或任何长驻进程。\n"
        "4. 完成前请自行检查必要文件是否齐全。\n\n"
    )
    return shared + task_prompt(task)


def build_continuation_prompt(task: str, validation: dict[str, Any]) -> str:
    return (
        "独立验收没有通过。请继续在同一个会话、同一个当前目录中完成任务，不要重新开始。\n"
        "只补齐缺失和修复错误，保留已经有效的产物。\n"
        "前端任务禁止启动长驻 dev server，只允许用 npm run build 验证。\n"
        "验收失败信息如下：\n"
        f"{json.dumps(validation, ensure_ascii=False, indent=2)}\n"
        f"任务类型：{task}\n"
    )


def run_turn(
    task: str,
    agent: str,
    run_dir: Path,
    turn_index: int,
    prompt_path: Path,
    session_id: str,
    session_path: Path,
) -> ProcResult:
    prompt = prompt_path.read_text(encoding="utf-8")
    if agent == "claude-code":
        claude_bin = resolve_command("claude")
        if turn_index == 1:
            cmd = [
                claude_bin,
                "-p",
                "--output-format",
                "json",
                "--permission-mode",
                "bypassPermissions",
                "--model",
                BENCH_MODEL,
                "--session-id",
                session_id,
            ]
            display = [
                "claude",
                "-p",
                "--output-format",
                "json",
                "--permission-mode",
                "bypassPermissions",
                "--model",
                BENCH_MODEL,
                "--session-id",
                session_id,
                "<turn-prompt.md>",
            ]
        else:
            cmd = [
                claude_bin,
                "-p",
                "--output-format",
                "json",
                "--permission-mode",
                "bypassPermissions",
                "--model",
                BENCH_MODEL,
                "--resume",
                session_id,
            ]
            display = [
                "claude",
                "-p",
                "--output-format",
                "json",
                "--permission-mode",
                "bypassPermissions",
                "--model",
                BENCH_MODEL,
                "--resume",
                session_id,
                "<turn-prompt.md>",
            ]
    elif agent == "thinkflow":
        cmd = [
            sys.executable,
            str(PROJECT_ROOT / "run.py"),
            "--provider-profile",
            "opencode-go",
            "--model",
            BENCH_MODEL,
            "--stream-usage",
            "--max-auto-continues",
            "12",
            "--max-tokens",
            "20000",
            "--sandbox",
            "workspace-write",
            "--trust-workspace",
            "--cwd",
            str(run_dir),
            "--session",
            str(session_path),
            "--prompt-file",
            str(prompt_path),
        ]
        if turn_index > 1:
            cmd.insert(-2, "--resume")
        display = [
            "python",
            "run.py",
            "--provider-profile",
            "opencode-go",
            "--model",
            BENCH_MODEL,
            "--stream-usage",
            "--max-auto-continues",
            "12",
            "--max-tokens",
            "20000",
            "--sandbox",
            "workspace-write",
            "--trust-workspace",
            "--cwd",
            str(run_dir),
            "--session",
            str(session_path),
            "--resume" if turn_index > 1 else "",
            "--prompt-file",
            "<turn-prompt.md>",
        ]
        display = [part for part in display if part]
    else:
        raise ValueError(f"unknown agent: {agent}")
    stdin_text = prompt if agent == "claude-code" else None
    return run_process(cmd, display, run_dir, f"{task}/{agent}/turn{turn_index}", stdin_text=stdin_text)


def run_process(
    cmd: list[str],
    display: list[str],
    cwd: Path,
    label: str,
    stdin_text: str | None = None,
) -> ProcResult:
    started = time.time()
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.PIPE if stdin_text is not None else None,
    )
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []

    def pump(stream, chunks: list[str]) -> None:
        try:
            for line in iter(stream.readline, ""):
                chunks.append(line)
        finally:
            stream.close()

    threads = [
        threading.Thread(target=pump, args=(proc.stdout, stdout_parts), daemon=True),
        threading.Thread(target=pump, args=(proc.stderr, stderr_parts), daemon=True),
    ]
    for thread in threads:
        thread.start()
    if stdin_text is not None and proc.stdin is not None:
        proc.stdin.write(stdin_text)
        proc.stdin.close()

    timed_out = False
    while proc.poll() is None:
        elapsed = time.time() - started
        if elapsed > TURN_TIMEOUT_SECONDS:
            timed_out = True
            terminate_process_tree(proc)
            break
        print(
            f"HEARTBEAT {label} elapsed={int(elapsed)}s files={count_files(cwd)}",
            flush=True,
        )
        time.sleep(30)

    for thread in threads:
        thread.join(timeout=30)
    ended = time.time()
    return ProcResult(
        command=cmd,
        display_command=display,
        duration_seconds=ended - started,
        exit_code="timeout" if timed_out else proc.returncode,
        stdout="".join(stdout_parts),
        stderr="".join(stderr_parts),
        timed_out=timed_out,
        stdin_text=stdin_text,
    )


def terminate_process_tree(proc: subprocess.Popen) -> None:
    if sys.platform.startswith("win"):
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        proc.kill()


def resolve_command(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise FileNotFoundError(f"Cannot find command on PATH: {name}")
    return path


def count_files(path: Path) -> int:
    try:
        return sum(1 for p in path.rglob("*") if p.is_file())
    except OSError:
        return -1


def extract_turn_usage(agent: str, stdout: str, session_path: Path) -> dict[str, Any]:
    if agent == "claude-code":
        try:
            data = json.loads(stdout)
        except Exception as exc:
            return {"parse_error": str(exc)}
        usage = data.get("usage", {}) or {}
        model_usage = data.get("modelUsage") or {}
        model_totals = aggregate_claude_model_usage(model_usage)
        input_tokens = model_totals.get("input_tokens")
        output_tokens = model_totals.get("output_tokens")
        cache_read_tokens = model_totals.get("cache_read_input_tokens")
        cache_creation_tokens = model_totals.get("cache_creation_input_tokens")
        cost_usd = model_totals.get("cost_usd")
        if input_tokens is None:
            input_tokens = usage.get("input_tokens")
            output_tokens = usage.get("output_tokens")
            cache_read_tokens = usage.get("cache_read_input_tokens")
            cache_creation_tokens = usage.get("cache_creation_input_tokens")
            cost_usd = data.get("total_cost_usd")
        return {
            "api_calls": data.get("num_turns"),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_input_tokens": cache_read_tokens,
            "cache_creation_input_tokens": cache_creation_tokens,
            "total_tokens": safe_sum(input_tokens, output_tokens),
            "cost_usd": cost_usd,
            "duration_api_ms": data.get("duration_api_ms"),
            "ttft_ms": data.get("ttft_ms"),
            "stop_reason": data.get("stop_reason"),
            "terminal_reason": data.get("terminal_reason"),
            "result": data.get("result"),
            "raw_usage": usage,
            "model_usage": model_usage,
            "token_source": "modelUsage" if model_totals.get("input_tokens") is not None else "usage",
        }
    if agent == "thinkflow":
        if not session_path.exists():
            return {"parse_error": "missing session file"}
        return extract_thinkflow_session_usage(session_path)
    return {}


def aggregate_usage(agent: str, turns: list[dict[str, Any]], session_path: Path) -> dict[str, Any]:
    if agent == "thinkflow":
        return extract_thinkflow_session_usage(session_path)
    totals: dict[str, Any] = {
        "api_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
        "duration_api_ms": 0,
    }
    for turn in turns:
        usage = turn.get("usage", {}) or {}
        for key in ("api_calls", "input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "total_tokens", "duration_api_ms"):
            value = usage.get(key)
            if isinstance(value, (int, float)):
                totals[key] += value
        cost = usage.get("cost_usd")
        if isinstance(cost, (int, float)):
            totals["cost_usd"] += cost
    totals["cost_usd"] = round(totals["cost_usd"], 6)
    return totals


def aggregate_claude_model_usage(model_usage: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(model_usage, dict) or not model_usage:
        return {
            "input_tokens": None,
            "output_tokens": None,
            "cache_read_input_tokens": None,
            "cache_creation_input_tokens": None,
            "cost_usd": None,
        }
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cost_usd": 0.0,
    }
    found = False
    for item in model_usage.values():
        if not isinstance(item, dict):
            continue
        found = True
        totals["input_tokens"] += int(item.get("inputTokens") or 0)
        totals["output_tokens"] += int(item.get("outputTokens") or 0)
        totals["cache_read_input_tokens"] += int(item.get("cacheReadInputTokens") or 0)
        totals["cache_creation_input_tokens"] += int(item.get("cacheCreationInputTokens") or 0)
        totals["cost_usd"] += float(item.get("costUSD") or 0)
    if not found:
        return {
            "input_tokens": None,
            "output_tokens": None,
            "cache_read_input_tokens": None,
            "cache_creation_input_tokens": None,
            "cost_usd": None,
        }
    totals["cost_usd"] = round(totals["cost_usd"], 6)
    return totals


def extract_thinkflow_session_usage(session_path: Path) -> dict[str, Any]:
    if not session_path.exists():
        return {"parse_error": "missing session file"}
    try:
        data = json.loads(session_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"parse_error": str(exc)}
    usage = data.get("usage", {}) or {}
    totals = usage.get("totals", {}) or {}
    turns = usage.get("turns") or []
    commands = totals.get("total_commands")
    if commands is None:
        commands = sum(int((turn or {}).get("commands_executed") or 0) for turn in turns if isinstance(turn, dict))
    tool_segments = sum(int((turn or {}).get("tool_calls_traditional") or 0) for turn in turns if isinstance(turn, dict))
    return {
        "api_calls": totals.get("api_calls"),
        "input_tokens": totals.get("prompt_tokens"),
        "output_tokens": totals.get("completion_tokens"),
        "cache_read_input_tokens": totals.get("cached_tokens"),
        "cache_creation_input_tokens": None,
        "total_tokens": safe_sum(totals.get("prompt_tokens"), totals.get("completion_tokens")),
        "commands_executed": commands,
        "tool_call_segments": tool_segments,
        "estimated_saved_api_calls": totals.get("estimated_saved_api_calls"),
        "estimated_avoided_prompt_tokens": totals.get("estimated_avoided_prompt_tokens"),
        "raw": usage,
    }


def safe_sum(a: Any, b: Any) -> int | None:
    if a is None or b is None:
        return None
    return int(a) + int(b)


def load_all_metrics() -> list[dict[str, Any]]:
    records = []
    for path in sorted(RUNS.glob("*/*/metrics.json")):
        records.append(json.loads(path.read_text(encoding="utf-8")))
    return records


def write_report(records: list[dict[str, Any]]) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "metrics.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# Normal Application Same-Session Benchmark",
        "",
        "停止条件：同一会话多轮执行，每轮后独立验收；验收通过即停止，否则把失败项作为下一轮 prompt 继续。",
        "",
        "| task | agent | valid | turns | seconds | api_calls | commands | saved_calls | input | output | total | cache_read | cost_usd | files | bytes | stop | notes |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for record in records:
        usage = record.get("usage", {}) or {}
        artifacts = record.get("artifacts", {}) or {}
        validation = record.get("validation", {}) or {}
        unavailable = record.get("agent") == "thinkflow" and usage.get("api_calls") and usage.get("total_tokens") in (0, None)
        notes = []
        missing = validation.get("missing_required_files") or []
        if missing:
            notes.append("missing " + ", ".join(missing[:4]))
        if unavailable:
            notes.append("token usage unavailable from provider")
        lines.append(
            "| {task} | {agent} | {valid} | {turns} | {seconds} | {api} | {commands} | {saved} | {inp} | {out} | {total} | {cache} | {cost} | {files} | {bytes} | {stop} | {notes} |".format(
                task=record.get("task"),
                agent=record.get("agent"),
                valid="yes" if record.get("valid") else "no",
                turns=record.get("outer_turns"),
                seconds=record.get("duration_seconds"),
                api=format_cell(usage.get("api_calls")),
                commands=format_cell(usage.get("commands_executed")),
                saved=format_cell(usage.get("estimated_saved_api_calls")),
                inp="n/a" if unavailable else usage.get("input_tokens"),
                out="n/a" if unavailable else usage.get("output_tokens"),
                total="n/a" if unavailable else usage.get("total_tokens"),
                cache="n/a" if unavailable else usage.get("cache_read_input_tokens"),
                cost=format_cell(usage.get("cost_usd")),
                files=artifacts.get("file_count"),
                bytes=artifacts.get("total_bytes"),
                stop=record.get("stopped_reason"),
                notes="; ".join(notes),
            )
        )
    lines.extend(["", "## Project Paths", ""])
    for record in records:
        project_root = (record.get("validation") or {}).get("project_root") or record.get("cwd")
        if project_root:
            project_root = str(Path(project_root).resolve())
        lines.append(f"- `{record.get('task')}` / `{record.get('agent')}`: `{project_root}`")
    (REPORTS / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def format_cell(value: Any) -> Any:
    return "n/a" if value is None else value


def build_initial_prompt(task: str) -> str:
    shared = (
        "This is a normal-use benchmark. Complete the task in the current working directory.\n"
        "Rules:\n"
        "1. Do not only describe a plan. Continue executing until the requested artifact is complete.\n"
        "2. Write all deliverables directly into the current directory. Do not create a wrapper parent project directory.\n"
        "3. For frontend tasks, use npm run build for verification only. Do not start npm run dev, a Vite dev server, or any long-running process.\n"
        "4. Before finishing, check that all required files exist and that the relevant validation command succeeds.\n"
        "5. Keep the run reproducible: no external APIs, no manual browser interaction, and no hidden state outside the current directory.\n\n"
    )
    return shared + task_prompt(task)


def build_continuation_prompt(task: str, validation: dict[str, Any]) -> str:
    return (
        "Independent artifact validation did not pass. Continue in the same session and the same current directory; do not restart from scratch.\n"
        "Only fill missing files and fix errors. Preserve any already-valid artifacts.\n"
        "For frontend tasks, do not start a long-running dev server; use npm run build only.\n"
        "Validation failure details:\n"
        f"{json.dumps(validation, ensure_ascii=False, indent=2)}\n"
        f"Task type: {task}\n"
    )


def load_all_metrics() -> list[dict[str, Any]]:
    records = []
    for task in TASKS:
        for agent in AGENTS:
            path = RUNS / task / agent / "metrics.json"
            if path.exists():
                records.append(json.loads(path.read_text(encoding="utf-8")))
    return records


def write_report(records: list[dict[str, Any]]) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "metrics.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# Normal Application Same-Session Benchmark",
        "",
        "Stop condition: each agent continues in the same session. After every outer turn, an independent validator checks the artifacts. Passing validation stops the cell; otherwise the validation failure is sent back as the next prompt.",
        "",
        "| task | agent | artifact_validation | turns | seconds | api_calls | commands | saved_calls | input | output | total | cache_read | cost_usd | files | bytes | stop | notes |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for record in records:
        usage = record.get("usage", {}) or {}
        artifacts = record.get("artifacts", {}) or {}
        validation = record.get("validation", {}) or {}
        token_missing = record.get("agent") == "thinkflow" and usage.get("api_calls") and usage.get("total_tokens") in (0, None)
        notes = []
        missing = validation.get("missing_required_files") or []
        if missing:
            notes.append("missing " + ", ".join(missing[:4]))
        build = validation.get("npm_build")
        if build and build.get("exit_code") not in (0, None):
            notes.append(f"build={build.get('exit_code')}")
        if token_missing:
            notes.append("token usage missing")
        lines.append(
            "| {task} | {agent} | {valid} | {turns} | {seconds} | {api} | {commands} | {saved} | {inp} | {out} | {total} | {cache} | {cost} | {files} | {bytes} | {stop} | {notes} |".format(
                task=record.get("task"),
                agent=record.get("agent"),
                valid="pass" if record.get("valid") else "fail",
                turns=record.get("outer_turns"),
                seconds=record.get("duration_seconds"),
                api=format_cell(usage.get("api_calls")),
                commands=format_cell(usage.get("commands_executed")),
                saved=format_cell(usage.get("estimated_saved_api_calls")),
                inp="n/a" if token_missing else format_cell(usage.get("input_tokens")),
                out="n/a" if token_missing else format_cell(usage.get("output_tokens")),
                total="n/a" if token_missing else format_cell(usage.get("total_tokens")),
                cache="n/a" if token_missing else format_cell(usage.get("cache_read_input_tokens")),
                cost=format_cell(usage.get("cost_usd")),
                files=artifacts.get("file_count"),
                bytes=artifacts.get("total_bytes"),
                stop=record.get("stopped_reason"),
                notes="; ".join(notes),
            )
        )
    lines.extend(["", "## Project Paths", ""])
    for record in records:
        project_root = (record.get("validation") or {}).get("project_root") or record.get("cwd")
        if project_root:
            project_root = str(Path(project_root).resolve())
        lines.append(f"- `{record.get('task')}` / `{record.get('agent')}`: `{project_root}`")
    (REPORTS / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
