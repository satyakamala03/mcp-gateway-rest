from __future__ import annotations

import json

from tests.scenarios.conftest import gateway_session


async def test_triage_agent_cannot_read_files(deps):
    # triage_issues grants issue/issue_comment/label/repo_meta -- never
    # code/docs, so read_file is outside the declared intent entirely.
    async with gateway_session(deps, agent_id="triage-agent", user="bob", intent="triage_issues") as session:
        result = await session.call_tool("read_file", {"repo": "storefront", "path": "src/app.py"})
        assert result.isError is True
        payload = json.loads(result.content[0].text)
        assert payload["decision"] == "DENY"
        assert payload["rule_id"] == "R09"
