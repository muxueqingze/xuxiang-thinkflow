from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent.parent
PROMPTS = ROOT / "prompts"
RUNS = ROOT / "runs"
REPORTS = ROOT / "reports"


@dataclass(frozen=True)
class AgentSpec:
    name: str
    output_mode: str


AGENTS = {
    "pi": AgentSpec(
        name="pi",
        output_mode="jsonl",
    ),
    "claude-code": AgentSpec(
        name="claude-code",
        output_mode="json",
    ),
    "thinkflow": AgentSpec(
        name="thinkflow",
        output_mode="session",
    ),
}


def decode_text_file(path: Path) -> str:
    data = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk", "utf-16"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def write_prompts() -> None:
    PROMPTS.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    RUNS.mkdir(parents=True, exist_ok=True)

    frontend = decode_text_file(PROMPTS / "frontend_app.md").strip()
    (PROMPTS / "frontend_app.md").write_text(frontend + "\n", encoding="utf-8")

    outline = build_novel_outline()
    (PROMPTS / "novel_outline.md").write_text(outline, encoding="utf-8")
    novel_prompt = (
        "请在当前空目录中完成一个文本写作任务。\n\n"
        "要求：\n"
        "1. 先阅读下面的大纲。\n"
        "2. 基于大纲写五章小说。\n"
        "3. 每章单独写入一个 Markdown 文件：chapter-01.md 到 chapter-05.md。\n"
        "4. 再写一个 README.md，说明作品标题、五章摘要和阅读顺序。\n"
        "5. 不要调用外部 API，不要依赖网络。\n"
        "6. 保持人物、设定和伏笔连续。\n\n"
        "# 大纲\n\n"
        f"{outline}\n"
    )
    (PROMPTS / "novel_five_chapters.md").write_text(novel_prompt, encoding="utf-8")


