"""Scenario tests run the full gateway -- real MCP server, real policy
engine, real auth -- against FakeBackend, going through the actual
streamable-HTTP MCP transport (not an in-memory shortcut), so headers
(agent token, session token) are exercised exactly as production carries
them.
"""

from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from gateway.backends.fake import FakeBackend
from gateway.core.audit import AuditLog
from gateway.core.auth import AgentTokenStore, KillSwitch, mint_session_token
from gateway.core.config import load_classification, load_policy
from gateway.server import GatewayDeps, create_app

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"
SIGNING_KEY = "scenario-test-signing-key-0123456789"
ADMIN_TOKEN = "scenario-admin-token"
AGENT_SECRETS = {"triage-agent": "triage-secret-abc", "review-agent": "review-secret-xyz"}


@pytest.fixture
def deps(tmp_path):
    policy = load_policy(CONFIG_DIR / "policy.yaml")
    classification = load_classification(CONFIG_DIR / "classification.yaml")
    audit = AuditLog(tmp_path / "audit.db")
    token_hashes = {
        agent: hashlib.sha256(secret.encode()).hexdigest()
        for agent, secret in AGENT_SECRETS.items()
    }
    token_store = AgentTokenStore(token_hashes)
    kill_switch = KillSwitch(is_enabled=audit.is_agent_enabled)
    backend = FakeBackend()
    return GatewayDeps(
        policy=policy, classification=classification, audit=audit,
        token_store=token_store, kill_switch=kill_switch,
        signing_key=SIGNING_KEY, admin_token=ADMIN_TOKEN, backend=backend,
        config_dir=CONFIG_DIR,
    )


@asynccontextmanager
async def gateway_session(
    deps: GatewayDeps,
    *,
    agent_id: str,
    user: str,
    intent: str,
    agent_token: str | None = None,
    session_token_override: str | None = None,
):
    """Yields a connected MCP ClientSession authenticated as the given
    agent/user/intent, talking to the real app over an in-process ASGI
    transport (no real network socket, but the real HTTP/MCP stack).
    """
    app = create_app(deps)
    token = agent_token if agent_token is not None else AGENT_SECRETS[agent_id]
    session_token = session_token_override or mint_session_token(
        user=user, agent=agent_id, intent=intent, session_id="scenario-session",
        signing_key=deps.signing_key,
    )
    headers = {"Authorization": f"Bearer {token}", "X-Session-Token": session_token}

    async with app.state.session_manager.run():
        http_client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://gateway.test",
            headers=headers,
        )
        async with http_client:
            async with streamable_http_client(
                "http://gateway.test/mcp", http_client=http_client
            ) as (read_stream, write_stream, _get_session_id):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    yield session
