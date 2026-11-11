"""
ThinkFlow Baseline Agent — 传统 tool_call 方式

作为对比基准。用标准 OpenAI function calling，每次 tool 调用都完整 API 往返。
和 ThinkFlow 做同样的任务，对比 token 消耗。
"""

import asyncio
import json
import os
import time
import sys
from typing import Optional

import httpx

from .usage_tracker import SessionUsage, TurnUsage


# 传统 tool 定义（OpenAI function calling 格式）
BASELINE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "write",
            "description": "写入文件（覆盖）",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径"},
                    "content": {"type": "string", "description": "文件内容"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mkdir",
            "description": "创建目录",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "目录路径"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "执行 shell 命令",
            "parameters": {
                "type": "object",
                "properties": {
                    "cmd": {"type": "string", "description": "命令"},
                },
                "required": ["cmd"],
            },
        },
    },
]


BASELINE_SYSTEM_PROMPT = """你是一个 agent。使用提供的工具（write/mkdir/bash）来完成任务。
每次只能调用一个工具，等待结果后再调用下一个。"""


class BaselineAgent:
    """传统 tool_call agent（对比基准）"""

    def __init__(self, base_url: str, api_key: str, model: str, cwd: str = "."):
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.cwd = cwd
        self.client = httpx.AsyncClient(
            base_url=base_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "content-type": "application/json",
            },
            timeout=httpx.Timeout(600.0, connect=30.0),
        )
        self.messages: list[dict] = []
        self.usage = SessionUsage(label="baseline")
        self._turn_count = 0

    async def run(self, user_input: str):
        self.usage.start_run()
        self.messages.append({"role": "user", "content": user_input})

        try:
            while await self._run_one_turn():
                pass
        finally:
            self.usage.finish_run()

    async def _run_one_turn(self) -> bool:
        self._turn_count += 1
        turn_usage = TurnUsage(turn=self._turn_count, timestamp=time.time(), http_attempts=0)
        self.usage.add_turn(turn_usage)
        try:
            return await self._run_one_turn_impl(turn_usage)
        finally:
            turn_usage.end_time = time.time()

    async def _run_one_turn_impl(self, turn_usage: TurnUsage) -> bool:
        full_messages = [{"role": "system", "content": BASELINE_SYSTEM_PROMPT}]
        full_messages.extend(self.messages)

        body = {
            "model": self.model,
            "max_tokens": 8000,
            "messages": full_messages,
            "tools": BASELINE_TOOLS,
            "stream": True,
            "stream_options": {"include_usage": True},
        }

        try:
            request = self.client.build_request("POST", "/v1/chat/completions", json=body)
            turn_usage.http_attempts += 1
            response = await self.client.send(request, stream=True)
        except Exception as e:
            print(f"\n[Baseline ERROR] API 调用失败: {e}", file=sys.stderr)
            return False

        if response.status_code != 200:
            error_text = ""
            async for chunk in response.aiter_text():
                error_text += chunk
            print(f"\n[Baseline ERROR] HTTP {response.status_code}: {error_text[:500]}",
                  file=sys.stderr)
            await response.aclose()
            return False

        turn_usage.successful_responses += 1
        content = ""
        tool_calls = []
        collected_args = {}

        try:
            async for line in response.aiter_lines():
                line = line.strip()
                if not line or not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if data_str == "[DONE]":
                    break

                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                choices = data.get("choices", [])
                finish = choices[0].get("finish_reason") if choices else None
                turn_usage.observe_usage(data.get("usage"), final=bool(finish or not choices))
                if not choices:
                    continue

                delta = choices[0].get("delta", {})

                if delta.get("content"):
                    content += delta["content"]

                if delta.get("tool_calls"):
                    for tc in delta["tool_calls"]:
                        idx = tc.get("index", 0)
                        if idx not in collected_args:
                            collected_args[idx] = {
                                "id": tc.get("id", ""),
                                "name": "",
                                "arguments": "",
                            }
                        fn = tc.get("function", {})
                        if fn.get("name"):
                            collected_args[idx]["name"] = fn["name"]
                        if fn.get("arguments"):
                            collected_args[idx]["arguments"] += fn["arguments"]

                if finish:
                    if finish == "tool_calls":
                        turn_usage.abort_reason = "tool_calls"
                    else:
                        turn_usage.abort_reason = finish
        finally:
            await response.aclose()

        # 输出内容
        if content:
            print(content, end="", flush=True)
            self.messages.append({"role": "assistant", "content": content})

        # 处理 tool_calls
        if collected_args:
            # 构建 assistant 消息
            tool_calls_msg = []
            for idx in sorted(collected_args.keys()):
                tc = collected_args[idx]
                tool_calls_msg.append({
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": tc["arguments"],
                    },
                })

            self.messages.append({
                "role": "assistant",
                "content": None,
                "tool_calls": tool_calls_msg,
            })

            # 执行每个 tool_call
            for idx in sorted(collected_args.keys()):
                tc = collected_args[idx]
                try:
                    args = json.loads(tc["arguments"])
                except json.JSONDecodeError:
                    args = {}

                result = self._execute_tool(tc["name"], args)
                turn_usage.commands_executed += 1

                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result,
                })

            print(f"\n  [Baseline] 执行了 {len(collected_args)} 个 tool_call",
                  file=sys.stderr)
            return True  # 继续

        print()  # 换行
        return False

    def _execute_tool(self, name: str, args: dict) -> str:
        """执行传统 tool。"""
        if name == "write":
            path = os.path.expanduser(args.get("path", ""))
            content = args.get("content", "")
            dir_path = os.path.dirname(path)
            if dir_path:
                os.makedirs(dir_path, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return f"写入成功: {path} ({len(content.encode('utf-8'))} bytes)"

        elif name == "mkdir":
            path = os.path.expanduser(args.get("path", ""))
            os.makedirs(path, exist_ok=True)
            return f"目录创建成功: {path}"

        elif name == "bash":
            import subprocess
            cmd = args.get("cmd", "")
            proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=self.cwd)
            return f"exit={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"

        return f"未知工具: {name}"

    async def close(self):
        await self.client.aclose()
