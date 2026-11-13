"""Measure local idle Steering overhead through AgentLoop.run and mock OpenAI SSE."""

import argparse
import asyncio
import json
import math
from pathlib import Path
import platform
from statistics import median
import sys
from tempfile import TemporaryDirectory
from time import perf_counter_ns

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.agent_loop import AgentConfig, AgentLoop
from src.prompts import BUILTIN_SYSTEM_PROMPT
from src.provider import ProviderConfig


PROMPT = "Summarize the current change in one sentence."
REPLY = "The change preserves the existing interface."
SSE = (
    "data: " + json.dumps({"choices": [{"delta": {"content": REPLY}, "finish_reason": None}]})
    + "\n\ndata: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]})
    + "\n\ndata: [DONE]\n\n"
).encode()


class FixtureStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield SSE


def latency_us(samples):
    ordered = sorted(samples)
    return {
        "median": round(median(ordered) / 1_000, 3),
        "p95": round(ordered[math.ceil(0.95 * len(ordered)) - 1] / 1_000, 3),
    }


async def measure(workspace, pairs, warmup):
    requests = []
    callback_calls = 0

    async def respond(request):
        requests.append(request.content)
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=FixtureStream())

    async def empty_queue():
        nonlocal callback_calls
        callback_calls += 1
        return []

    base_url = "https://steering-fixture.invalid/v1"
    config = AgentConfig(
        cwd=str(workspace),
        system_prompt=BUILTIN_SYSTEM_PROMPT,
        provider=ProviderConfig(base_url=base_url, model="fixture", enable_native_tools=False),
        max_retries=0,
    )
    agent = AgentLoop(config, event_sink=lambda _: None)
    await agent.client.aclose()
    agent.client = httpx.AsyncClient(base_url=base_url, transport=httpx.MockTransport(respond))
    snapshot = agent.to_snapshot()
    disabled, enabled, paired_delta = [], [], []
    request_body = None
    try:
        for pair in range(pairs + warmup):
            elapsed = {}
            for use_source in ((False, True) if pair % 2 == 0 else (True, False)):
                agent.steering_source = empty_queue if use_source else None
                agent.load_snapshot(snapshot)
                requests.clear()
                previous_calls = callback_calls

                started = perf_counter_ns()
                await agent.run(PROMPT)
                elapsed[use_source] = perf_counter_ns() - started

                if len(requests) != 1 or agent.usage.totals()["api_calls"] != 1:
                    raise RuntimeError(f"Expected one request, got {len(requests)}")
                if callback_calls - previous_calls != int(use_source):
                    raise RuntimeError("Expected one empty queue check per enabled response")
                if agent.stopped_reason != "completed" or agent.last_error:
                    raise RuntimeError(f"Fixture run failed: {agent.stopped_reason} {agent.last_error}")
                if agent.messages[-1]["content"] != REPLY:
                    raise RuntimeError("Agent did not consume the fixture SSE response")
                if request_body is None:
                    request_body = requests[0]
                if requests[0] != request_body:
                    raise RuntimeError(f"Serialized request body changed in pair {pair}")

            if pair >= warmup:
                disabled.append(elapsed[False])
                enabled.append(elapsed[True])
                paired_delta.append(elapsed[True] - elapsed[False])
    finally:
        await agent.close()

    return {
        "scope": "Local scheduling comparison on one ThinkFlow revision. AgentLoop.run uses "
                 "OpenAI SSE through HTTPX MockTransport. No remote model, provider latency, or Pi comparison.",
        "python": platform.python_version(),
        "platform": platform.platform(),
        "httpx": httpx.__version__,
        "measurement": "Alternating pair order after warmup. Reuses one agent and client. "
                       "Restores the same empty snapshot before each run, outside the timed interval.",
        "warmup_pairs": warmup,
        "measured_pairs": pairs,
        "http_requests_per_run": {"source_none": 1, "empty_source": 1},
        "queue_checks_per_run": {"source_none": 0, "empty_source": 1},
        "request_body_byte_equal_across_all_runs": True,
        "request_body_bytes": len(request_body),
        "latency_us": {
            "source_none": latency_us(disabled),
            "empty_source": latency_us(enabled),
            "paired_delta_empty_minus_none": latency_us(paired_delta),
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs", type=int, default=200)
    parser.add_argument("--warmup", type=int, default=20)
    parser.add_argument("--output", type=Path, default=ROOT / "docs/evidence/steering-performance-20261113.json")
    args = parser.parse_args()
    if args.pairs < 1 or args.warmup < 0:
        parser.error("--pairs must be positive and --warmup must be nonnegative")

    temp = ROOT / "temp"
    temp.mkdir(exist_ok=True)
    with TemporaryDirectory(prefix="steering-perf-20261113-", dir=temp) as workspace:
        result = asyncio.run(measure(Path(workspace), args.pairs, args.warmup))
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
