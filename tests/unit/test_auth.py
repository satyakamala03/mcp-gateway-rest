from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
import pytest

from gateway.core.auth import (
    AgentTokenStore,
    AuthError,
    KillSwitch,
    mint_session_token,
    verify_session,
)

SIGNING_KEY = "test-signing-key"


def test_r01_unknown_agent_token_rejected():
    import hashlib

    store = AgentTokenStore({"triage-agent": hashlib.sha256(b"secret-123").hexdigest()})
    with pytest.raises(AuthError) as exc:
        store.authenticate("triage-agent", "wrong-token")
    assert exc.value.rule_id == "R01"


def test_r01_known_agent_token_accepted():
    import hashlib

    store = AgentTokenStore({"triage-agent": hashlib.sha256(b"secret-123").hexdigest()})
    store.authenticate("triage-agent", "secret-123")  # no raise


def test_r01_unregistered_agent_rejected():
    store = AgentTokenStore({})
    with pytest.raises(AuthError) as exc:
        store.authenticate("ghost-agent", "anything")
    assert exc.value.rule_id == "R01"


def test_r02_kill_switch_blocks_disabled_agent():
    switch = KillSwitch(is_enabled=lambda agent_id: agent_id != "triage-agent")
    with pytest.raises(AuthError) as exc:
        switch.check("triage-agent")
    assert exc.value.rule_id == "R02"
    switch.check("review-agent")  # unaffected, no raise


def test_r03_session_round_trip():
    token = mint_session_token(
        user="alice", agent="review-agent", intent="explain_code",
        session_id="s1", signing_key=SIGNING_KEY,
    )
    claims = verify_session(token, signing_key=SIGNING_KEY, calling_agent_id="review-agent")
    assert claims.user == "alice"
    assert claims.intent == "explain_code"


def test_r03_expired_session_rejected():
    now = datetime.now(timezone.utc) - timedelta(minutes=30)
    token = jwt.encode(
        {
            "user": "alice", "agent": "review-agent", "intent": "explain_code",
            "session_id": "s1",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=15)).timestamp()),  # expired 15 min ago
        },
        SIGNING_KEY, algorithm="HS256",
    )
    with pytest.raises(AuthError) as exc:
        verify_session(token, signing_key=SIGNING_KEY, calling_agent_id="review-agent")
    assert exc.value.rule_id == "R03"


def test_r03_wrong_signing_key_rejected():
    token = mint_session_token(
        user="alice", agent="review-agent", intent="explain_code",
        session_id="s1", signing_key=SIGNING_KEY,
    )
    with pytest.raises(AuthError) as exc:
        verify_session(token, signing_key="a-different-key", calling_agent_id="review-agent")
    assert exc.value.rule_id == "R03"


def test_r03_session_rejected_for_different_agent():
    # a session token minted for review-agent must not be usable by triage-agent
    token = mint_session_token(
        user="alice", agent="review-agent", intent="explain_code",
        session_id="s1", signing_key=SIGNING_KEY,
    )
    with pytest.raises(AuthError) as exc:
        verify_session(token, signing_key=SIGNING_KEY, calling_agent_id="triage-agent")
    assert exc.value.rule_id == "R03"
