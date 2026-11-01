from __future__ import annotations

import json

from tests.scenarios.conftest import gateway_session


async def test_every_outcome_writes_exactly_one_row(deps):
    async with gateway_session(deps, agent_id="triage-agent", user="bob", intent="triage_issues") as session:
        await session.call_tool("get_issue", {"repo": "storefront", "number": 1})
        await session.call_tool("read_file", {"repo": "storefront", "path": "src/app.py"})  # denied

    rows = deps.audit.query(agent="triage-agent")
    assert len(rows) == 2
    allow_row = next(r for r in rows if r["tool"] == "get_issue")
    deny_row = next(r for r in rows if r["tool"] == "read_file")
    assert allow_row["outcome"] == "ALLOW"
    assert allow_row["rule_id"] == "ALLOW"
    assert deny_row["outcome"] == "DENY"
    assert deny_row["rule_id"] == "R09"


async def test_pr_diff_metadata_lookup_folds_into_one_row_not_two(deps):
    async with gateway_session(deps, agent_id="review-agent", user="alice", intent="review_pr") as session:
        result = await session.call_tool("get_pr_diff", {"repo": "storefront", "number": 10})
        assert result.isError is False

    rows = deps.audit.query(agent="review-agent")
    pr_diff_rows = [r for r in rows if r["tool"] == "get_pr_diff"]
    assert len(pr_diff_rows) == 1
    assert json.loads(pr_diff_rows[0]["paths_checked"]) == ["src/app.py", "README.md"]
