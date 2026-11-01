from __future__ import annotations

import json

from tests.scenarios.conftest import gateway_session


async def test_unknown_agent_token_denied(deps):
    async with gateway_session(
        deps, agent_id="triage-agent", user="bob", intent="triage_issues",
        agent_token="this-is-not-a-valid-token",
    ) as session:
        result = await session.call_tool("get_issue", {"repo": "storefront", "number": 1})
        assert result.isError is True
        payload = json.loads(result.content[0].text)
        assert payload["decision"] == "DENY"
        assert payload["rule_id"] == "R01"
