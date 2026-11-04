"""The LLM provider sits behind this one small client function, so changing
providers touches only this file. Everything outside this module speaks a
provider-neutral shape:

  - `messages` passed in are: {"role": "user", "content": str}
                            or {"role": "assistant", "text": str | None, "tool_calls": [ToolUseRequest-ish]}
                            or {"role": "tool_result", "tool_call_id": str, "content": str, "is_error": bool}
  - `tools` passed in are: {"name": str, "description": str, "input_schema": dict}
  - the return value is an LlmResponse, translated back out of whatever
    shape the provider's API actually uses.

Currently backed by OpenAI's Chat Completions API.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, cast

from openai import OpenAI

MODEL = "gpt-4o-mini"
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


_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _client


def _to_openai_messages(system: str, messages: list[dict]) -> list[dict]:
    openai_messages = [{"role": "system", "content": system}]
    for msg in messages:
        role = msg["role"]
        if role == "user":
            openai_messages.append({"role": "user", "content": msg["content"]})
        elif role == "assistant":
            entry: dict = {"role": "assistant", "content": msg.get("text")}
            tool_calls = msg.get("tool_calls") or []
            if tool_calls:
                entry["tool_calls"] = [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc["input"]),
                        },
                    }
                    for tc in tool_calls
                ]
            openai_messages.append(entry)
        elif role == "tool_result":
            openai_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": msg["tool_call_id"],
                    "content": msg["content"],
                }
            )
        else:
            raise ValueError(f"unknown message role: {role}")
    return openai_messages


def _to_openai_tools(tools: list[dict]) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description") or "",
                "parameters": tool["input_schema"],
            },
        }
        for tool in tools
    ]


def complete(system: str, messages: list[dict], tools: list[dict]) -> LlmResponse:
    # The messages/tools shapes above are kept as plain dicts on purpose --
    # this module's whole job is translating a provider-neutral shape into
    # whatever the SDK wants. The SDK itself validates them at the API
    # boundary; `cast` just tells mypy to stop checking against its exact
    # TypedDict unions here.
    response = _get_client().chat.completions.create(
        model=MODEL,
        max_completion_tokens=MAX_TOKENS,
        messages=cast(Any, _to_openai_messages(system, messages)),
        tools=cast(Any, _to_openai_tools(tools) if tools else None),
    )
    choice = response.choices[0]
    message = choice.message

    tool_uses = [
        ToolUseRequest(
            id=tc.id,
            name=tc.function.name,
            input=json.loads(tc.function.arguments) if tc.function.arguments else {},
        )
        for tc in (message.tool_calls or [])
        if tc.type == "function"  # we only ever request function-type tools
    ]

    return LlmResponse(
        text=message.content,
        tool_uses=tool_uses,
        stop_reason=choice.finish_reason,
    )
