"""Estimate tokens from the compact UTF-8 representation of provider input."""

from __future__ import annotations

import json
from typing import Any


def estimate_request_tokens(body: dict[str, Any]) -> int:
    """Estimate tokens for the input fields in a request, excluding generation options."""
    inputs = {key: body[key] for key in ("messages", "system", "tools") if key in body}
    return _estimate_json_tokens(inputs)


def estimate_message_tokens(message: dict[str, Any]) -> int:
    """Estimate one retained message using the same bytes-per-token heuristic."""
    return _estimate_json_tokens(message)


def _estimate_json_tokens(value: Any) -> int:
    size = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    return (size + 3) // 4
