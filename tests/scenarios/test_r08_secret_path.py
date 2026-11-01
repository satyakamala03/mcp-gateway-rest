from __future__ import annotations

import json

from tests.scenarios.conftest import gateway_session


async def test_env_file_denied_even_for_review_agent(deps):
    # review-agent's explain_code intent grants "code" and "docs", but
    # never "secret" -- denied at R09 since no intent ever grants secret.
    async with gateway_session(deps, agent_id="review-agent", user="alice", intent="explain_code") as session:
        result = await session.call_tool("read_file", {"repo": "storefront", "path": ".env"})
        assert result.isError is True
        payload = json.loads(result.content[0].text)
        assert payload["decision"] == "DENY"
        assert payload["rule_id"] == "R09"


async def test_pr_diff_touching_env_denied_at_r08(deps):
    # payments-api PR 11 touches .env (see FakeBackend fixtures) -- this is
    # the real-world trigger for R08: pr_diff IS granted by review_pr, but
    # the riskiest changed path escalates the tier to T3.
    async with gateway_session(deps, agent_id="review-agent", user="alice", intent="review_pr") as session:
        result = await session.call_tool("get_pr_diff", {"repo": "payments-api", "number": 11})
        assert result.isError is True
        payload = json.loads(result.content[0].text)
        assert payload["decision"] == "DENY"
        assert payload["rule_id"] == "R08"
