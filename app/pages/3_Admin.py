import os

import httpx
import streamlit as st

from app.common import GATEWAY_URL

st.set_page_config(page_title="Admin", page_icon="🔒")
st.title("Admin")

ADMIN_TOKEN = os.environ["ADMIN_TOKEN"]
HEADERS = {"Authorization": f"Bearer {ADMIN_TOKEN}"}


def _get(path: str, **params):
    resp = httpx.get(f"{GATEWAY_URL}{path}", headers=HEADERS, params=params, timeout=10.0)
    resp.raise_for_status()
    return resp.json()


def _post(path: str, json: dict):
    resp = httpx.post(f"{GATEWAY_URL}{path}", headers=HEADERS, json=json, timeout=10.0)
    resp.raise_for_status()
    return resp.json()


st.subheader("Kill switches")
agents = _get("/admin/agents")["agents"]
cols = st.columns(len(agents) or 1)
for col, (agent_id, enabled) in zip(cols, agents.items()):
    with col:
        new_value = st.toggle(agent_id, value=enabled, key=f"toggle_{agent_id}")
        if new_value != enabled:
            _post(f"/admin/agents/{agent_id}/toggle", {"enabled": new_value})
            st.rerun()

st.subheader("Audit log")
col1, col2, col3 = st.columns(3)
agent_filter = col1.selectbox("Agent", ["(all)", *agents.keys()])
outcome_filter = col2.selectbox("Decision", ["(all)", "ALLOW", "DENY"])
limit = col3.number_input("Rows", min_value=10, max_value=1000, value=100, step=10)

params = {"limit": limit}
if agent_filter != "(all)":
    params["agent"] = agent_filter
if outcome_filter != "(all)":
    params["outcome"] = outcome_filter

rows = _get("/admin/audit", **params)["rows"]
st.caption(f"{len(rows)} row(s)")
st.dataframe(
    rows,
    column_order=[
        "timestamp", "agent", "user", "intent", "tool", "repo",
        "resource_type", "tier", "action", "outcome", "rule_id",
        "redactions", "fetch_error", "latency_ms",
    ],
    width="stretch",
)
