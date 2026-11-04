"""R01-R03: agent authentication, kill switch, session token verification.

Runs before policy.decide() -- these three checks don't need a PolicyConfig
at all, just the agent token store, the kill-switch state and the session
JWT's signature/expiry.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import jwt
from pydantic import ValidationError

from gateway.core.models import Decision, SessionClaims

SESSION_TTL = timedelta(minutes=15)


class AuthError(Exception):
    """Raised for any auth failure; callers turn this into a DENY Decision."""

    def __init__(self, rule_id: str, reason: str):
        self.rule_id = rule_id
        self.reason = reason
        super().__init__(reason)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class AgentTokenStore:
    """Agent tokens are configured per agent at deploy time; the gateway
    stores only their hash, never the raw secret.
    """

    def __init__(self, token_hashes: dict[str, str]):
        self._token_hashes = token_hashes  # agent_id -> sha256 hex digest

    @classmethod
    def from_env(cls, agent_ids: list[str]) -> AgentTokenStore:
        import os

        hashes = {}
        for agent_id in agent_ids:
            env_var = f"AGENT_TOKEN_{agent_id.upper().replace('-', '_')}"
            raw = os.environ.get(env_var)
            if raw:
                hashes[agent_id] = _hash_token(raw)
        return cls(hashes)

    def authenticate(self, agent_id: str, presented_token: str) -> None:
        """R01: the agent token is valid, or raise unknown_agent."""
        expected = self._token_hashes.get(agent_id)
        if expected is None or not hmac.compare_digest(
            expected, _hash_token(presented_token)
        ):
            raise AuthError("R01", f"agent '{agent_id}' token is invalid")

    def identify(self, presented_token: str) -> str | None:
        """Reverse lookup: which agent (if any) a bearer token belongs to.
        The gateway never receives the agent_id directly -- only the raw
        token in the Authorization header -- so it must find the match.
        """
        presented_hash = _hash_token(presented_token)
        for agent_id, expected_hash in self._token_hashes.items():
            if hmac.compare_digest(expected_hash, presented_hash):
                return agent_id
        return None


class KillSwitch:
    """Backed by gateway/core/audit.py's agent_state table. Checked on
    every call; takes effect immediately; other agents unaffected.
    """

    def __init__(self, is_enabled: Callable[[str], bool]):
        self._is_enabled = is_enabled

    def check(self, agent_id: str) -> None:
        """R02: the agent is not disabled, or raise agent_disabled."""
        if not self._is_enabled(agent_id):
            raise AuthError("R02", f"agent '{agent_id}' is disabled")


def mint_session_token(
    *,
    user: str,
    agent: str,
    intent: str,
    session_id: str,
    signing_key: str,
    now: datetime | None = None,
) -> str:
    """Called by the Streamlit app, never by agent code or the LLM."""
    now = now or datetime.now(UTC)
    return jwt.encode(
        {
            "user": user,
            "agent": agent,
            "intent": intent,
            "session_id": session_id,
            "iat": int(now.timestamp()),
            "exp": int((now + SESSION_TTL).timestamp()),
        },
        signing_key,
        algorithm="HS256",
    )


def verify_session(
    token: str, *, signing_key: str, calling_agent_id: str
) -> SessionClaims:
    """R03: the session token is signed by the Streamlit app and not
    expired, or raise invalid_session. Also confirms the session's agent
    claim matches the agent presenting it -- a session token alone cannot
    be used by a different agent than the one it was minted for.
    """
    try:
        payload = jwt.decode(token, signing_key, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise AuthError("R03", f"session token invalid or expired: {exc}") from exc

    if payload.get("agent") != calling_agent_id:
        raise AuthError(
            "R03", "session token was not issued for this agent"
        )

    try:
        return SessionClaims(
            user=payload["user"],
            agent=payload["agent"],
            intent=payload["intent"],
            session_id=payload["session_id"],
            issued_at=datetime.fromtimestamp(payload["iat"], tz=UTC),
            expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
        )
    except (KeyError, ValidationError) as exc:
        raise AuthError("R03", f"session token missing required claims: {exc}") from exc


def auth_error_to_decision(exc: AuthError) -> Decision:
    return Decision(outcome="DENY", rule_id=exc.rule_id, reason=exc.reason)
