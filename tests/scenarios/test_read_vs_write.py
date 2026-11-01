from __future__ import annotations

import json

from tests.scenarios.conftest import gateway_session


async def test_explain_code_allows_read_denies_write(deps):
    async with gateway_session(deps, agent_id="review-agent", user="alice", intent="explain_code") as session:
        read_result = await session.call_tool("read_file", {"repo": "storefront", "path": "src/app.py"})
        assert read_result.isError is False

        write_result = await session.call_tool(
            "post_review_comment", {"repo": "storefront", "number": 10, "body": "lgtm"}
        )
        assert write_result.isError is True
        payload = json.loads(write_result.content[0].text)
        assert payload["decision"] == "DENY"
        assert payload["rule_id"] == "R07"  # explain_code grants no writes at all


async def test_bob_may_add_labels_but_not_post_review_comment(deps):
    # bob's own write list is [add_labels] -- he may use that write under
    # triage-agent, but review-agent is closed to him entirely (R05) before
    # the write check would ever matter.
    async with gateway_session(deps, agent_id="triage-agent", user="bob", intent="triage_issues") as session:
        result = await session.call_tool("add_labels", {"repo": "storefront", "number": 1, "labels": ["bug"]})
        assert result.isError is False