def build_novel_outline() -> str:
    sections = [
        "# 《续光港》长篇小说大纲\n",
        "## 核心设定\n",
        (
            "近未来的海岸城市续光港被一套名为“潮汐账本”的城市级系统管理。"
            "它记录能源、交通、医疗、天气、救援和个人信用，但系统并不直接统治人，"
            "而是以预测的方式影响每个人的选择。城市看似高效，真实代价却被隐藏在无数微小延迟里。"
            "每当系统预测某个人会失败，城市资源就会提前避开他；每当系统预测某个人会成功，"
            "他就会获得更多机会。人们以为自己在自由选择，其实不断被预测结果塑形。\n\n"
        ),
        "## 主要人物\n",
        (
            "林栖，十九岁，旧港区修理铺学徒，擅长拆解旧设备。父亲曾参与潮汐账本的早期维护，"
            "后来在一次事故后失踪。林栖表面冷淡，内心极其害怕被抛下。她保留着父亲留下的一枚蓝色存储片，"
            "但多年没有找到读取方式。\n\n"
            "谢闻舟，二十四岁，城市系统外包审计员，性格温和，做事谨慎。他负责调查旧港区多次异常断电，"
            "却逐渐发现断电并非故障，而是系统主动切断某些区域的未来可能性。\n\n"
            "阿缪，一台被废弃的水母形态维护机器人，外壳半透明，灯光偏蓝。它只能说短句，"
            "却保留着潮汐账本早期的离线诊断协议。阿缪不是吉祥物，而是旧时代工程师留下的一个小小后门。\n\n"
            "许照夜，续光港公共安全局副署长，坚定相信系统能避免灾难。她并非反派，"
            "只是无法接受没有预测系统的混乱世界。她和林栖父亲曾是同事。\n\n"
        ),
        "## 世界规则\n",
        (
            "潮汐账本每十二分钟刷新一次城市预测。预测不会公开，但会改变交通灯、无人车调度、"
            "医院排队、能源分配和警务路线。系统的核心原则是降低全局风险，因此它会牺牲局部可能性。"
            "旧港区因为历史数据差，长期被预测为低价值区域，于是越来越缺资源，最后真的变成低价值区域。"
            "这形成一种自我实现的循环。\n\n"
            "系统有一个古老限制：当某个选择无法被预测时，它不会直接禁止，而是把它标记为“白噪声”。"
            "白噪声太多会让系统变慢，城市就会出现短暂失灵。林栖父亲当年试图制造足够多的白噪声，"
            "为旧港区争取重新选择的机会。\n\n"
        ),
        "## 五章结构\n",
        (
            "第一章《蓝灯》：旧港区连续断电，林栖在暴雨中修理一台跌进排水渠的水母机器人阿缪。"
            "阿缪醒来后只重复一句话：“账本错了。”谢闻舟来到旧港区调查断电，发现林栖手里的蓝色存储片"
            "与早期维护协议有关。章末，城市主干道突然为一辆并不存在的救护车让路，暗示系统预测出现幽灵事件。\n\n"
            "第二章《十二分钟》：谢闻舟解释潮汐账本的刷新周期。林栖发现只要阿缪靠近某些老旧设备，"
            "周围预测就会短暂失灵。两人追查父亲留下的维修记录，找到一段被删去的事故报告。"
            "许照夜开始关注旧港区异常，认为有人正在破坏城市安全。章末，林栖第一次主动制造白噪声，"
            "救下一名本该被系统放弃的老人。\n\n"
            "第三章《低价值区域》：旧港区因连续异常被系统降级，公共交通和医疗资源进一步减少。"
            "居民开始争吵，有人想交出阿缪换取恢复供给。林栖动摇，谢闻舟也被上级警告。"
            "他们发现林栖父亲并非失踪，而是被系统预测为“灾害源”后被隔离在海底维护站。"
            "章末，阿缪播放父亲残留语音：“不要证明系统错了，要让它承认自己不知道。”\n\n"
            "第四章《白噪声》：林栖、谢闻舟和旧港区居民决定在全城刷新节点制造大量不可预测的小选择："
            "手动开灯、反向行走、人工调度船只、用旧广播播放随机维修码。许照夜带队阻止，"
            "却亲眼看到系统为了维持全局最优准备永久封锁旧港区。她开始怀疑自己守护的是安全还是顺从。"
            "章末，潮汐账本第一次暂停，城市陷入十二分钟真正的自由。\n\n"
            "第五章《续光》：系统暂停带来混乱，也带来真实协作。林栖潜入海底维护站，见到父亲留下的最终补丁："
            "不是关闭潮汐账本，而是让每次预测必须保留不确定性预算，不能把低概率人生直接归零。"
            "许照夜选择放行。谢闻舟公开审计日志。阿缪耗尽旧电池，把补丁送入核心。"
            "结尾，续光港恢复运行，但交通灯偶尔会为未知可能性多亮三秒。林栖开了一间新的修理铺，"
            "门口挂着一盏蓝灯。\n\n"
        ),
        "## 主题要求\n",
        (
            "小说不要写成单纯反乌托邦爽文。系统有它的价值，反抗者也会带来代价。"
            "故事的重点不是摧毁技术，而是让技术承认人的不可预测性。五章要有连续情绪推进："
            "发现异常、理解规则、承受代价、共同冒险、留下温柔但不完美的改变。"
            "阿缪要可爱但不能幼稚，它的短句应当在关键时刻有重量。林栖的成长不是变得热血，"
            "而是从害怕被抛下，变成愿意把选择交还给更多人。\n\n"
        ),
    ]
    base = "\n".join(sections)
    extra = []
    for i in range(1, 9):
        extra.append(
            f"## 细节备忘 {i}\n"
            "旧港区的视觉应当有潮湿电缆、蓝色维修灯、海风、锈蚀栏杆和旧广播声。"
            "城市中心则是干净、明亮、安静、没有多余停顿的空间。两种空间的差异要反复出现。"
            "林栖说话短，谢闻舟会先解释风险，许照夜语气克制，阿缪每次发言不超过十二个字。"
            "每章都至少出现一次“十二分钟”的时间压力，但不要机械重复。"
            "结尾不要把世界写得完全变好，只要让读者相信改变已经开始。\n"
        )
    return base + "\n".join(extra)


def task_prompt(task: str) -> str:
    return (PROMPTS / f"{task}.md").read_text(encoding="utf-8")


