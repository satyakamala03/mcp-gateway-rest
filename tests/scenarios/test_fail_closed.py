"""Fail-closed through the full stack: an expired session and an internal
decision-path error both DENY, never ALLOW. (Invalid policy file and a
post-ALLOW backend exception -- which intentionally stays ALLOW with a
fetch_error, not a DENY -- are covered in tests/unit/test_fail_closed.py
and test_repo_abac.py respectively.)
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import jwt

from tests.scenarios.conftest import SIGNING_KEY, gateway_session


async def test_expired_session_denied(deps):
    expired = datetime.now(timezone.utc) - timedelta(minutes=30)
    stale_token = jwt.encode(
        {
            "user": "alice", "agent": "review-agent", "intent": "explain_code",
            "session_id": "stale",
            "iat": int(expired.timestamp()),
            "exp": int((expired + timedelta(minutes=15)).timestamp()),
        },
        SIGNING_KEY, algorithm="HS256",
    )
    async with gateway_session(
        deps, agent_id="review-agent", user="alice", intent="explain_code",
        session_token_override=stale_token,
    ) as session:
        result = await session.call_tool("read_file", {"repo": "storefront", "path": "src/app.py"})
        assert result.isError is True
        payload = json.loads(result.content[0].text)
        assert payload["decision"] == "DENY"
        assert payload["rule_id"] == "R03"


async def test_get_pr_diff_metadata_failure_fails_closed(deps):
    # A PR number that doesn't exist raises BackendError out of
    # list_pr_files -- before the decision is finalized, so this must
    # DENY fail_closed, not fall through to ALLOW.
    async with gateway_session(deps, agent_id="review-agent", user="alice", intent="review_pr") as session:
        result = await session.call_tool("get_pr_diff", {"repo": "storefront", "number": 9999})
        assert result.isError is True
        payload = json.loads(result.content[0].text)
        assert payload["decision"] == "DENY"
        assert payload["rule_id"] == "fail_closed"
