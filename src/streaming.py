"""
ThinkFlow Streaming — SSE 流处理

接收 API 的流式响应，分发 thinking_delta / text_delta / tool_use 事件。
"""

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import AsyncIterator, Optional

import httpx


class EventType(Enum):
    THINKING_DELTA = "thinking_delta"
    TEXT_DELTA = "text_delta"
    TOOL_USE_START = "tool_use_start"
    TOOL_USE_DELTA = "tool_use_delta"
    TOOL_USE_END = "tool_use_end"
    MESSAGE_STOP = "message_stop"
    ERROR = "error"


@dataclass
class StreamEvent:
    """流事件"""
    type: EventType
    thinking_text: str = ""       # thinking_delta 的文本
    text: str = ""                # text_delta 的文本
    tool_name: str = ""           # tool_use 的工具名
    tool_id: str = ""             # tool_use 的 ID
    tool_index: int = 0           # OpenAI/Anthropic 流里的 tool block/index
    tool_input: str = ""          # tool_use 的输入（JSON）
    error: str = ""               # 错误信息
    finish_reason: str = ""       # provider stop/finish reason
    raw: str = ""                 # 原始数据


class SSEStreamProcessor:
    """
    处理 SSE 流，解析 Anthropic API 格式的事件。

    Anthropic SSE 事件格式：
        event: content_block_delta
        data: {"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"..."}}

        event: content_block_delta
        data: {"type":"content_block_delta","index":1,"delta":{"type":"text_delta","text":"..."}}

        event: content_block_start
        data: {"type":"content_block_start","index":2,"content_block":{"type":"tool_use","id":"...","name":"read"}}

        event: message_stop
        data: {"type":"message_stop"}
    """

    async def process_stream(
        self, response: httpx.Response
    ) -> AsyncIterator[StreamEvent]:
        """处理 httpx 流式响应，产出 StreamEvent。"""
        event_type = ""

        async for line in response.aiter_lines():
            line = line.strip()

            if not line:
                # 空行 = 事件分隔
                event_type = ""
                continue

            if line.startswith("event:"):
                event_type = line[6:].strip()
                continue

            if line.startswith("data:"):
                data_str = line[5:].strip()
                if not data_str:
                    continue

                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                event = self._parse_event(event_type, data)
                if event:
                    yield event

                    if event.type == EventType.MESSAGE_STOP:
                        return

    def _parse_event(self, event_type: str, data: dict) -> Optional[StreamEvent]:
        """解析单个 SSE 事件为 StreamEvent。"""
        data_type = data.get("type", "")

        # thinking delta
        if data_type == "content_block_delta":
            delta = data.get("delta", {})
            delta_type = delta.get("type", "")

            if delta_type == "thinking_delta":
                return StreamEvent(
                    type=EventType.THINKING_DELTA,
                    thinking_text=delta.get("thinking", ""),
                    raw=json.dumps(data),
                )

            if delta_type == "text_delta":
                return StreamEvent(
                    type=EventType.TEXT_DELTA,
                    text=delta.get("text", ""),
                    raw=json.dumps(data),
                )

            if delta_type == "input_json_delta":
                return StreamEvent(
                    type=EventType.TOOL_USE_DELTA,
                    tool_input=delta.get("partial_json", ""),
                    raw=json.dumps(data),
                )

        # content block start
        if data_type == "content_block_start":
            block = data.get("content_block", {})
            block_type = block.get("type", "")

            if block_type == "tool_use":
                return StreamEvent(
                    type=EventType.TOOL_USE_START,
                    tool_name=block.get("name", ""),
                    tool_id=block.get("id", ""),
                    tool_index=data.get("index", 0),
                    raw=json.dumps(data),
                )

        # message stop
        if data_type == "message_stop":
            return StreamEvent(
                type=EventType.MESSAGE_STOP,
                finish_reason=data.get("stop_reason", "") or "stop",
                raw=json.dumps(data),
            )

        if data_type == "message_delta":
            delta = data.get("delta", {}) or {}
            stop_reason = delta.get("stop_reason") or data.get("stop_reason")
            if stop_reason:
                return StreamEvent(
                    type=EventType.MESSAGE_STOP,
                    finish_reason=stop_reason,
                    raw=json.dumps(data),
                )

        # error
        if data_type == "error":
            return StreamEvent(
                type=EventType.ERROR,
                error=data.get("error", {}).get("message", str(data)),
                raw=json.dumps(data),
            )

        return None


class OpenAISSEProcessor:
    """
    处理 OpenAI 兼容 API 的 SSE 流。

    OpenAI 格式：
        data: {"choices":[{"delta":{"reasoning_content":"..."}}]}  # thinking
        data: {"choices":[{"delta":{"content":"..."}}]}            # text
        data: {"choices":[{"delta":{"tool_calls":[...]}}]}         # tool_use
        data: [DONE]
    """

    async def process_stream(
        self, response: httpx.Response
    ) -> AsyncIterator[StreamEvent]:
        async for line in response.aiter_lines():
            line = line.strip()

            if not line:
                continue

            if line.startswith("data:"):
                data_str = line[5:].strip()

                if data_str == "[DONE]":
                    yield StreamEvent(type=EventType.MESSAGE_STOP, finish_reason="stop")
                    return

                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                choices = data.get("choices", [])
                if not choices:
                    continue

                delta = choices[0].get("delta", {})

                # thinking / reasoning
                reasoning = delta.get("reasoning_content", None)
                if reasoning is not None and reasoning != "":
                    yield StreamEvent(
                        type=EventType.THINKING_DELTA,
                        thinking_text=reasoning,
                    )

                # text
                content_val = delta.get("content", None)
                if content_val is not None and content_val != "":
                    yield StreamEvent(
                        type=EventType.TEXT_DELTA,
                        text=content_val,
                    )

                # tool calls
                if "tool_calls" in delta:
                    for tc in delta["tool_calls"]:
                        function = tc.get("function", {})
                        if function.get("name") or tc.get("id"):
                            yield StreamEvent(
                                type=EventType.TOOL_USE_START,
                                tool_name=function.get("name", ""),
                                tool_id=tc.get("id", ""),
                                tool_index=tc.get("index", 0),
                            )
                        if function.get("arguments"):
                            yield StreamEvent(
                                type=EventType.TOOL_USE_DELTA,
                                tool_index=tc.get("index", 0),
                                tool_input=function.get("arguments", ""),
                            )

                # finish reason
                finish = choices[0].get("finish_reason")
                if finish and finish != "tool_calls":
                    yield StreamEvent(type=EventType.MESSAGE_STOP, finish_reason=finish)
                    return
