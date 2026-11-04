"""Hot policy reload: an admin-triggered reload picks up changed
policy/classification without restarting the gateway, and fails closed --
keeping the last-known-good policy in place -- if the new file is invalid.
"""

from __future__ import annotations

import hashlib
import json
import shutil

import httpx
import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from gateway.backends.fake import FakeBackend
from gateway.core.audit import AuditLog
from gateway.core.auth import AgentTokenStore, KillSwitch, mint_session_token
from gateway.core.config import load_classification, load_policy
from gateway.server import GatewayDeps, create_app
from tests.scenarios.conftest import ADMIN_TOKEN, AGENT_SECRETS, CONFIG_DIR, SIGNING_KEY


@pytest.fixture
def reloadable_deps(tmp_path):
    """Like the shared `deps` fixture, but config_dir is a mutable copy so
    a test can edit policy.yaml/classification.yaml and then reload.
    """
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    shutil.copy(CONFIG_DIR / "policy.yaml", config_dir / "policy.yaml")
    shutil.copy(CONFIG_DIR / "classification.yaml", config_dir / "classification.yaml")

    policy = load_policy(config_dir / "policy.yaml")
    classification = load_classification(config_dir / "classification.yaml")
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
        config_dir=config_dir,
    )


async def _call_tool(app, *, agent_id: str, user: str, intent: str, signing_key: str, tool: str, args: dict):
    session_token = mint_session_token(
        user=user, agent=agent_id, intent=intent, session_id="s1", signing_key=signing_key,
    )
    headers = {
        "Authorization": f"Bearer {AGENT_SECRETS[agent_id]}",
        "X-Session-Token": session_token,
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gateway.test", headers=headers
    ) as http_client:
        async with streamable_http_client(
            "http://gateway.test/mcp", http_client=http_client
        ) as (read_stream, write_stream, _get_session_id):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                return await session.call_tool(tool, args)


async def test_successful_reload_picks_up_a_changed_policy(reloadable_deps):
    deps = reloadable_deps
    app = create_app(deps)

    async with app.state.session_manager.run():
        # Before: bob has no payments-api access (R10).
        before = await _call_tool(
            app, agent_id="triage-agent", user="bob", intent="triage_issues", signing_key=deps.signing_key,
            tool="get_issue", args={"repo": "payments-api", "number": 1},
        )
        assert before.isError is True
        assert json.loads(before.content[0].text)["rule_id"] == "R10"

        # Widen bob's repos on disk, then trigger a reload.
        policy_path = deps.config_dir / "policy.yaml"
        policy_path.write_text(
            policy_path.read_text().replace(
                "bob: { role: support, repos: [storefront]",
                "bob: { role: support, repos: [storefront, payments-api]",
            )
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://gateway.test"
        ) as admin_client:
            reload_resp = await admin_client.post(
                "/admin/policy/reload", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"}
            )
        assert reload_resp.status_code == 200
        assert "triage-agent" in reload_resp.json()["agents"]

        # After: same call now allowed -- no restart happened.
        after = await _call_tool(
            app, agent_id="triage-agent", user="bob", intent="triage_issues", signing_key=deps.signing_key,
            tool="get_issue", args={"repo": "payments-api", "number": 1},
        )
        assert json.loads(after.content[0].text).get("decision") != "DENY"


async def test_invalid_reload_keeps_serving_the_last_known_good_policy(reloadable_deps):
    deps = reloadable_deps
    app = create_app(deps)

    async with app.state.session_manager.run():
        (deps.config_dir / "policy.yaml").write_text("not: [valid, yaml, structure\n")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://gateway.test"
        ) as admin_client:
            reload_resp = await admin_client.post(
                "/admin/policy/reload", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"}
            )
        assert reload_resp.status_code == 400

        # The gateway keeps working on the old, still-valid in-memory policy.
        result = await _call_tool(
            app, agent_id="triage-agent", user="bob", intent="triage_issues", signing_key=deps.signing_key,
            tool="get_issue", args={"repo": "storefront", "number": 1},
        )
        assert result.isError is False
