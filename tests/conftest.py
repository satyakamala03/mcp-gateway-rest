from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from gateway.core.config import load_classification, load_policy
from gateway.core.models import SessionClaims, ToolCall

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


@pytest.fixture(scope="session")
def policy():
    return load_policy(CONFIG_DIR / "policy.yaml")


@pytest.fixture(scope="session")
def classification():
    return load_classification(CONFIG_DIR / "classification.yaml")


def make_session(user: str, agent: str, intent: str) -> SessionClaims:
    now = datetime.now(timezone.utc)
    return SessionClaims(
        user=user,
        agent=agent,
        intent=intent,
        issued_at=now,
        expires_at=now + timedelta(minutes=15),
        session_id="test-session",
    )


def make_call(user: str, agent: str, intent: str, tool: str, **args) -> ToolCall:
    return ToolCall(
        agent_id=agent,
        session=make_session(user, agent, intent),
        tool=tool,
        args=args,
    )
