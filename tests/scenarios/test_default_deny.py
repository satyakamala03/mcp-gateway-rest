from __future__ import annotations

import json

from tests.scenarios.conftest import gateway_session


async def test_unmapped_tool_denied_even_if_forged(deps):
    # merge_pr doesn't exist in the canonical catalog -- dangerous
    # operations simply aren't registered, so this denies at R06
    # regardless of how the call is constructed.
    async with gateway_session(deps, agent_id="review-agent", user="alice", intent="review_pr") as session:
        result = await session.call_tool("merge_pr", {"repo": "storefront", "number": 10})
        assert result.isError is True
        payload = json.loads(result.content[0].text)
        assert payload["decision"] == "DENY"
        assert payload["rule_id"] == "R06"
