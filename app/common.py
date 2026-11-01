"""Shared helpers for the Streamlit pages: identity/intent pickers backed
by the real policy.yaml, session token minting, and agent construction.
Agents and the gateway never see raw Streamlit state -- only the signed
session token and the agent's own token travel over the wire.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

from agents.runner import GatewayAgent
from gateway.core.auth import SESSION_TTL, mint_session_token
from gateway.core.config import load_policy

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
PROMPTS_DIR = ROOT / "agents" / "prompts"

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:8080")
SESSION_SIGNING_KEY = os.environ["SESSION_SIGNING_KEY"]

AGENT_TOKEN_ENV = {
    "triage-agent": "AGENT_TOKEN_TRIAGE_AGENT",
    "review-agent": "AGENT_TOKEN_REVIEW_AGENT",
}


def load_demo_policy():
    return load_policy(CONFIG_DIR / "policy.yaml")


def users_allowed_for(agent_id: str) -> list[str]:
    policy = load_demo_policy()
    agent = policy.agents[agent_id]
    return [
        user_id
        for user_id, user in policy.users.items()
        if user.role in agent.allowed_roles
    ]


def intents_for(agent_id: str) -> list[str]:
    policy = load_demo_policy()
    return list(policy.agents[agent_id].intents)


def read_prompt(agent_id: str) -> str:
    name = "triage_agent.md" if agent_id == "triage-agent" else "review_agent.md"
    return (PROMPTS_DIR / name).read_text()


def agent_token(agent_id: str) -> str:
    return os.environ[AGENT_TOKEN_ENV[agent_id]]


def mint_session(user: str, agent_id: str, intent: str) -> str:
    return mint_session_token(
        user=user, agent=agent_id, intent=intent,
        session_id=str(uuid.uuid4()), signing_key=SESSION_SIGNING_KEY,
    )


def render_agent_page(agent_id: str, display_name: str) -> None:
    """Shared chat page for Triage and Review: identity/intent picker,
    hard 15-minute session expiry with no silent renewal, and inline
    ALLOW/DENY badges on every tool call.
    """
    st.set_page_config(page_title=display_name, page_icon="🔒")
    st.title(display_name)

    key = agent_id.replace("-", "_")
    session_key = f"{key}_session"

    def _start_session(user: str, intent: str) -> None:
        st.session_state[session_key] = {
            "user": user,
            "intent": intent,
            "token": mint_session(user, agent_id, intent),
            "minted_at": datetime.now(timezone.utc),
            "history": [],
            "turns": [],
        }

    state = st.session_state.get(session_key)
    expired = state is not None and (
        datetime.now(timezone.utc) - state["minted_at"] > SESSION_TTL
    )
    if expired:
        st.warning("Session expired after 15 minutes. Pick your identity again to continue.")
        state = None

    if state is None:
        st.subheader("Start a session")
        user = st.selectbox("Who are you?", users_allowed_for(agent_id), key=f"{key}_user")
        intent = st.selectbox("What have you authorized this session to do?", intents_for(agent_id), key=f"{key}_intent")
        if st.button("Start session", key=f"{key}_start"):
            _start_session(user, intent)
            st.rerun()
        return

    st.caption(f"Signed in as **{state['user']}**, intent **{state['intent']}** -- expires 15 min after session start.")
    if st.button("End session", key=f"{key}_end"):
        del st.session_state[session_key]
        st.rerun()

    for turn in state["turns"]:
        with st.chat_message(turn["role"]):
            st.write(turn["text"])
            for call in turn.get("tool_calls", []):
                badge = "🟢 ALLOW" if not call["is_error"] else "🔴 DENY" if call["decision"] == "DENY" else "🟡 ERROR"
                label = f"{badge} · `{call['tool']}`"
                if call.get("rule_id"):
                    label += f" · rule `{call['rule_id']}`"
                with st.expander(label):
                    st.json({"args": call["args"], "message": call.get("message")})

    user_text = st.chat_input("Message the assistant...")
    if not user_text:
        return

    agent = GatewayAgent(
        agent_id=agent_id, agent_token=agent_token(agent_id),
        gateway_url=GATEWAY_URL, system_prompt=read_prompt(agent_id),
    )
    result = agent.send(user_text, state["token"], state["history"])

    state["history"].append({"role": "user", "content": user_text})
    state["history"].append({"role": "assistant", "content": result.reply})
    state["turns"].append({"role": "user", "text": user_text})
    state["turns"].append(
        {
            "role": "assistant",
            "text": result.reply,
            "tool_calls": [
                {
                    "tool": c.tool, "args": c.args, "is_error": c.is_error,
                    "decision": c.decision, "rule_id": c.rule_id, "message": c.message,
                }
                for c in result.tool_calls
            ],
        }
    )
    st.rerun()
