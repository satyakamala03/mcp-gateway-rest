"""Runs the real gateway app with FakeBackend instead of RestBackend, for
local smoke-testing without a live GitHub token. Not part of the shipped
demo (which uses RestBackend via gateway/server.py:main) -- this is a
throwaway script for manual verification only.
"""

import hashlib
import os
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

from gateway.backends.fake import FakeBackend
from gateway.core.audit import AuditLog
from gateway.core.auth import AgentTokenStore, KillSwitch
from gateway.core.config import load_classification, load_policy
from gateway.server import GatewayDeps, create_app

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

CONFIG_DIR = Path("config")

policy = load_policy(CONFIG_DIR / "policy.yaml")
classification = load_classification(CONFIG_DIR / "classification.yaml")
audit = AuditLog("data/demo_audit.db")
token_hashes = {
    "triage-agent": hashlib.sha256(os.environ["AGENT_TOKEN_TRIAGE_AGENT"].encode()).hexdigest(),
    "review-agent": hashlib.sha256(os.environ["AGENT_TOKEN_REVIEW_AGENT"].encode()).hexdigest(),
}
token_store = AgentTokenStore(token_hashes)
kill_switch = KillSwitch(is_enabled=audit.is_agent_enabled)

deps = GatewayDeps(
    policy=policy, classification=classification, audit=audit,
    token_store=token_store, kill_switch=kill_switch,
    signing_key=os.environ["SESSION_SIGNING_KEY"], admin_token=os.environ["ADMIN_TOKEN"],
    backend=FakeBackend(), config_dir=CONFIG_DIR,
)

app = create_app(deps)

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8080)
