"""The shared tool-calling loop. Each agent (Triage, Review) is just a
configuration of this runner: its own agent token, system prompt and
display name. Agents hold no GitHub credentials and no policy logic --
they only know the gateway's URL and their own agent token.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

import httpx
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from agents.llm import complete

MAX_TOOL_CALLS_PER_TURN = 6


@dataclass
class ToolCallRecord:
    """One row for the Streamlit page's inline ALLOW/DENY badge."""

    tool: str
    args: dict
    is_error: bool
    decision: str | None
    rule_id: str | None
    message: str | None


@dataclass
class TurnResult:
    reply: str
    tool_calls: list[ToolCallRecord] = field(default_factory=list)


class GatewayAgent:
    def __init__(self, *, agent_id: str, agent_token: str, gateway_url: str, system_prompt: str):
        self.agent_id = agent_id
        self.agent_token = agent_token
        self.gateway_url = gateway_url.rstrip("/")
        self.system_prompt = system_prompt

    def send(self, user_text: str, session_token: str, history: list[dict]) -> TurnResult:
        """Synchronous entry point for Streamlit's script-rerun model. Opens
        a fresh MCP connection for this turn -- simple and stateless, at the
        cost of a reconnect per turn, which is fine at demo scale.
        """
        return asyncio.run(self._send_async(user_text, session_token, history))

    async def _send_async(self, user_text: str, session_token: str, history: list[dict]) -> TurnResult:
        headers = {
            "Authorization": f"Bearer {self.agent_token}",
            "X-Session-Token": session_token,
        }
        async with httpx.AsyncClient(
            base_url=self.gateway_url, headers=headers, timeout=30.0
        ) as http_client:
            async with streamable_http_client(
                f"{self.gateway_url}/mcp", http_client=http_client
            ) as (read_stream, write_stream, _get_session_id):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    return await self._run_turn(session, user_text, history)

    async def _run_turn(self, session: ClientSession, user_text: str, history: list[dict]) -> TurnResult:
        tools_result = await session.list_tools()
        anthropic_tools = [
            {"name": t.name, "description": t.description or "", "input_schema": t.inputSchema}
            for t in tools_result.tools
        ]

        messages = [*history, {"role": "user", "content": user_text}]
        tool_calls: list[ToolCallRecord] = []

        for _ in range(MAX_TOOL_CALLS_PER_TURN):
            response = complete(self.system_prompt, messages, anthropic_tools)

            if not response.tool_uses:
                return TurnResult(reply=response.text or "", tool_calls=tool_calls)

            assistant_content = []
            if response.text:
                assistant_content.append({"type": "text", "text": response.text})
            for tu in response.tool_uses:
                assistant_content.append(
                    {"type": "tool_use", "id": tu.id, "name": tu.name, "input": tu.input}
                )
            messages.append({"role": "assistant", "content": assistant_content})

            tool_result_blocks = []
            for tu in response.tool_uses:
                result = await session.call_tool(tu.name, tu.input)
                text = result.content[0].text if result.content else "{}"
                try:
                    parsed = json.loads(text)
                except (ValueError, IndexError):
                    parsed = {}

                tool_calls.append(
                    ToolCallRecord(
                        tool=tu.name,
                        args=tu.input,
                        is_error=result.isError,
                        decision=parsed.get("decision"),
                        rule_id=parsed.get("rule_id"),
                        message=parsed.get("reason") or parsed.get("message"),
                    )
                )
                tool_result_blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tu.id,
                        "content": text,
                        "is_error": result.isError,
                    }
                )
            messages.append({"role": "user", "content": tool_result_blocks})

        return TurnResult(
            reply="(stopped after reaching the per-turn tool-call limit)",
            tool_calls=tool_calls,
        )
