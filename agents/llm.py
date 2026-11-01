"""The LLM provider sits behind this one small client function, so changing
providers touches only this file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import anthropic

MODEL = "claude-sonnet-5"
MAX_TOKENS = 1024


@dataclass
class ToolUseRequest:
    id: str
    name: str
    input: dict


@dataclass
class LlmResponse:
    text: str | None
    tool_uses: list[ToolUseRequest] = field(default_factory=list)
    stop_reason: str | None = None


_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


def complete(system: str, messages: list[dict], tools: list[dict]) -> LlmResponse:
    response = _get_client().messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=system,
        messages=messages,
        tools=tools,
    )
    text_parts = [block.text for block in response.content if block.type == "text"]
    tool_uses = [
        ToolUseRequest(id=block.id, name=block.name, input=block.input)
        for block in response.content
        if block.type == "tool_use"
    ]
    return LlmResponse(
        text="\n".join(text_parts) if text_parts else None,
        tool_uses=tool_uses,
        stop_reason=response.stop_reason,
    )
