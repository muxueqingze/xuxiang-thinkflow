"""Real API smoke test for ThinkFlow.

Reads credentials from environment variables or an ignored config file.
This script never prints the API key.
"""

import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.agent_loop import AgentLoop
from src.cli import apply_env_defaults, create_agent, load_config, resolve_system_prompt
from src.model_registry import merge_active_provider, resolve_auto_model


SMOKE_TEXT = "THINKFLOW_SMOKE_OK"


def load_effective_config(config_path: str | None) -> dict:
    config = {}
    candidates = []
    if config_path:
        candidates.append(config_path)
    candidates.append("config.json")
    for path in candidates:
        if path and os.path.exists(path):
            config = load_config(path)
            break
    return resolve_auto_model(merge_active_provider(apply_env_defaults(config)))


async def run_smoke(agent: AgentLoop, workdir: Path):
    prompt = (
        "Create a file named smoke.txt in the current working directory. "
        f"Its entire content must be exactly: {SMOKE_TEXT}\n"
        "Use the available write tool if tools are available. Do not use web_search, fetch_url, list_skills, or read_skill. "
        "After the file is created, reply with one short sentence."
    )
    await agent.run(prompt)
    if agent.last_error:
        raise RuntimeError(agent.last_error)
    output = workdir / "smoke.txt"
    if not output.exists():
        raise RuntimeError("smoke.txt was not created")
    actual = output.read_text(encoding="utf-8").strip()
    if actual != SMOKE_TEXT:
        raise RuntimeError(f"smoke.txt content mismatch: {actual!r}")


def main():
    parser = argparse.ArgumentParser(description="Run a real ThinkFlow API smoke test.")
    parser.add_argument("--config", default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--api-path", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--provider", default=None, choices=["openai", "anthropic"])
    parser.add_argument("--thinking-budget", type=int, default=0)
    parser.add_argument("--max-tokens", type=int, default=2000)
    parser.add_argument("--stream-usage", action="store_true")
    parser.add_argument("--no-native-tools", action="store_true")
    parser.add_argument("--use-built-in-system-prompt", dest="use_builtin_system_prompt", action="store_true")
    args = parser.parse_args()

    config = load_effective_config(args.config)
    if args.base_url:
        config["base_url"] = args.base_url
    if args.api_path:
        config["api_path"] = args.api_path
    if args.model:
        config["model"] = args.model
    if args.provider:
        config["provider"] = args.provider
    config["thinking_budget"] = args.thinking_budget
    config["max_tokens"] = args.max_tokens
    if args.stream_usage:
        config["stream_options_include_usage"] = True
    if args.no_native_tools:
        config["enable_native_tools"] = False
    else:
        config["native_tools"] = ["pwd", "read", "write"]
    config["verbose"] = False
    interfaces = dict(config.get("interfaces", {}) or {})
    web = dict(interfaces.get("web", {}) or {})
    web["enabled"] = False
    interfaces["web"] = web
    skills = dict(interfaces.get("skills", {}) or {})
    skills["enabled"] = False
    interfaces["skills"] = skills
    config["interfaces"] = interfaces

    if not config.get("api_key"):
        print("SKIP: missing THINKFLOW_API_KEY or config api_key", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        system_prompt = resolve_system_prompt(
            config,
            use_builtin=args.use_builtin_system_prompt,
        )
        agent = create_agent(config, system_prompt, cwd=str(workdir))

        async def runner():
            try:
                await run_smoke(agent, workdir)
            finally:
                await agent.close()

        try:
            asyncio.run(runner())
        except Exception as e:
            print(json.dumps({
                "ok": False,
                "error": str(e),
                "model": config.get("model"),
                "provider": config.get("provider", "openai"),
                "base_url": config.get("base_url"),
            }, ensure_ascii=False), file=sys.stderr)
            return 1

        print(json.dumps({
            "ok": True,
            "model": config.get("model"),
            "provider": config.get("provider", "openai"),
            "base_url": config.get("base_url"),
        }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