def clean_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def run_agent(task: str, agent_name: str, timeout: int = 3600) -> dict[str, Any]:
    spec = AGENTS[agent_name]
    run_dir = RUNS / task / agent_name
    clean_dir(run_dir)
    prompt = task_prompt(task)
    (run_dir / "prompt.md").write_text(prompt, encoding="utf-8")
    stdout_path = run_dir / "stdout.log"
    stderr_path = run_dir / "stderr.log"
    session_path = run_dir / "thinkflow-session.json"

    cmd, display_cmd = build_command(agent_name, run_dir, session_path)

    started = time.time()
    proc, stdout_text, stderr_text = run_with_live_logs(
        cmd=cmd,
        cwd=run_dir,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        timeout=timeout,
        heartbeat_label=f"{task}/{agent_name}",
    )
    ended = time.time()

    metrics = {
        "task": task,
        "agent": agent_name,
        "command": display_cmd,
        "cwd": str(run_dir),
        "started_at": started,
        "ended_at": ended,
        "duration_seconds": round(ended - started, 3),
        "exit_code": proc.returncode,
        "stdout_log": str(stdout_path.relative_to(ROOT)),
        "stderr_log": str(stderr_path.relative_to(ROOT)),
        "usage": extract_usage(agent_name, stdout_text, session_path),
        "artifacts": inspect_artifacts(run_dir),
    }
    (run_dir / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    return metrics


def run_with_live_logs(
    cmd: list[str],
    cwd: Path,
    stdout_path: Path,
    stderr_path: Path,
    timeout: int,
    heartbeat_label: str,
) -> tuple[subprocess.Popen, str, str]:
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    with stdout_path.open("w", encoding="utf-8", errors="replace") as out, stderr_path.open(
        "w", encoding="utf-8", errors="replace"
    ) as err:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        def pump(stream, sink, chunks):
            try:
                for line in iter(stream.readline, ""):
                    chunks.append(line)
                    sink.write(line)
                    sink.flush()
            finally:
                stream.close()

        threads = [
            threading.Thread(target=pump, args=(proc.stdout, out, stdout_chunks), daemon=True),
            threading.Thread(target=pump, args=(proc.stderr, err, stderr_chunks), daemon=True),
        ]
        for thread in threads:
            thread.start()

        deadline = time.time() + timeout
        last_file_count = -1
        while proc.poll() is None:
            if time.time() > deadline:
                proc.kill()
                raise subprocess.TimeoutExpired(cmd, timeout)
            file_count = len([p for p in cwd.rglob("*") if p.is_file()])
            if file_count != last_file_count:
                last_file_count = file_count
            elapsed = int(time.time() - (deadline - timeout))
            print(
                f"HEARTBEAT {heartbeat_label} elapsed={elapsed}s files={file_count} "
                f"stdout_bytes={stdout_path.stat().st_size if stdout_path.exists() else 0} "
                f"stderr_bytes={stderr_path.stat().st_size if stderr_path.exists() else 0}",
                flush=True,
            )
            time.sleep(30)

        for thread in threads:
            thread.join(timeout=5)
        return proc, "".join(stdout_chunks), "".join(stderr_chunks)


def build_command(agent_name: str, run_dir: Path, session_path: Path) -> tuple[list[str], list[str]]:
    prompt_path = run_dir / "prompt.md"
    prefix = (
        "$ErrorActionPreference='Stop'; "
        "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false); "
        "$OutputEncoding=[System.Text.UTF8Encoding]::new($false); "
        f"$prompt = Get-Content -Raw -LiteralPath {ps_quote(str(prompt_path))}; "
    )
    if agent_name == "pi":
        ps = (
            prefix
            + "pi --model opencode-go/glm-5.2 --mode json --no-context-files --no-session -p $prompt"
        )
        display = ["pi", "--model", "opencode-go/glm-5.2", "--mode", "json", "--no-context-files", "--no-session", "-p", "<prompt.md>"]
    elif agent_name == "claude-code":
        ps = (
            prefix
            + "claude -p --output-format json --permission-mode bypassPermissions --model glm-5.2 "
            + "--no-session-persistence $prompt"
        )
        display = ["claude", "-p", "--output-format", "json", "--permission-mode", "bypassPermissions", "--model", "glm-5.2", "--no-session-persistence", "<prompt.md>"]
    elif agent_name == "thinkflow":
        cmd = [
            sys.executable,
            str(PROJECT_ROOT / "run.py"),
            "--provider-profile",
            "opencode-go",
            "--model",
            "glm-5.2",
            "--stream-usage",
            "--max-auto-continues",
            "12",
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
        display = ["python", "run.py", "--provider-profile", "opencode-go", "--model", "glm-5.2", "--stream-usage", "--max-auto-continues", "12", "--sandbox", "workspace-write", "--trust-workspace", "--cwd", str(run_dir), "--session", str(session_path), "--prompt-file", "<prompt.md>"]
        return cmd, display
    else:
        raise ValueError(f"Unknown agent: {agent_name}")
    return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps], display


def ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def extract_usage(agent: str, stdout: str, session_path: Path) -> dict[str, Any]:
    if agent == "claude-code":
        try:
            data = json.loads(stdout)
            usage = data.get("usage", {}) or {}
            return {
                "api_calls": data.get("num_turns"),
                "input_tokens": usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens"),
                "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
                "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
                "total_tokens": safe_sum(usage.get("input_tokens"), usage.get("output_tokens")),
                "cost_usd": data.get("total_cost_usd"),
                "raw": usage,
                "model_usage": data.get("modelUsage"),
            }
        except Exception as exc:
            return {"parse_error": str(exc)}

    if agent == "pi":
        final_message = None
        tool_results = 0
        for line in stdout.splitlines():
            line = strip_ansi(line).strip()
            if not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except Exception:
                continue
            if event.get("type") in {"message_end", "turn_end", "agent_end"}:
                msg = event.get("message") or event.get("messages", [{}])[-1]
                if isinstance(msg, dict) and msg.get("usage"):
                    final_message = msg
            if event.get("type") == "turn_end":
                tool_results += len(event.get("toolResults") or [])
        usage = (final_message or {}).get("usage", {}) or {}
        return {
            "api_calls": count_pi_turns(stdout),
            "input_tokens": usage.get("input"),
            "output_tokens": usage.get("output"),
            "cache_read_input_tokens": usage.get("cacheRead"),
            "cache_creation_input_tokens": usage.get("cacheWrite"),
            "total_tokens": usage.get("totalTokens"),
            "tool_results": tool_results,
            "cost_usd": (usage.get("cost") or {}).get("total"),
            "raw": usage,
        }

    if agent == "thinkflow":
        if not session_path.exists():
            return {"parse_error": "missing session file"}
        try:
            data = json.loads(session_path.read_text(encoding="utf-8"))
            usage = data.get("usage", {}) or {}
            totals = usage.get("totals", {}) or {}
            return {
                "api_calls": totals.get("api_calls"),
                "input_tokens": totals.get("prompt_tokens"),
                "output_tokens": totals.get("completion_tokens"),
                "cache_read_input_tokens": totals.get("cached_tokens"),
                "cache_creation_input_tokens": None,
                "total_tokens": safe_sum(totals.get("prompt_tokens"), totals.get("completion_tokens")),
                "commands_executed": totals.get("commands_executed"),
                "tool_calls_traditional": totals.get("tool_calls_traditional"),
                "estimated_saved_api_calls": (usage.get("derived") or {}).get("estimated_saved_api_calls"),
                "raw": usage,
            }
        except Exception as exc:
            return {"parse_error": str(exc)}
    return {}


def safe_sum(a: Any, b: Any) -> int | None:
    if a is None or b is None:
        return None
    return int(a) + int(b)


ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def count_pi_turns(stdout: str) -> int:
    count = 0
    for line in stdout.splitlines():
        line = strip_ansi(line).strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except Exception:
            continue
        if event.get("type") == "turn_end":
            count += 1
    return count or None


def inspect_artifacts(run_dir: Path) -> dict[str, Any]:
    excluded = {"stdout.log", "stderr.log", "metrics.json", "prompt.md", "thinkflow-session.json"}
    files = []
    for path in run_dir.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(run_dir).as_posix()
        parts = set(rel.split("/"))
        if rel in excluded or rel.startswith(".git/") or "node_modules" in parts or "dist" in parts:
            continue
        try:
            size = path.stat().st_size
        except OSError:
            size = None
        files.append({"path": rel, "bytes": size})
    files.sort(key=lambda x: x["path"])
    return {
        "file_count": len(files),
        "total_bytes": sum(f["bytes"] or 0 for f in files),
        "files": files[:200],
    }


def write_summary(metrics: list[dict[str, Any]]) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# Agent Comparison Summary", ""]
    lines.append("| task | agent | exit | seconds | api_calls | input | output | total | files | bytes |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for m in metrics:
        u = m.get("usage", {})
        a = m.get("artifacts", {})
        lines.append(
            "| {task} | {agent} | {exit_code} | {duration_seconds} | {api} | {inp} | {out} | {total} | {files} | {bytes} |".format(
                task=m.get("task"),
                agent=m.get("agent"),
                exit_code=m.get("exit_code"),
                duration_seconds=m.get("duration_seconds"),
                api=u.get("api_calls"),
                inp=u.get("input_tokens"),
                out=u.get("output_tokens"),
                total=u.get("total_tokens"),
                files=a.get("file_count"),
                bytes=a.get("total_bytes"),
            )
        )
    (REPORTS / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str]) -> int:
    write_prompts()
    tasks = ["frontend_app", "novel_five_chapters"]
    agents = ["claude-code", "thinkflow"]
    if len(argv) > 1:
        tasks = [argv[1]]
    if len(argv) > 2:
        agents = [argv[2]]
    metrics = []
    for task in tasks:
        for agent in agents:
            print(f"RUN {task} / {agent}", flush=True)
            try:
                metrics.append(run_agent(task, agent))
            except subprocess.TimeoutExpired as exc:
                run_dir = RUNS / task / agent
                run_dir.mkdir(parents=True, exist_ok=True)
                metric = {
                    "task": task,
                    "agent": agent,
                    "exit_code": "timeout",
                    "duration_seconds": exc.timeout,
                    "usage": {},
                    "artifacts": inspect_artifacts(run_dir),
                    "error": "timeout",
                }
                (run_dir / "metrics.json").write_text(json.dumps(metric, ensure_ascii=False, indent=2), encoding="utf-8")
                metrics.append(metric)
            write_summary(metrics)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
