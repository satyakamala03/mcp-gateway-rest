"""Intent is a signed session claim, never an argument the agent (or a
hijacked LLM) can set. A tool call that tries to smuggle a different
intent through its arguments must still be judged against the session's
real, signed intent.
"""

from __future__ import annotations

import json

from tests.scenarios.conftest import gateway_session


async def test_spoofed_intent_argument_is_ignored(deps):
    # Session is signed for explain_code (read-only, no writes at all).
    # Even if the call smuggles an "intent" field claiming review_pr's
    # write power, the gateway never reads intent from arguments.
    async with gateway_session(deps, agent_id="review-agent", user="alice", intent="explain_code") as session:
        result = await session.call_tool(
            "post_review_comment",
            {"repo": "storefront", "number": 10, "body": "lgtm", "intent": "review_pr"},
        )
        assert result.isError is True
        payload = json.loads(result.content[0].text)
        assert payload["decision"] == "DENY"
        assert payload["rule_id"] == "R07"  # explain_code grants no writes at all
