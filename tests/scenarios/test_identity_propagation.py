from __future__ import annotations

import json

from tests.scenarios.conftest import gateway_session


async def test_same_agent_same_intent_different_user_different_outcome(deps):
    args = {"repo": "payments-api", "number": 1}

    async with gateway_session(
        deps, agent_id="triage-agent", user="alice", intent="triage_issues"
    ) as session:
        alice_result = await session.call_tool("get_issue", args)
    async with gateway_session(deps, agent_id="triage-agent", user="bob", intent="triage_issues") as session:
        bob_result = await session.call_tool("get_issue", args)

    alice_payload = json.loads(alice_result.content[0].text)
    bob_payload = json.loads(bob_result.content[0].text)

    assert alice_payload.get("decision") != "DENY"  # alice has payments-api
    assert bob_payload["decision"] == "DENY"
    assert bob_payload["rule_id"] == "R10"  # bob is storefront-only
