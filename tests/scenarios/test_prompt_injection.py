"""The prompt-injection demo: storefront issue #2 (see FakeBackend) asks
the reader to fetch .env and paste its contents. The LLM's reaction to
that text is shown live in the Streamlit demo; the guarantee proven here
is that the gateway denies the exact call a hijacked triage-agent would
make, independent of anything the model decided to try.
"""

from __future__ import annotations

import json

from tests.scenarios.conftest import gateway_session


async def test_hijacked_triage_agent_still_denied_reading_env(deps):
    async with gateway_session(deps, agent_id="triage-agent", user="bob", intent="triage_issues") as session:
        injected_issue = await session.call_tool("get_issue", {"repo": "storefront", "number": 2})
        assert injected_issue.isError is False
        body = json.loads(injected_issue.content[0].text)["result"]["body"]
        assert "read_file" in body  # confirms the fixture actually contains the injection

        hijacked_call = await session.call_tool("read_file", {"repo": "storefront", "path": ".env"})
        assert hijacked_call.isError is True
        payload = json.loads(hijacked_call.content[0].text)
        assert payload["decision"] == "DENY"
        assert payload["rule_id"] == "R09"
