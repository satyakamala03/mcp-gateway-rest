from __future__ import annotations

import json

from tests.scenarios.conftest import gateway_session


async def test_disabling_one_agent_denies_it_and_leaves_the_other_unaffected(deps):
    deps.audit.set_agent_enabled("triage-agent", False)

    async with gateway_session(deps, agent_id="triage-agent", user="bob", intent="triage_issues") as session:
        result = await session.call_tool("get_issue", {"repo": "storefront", "number": 1})
        assert result.isError is True
        payload = json.loads(result.content[0].text)
        assert payload["decision"] == "DENY"
        assert payload["rule_id"] == "R02"

    async with gateway_session(deps, agent_id="review-agent", user="alice", intent="explain_code") as session:
        result = await session.call_tool("read_file", {"repo": "storefront", "path": "src/app.py"})
        assert result.isError is False  # review-agent is unaffected

    deps.audit.set_agent_enabled("triage-agent", True)
    async with gateway_session(deps, agent_id="triage-agent", user="bob", intent="triage_issues") as session:
        result = await session.call_tool("get_issue", {"repo": "storefront", "number": 1})
        assert result.isError is False  # takes effect immediately on the next call
