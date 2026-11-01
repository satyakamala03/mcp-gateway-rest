from __future__ import annotations

import json

from tests.scenarios.conftest import gateway_session


async def test_happy_path_triage_agent_adds_label(deps):
    async with gateway_session(deps, agent_id="triage-agent", user="bob", intent="triage_issues") as session:
        result = await session.call_tool("add_labels", {"repo": "storefront", "number": 1, "labels": ["bug"]})
        assert result.isError is False
        payload = json.loads(result.content[0].text)
        assert payload["result"]["status"] == "ok"
