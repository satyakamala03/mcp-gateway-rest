from __future__ import annotations

import json

from tests.scenarios.conftest import gateway_session


async def test_read_file_allowed_for_review_denied_for_triage(deps):
    args = {"repo": "storefront", "path": "src/app.py"}

    async with gateway_session(deps, agent_id="review-agent", user="alice", intent="explain_code") as session:
        result = await session.call_tool("read_file", args)
        assert result.isError is False
        payload = json.loads(result.content[0].text)
        assert "handler" in payload["result"]["content"]

    async with gateway_session(deps, agent_id="triage-agent", user="alice", intent="triage_issues") as session:
        result = await session.call_tool("read_file", args)
        assert result.isError is True
        payload = json.loads(result.content[0].text)
        assert payload["decision"] == "DENY"
        assert payload["rule_id"] == "R09"
