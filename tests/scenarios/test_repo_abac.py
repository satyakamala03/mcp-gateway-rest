from __future__ import annotations

import json

from tests.scenarios.conftest import gateway_session


async def test_bob_denied_on_payments_api_alice_allowed(deps):
    args = {"repo": "payments-api", "number": 1}

    async with gateway_session(deps, agent_id="triage-agent", user="bob", intent="triage_issues") as session:
        result = await session.call_tool("get_issue", args)
        assert result.isError is True
        payload = json.loads(result.content[0].text)
        assert payload["rule_id"] == "R10"

    async with gateway_session(
        deps, agent_id="triage-agent", user="alice", intent="triage_issues"
    ) as session:
        result = await session.call_tool("get_issue", args)
        # payments-api has no issue #1 in FakeBackend, so this proves the
        # policy decision allowed it through to the backend (a BackendError
        # fetch_error, not a policy DENY).
        payload = json.loads(result.content[0].text)
        assert payload["decision"] != "DENY"
        assert payload.get("error") == "fetch_failed"
