import streamlit as st

st.set_page_config(page_title="MCP Gateway Demo", page_icon="🔒")

st.title("MCP Gateway Demo")

st.markdown(
    """
Two AI agents reach GitHub only through one MCP gateway, which allows,
denies or redacts each call based on agent identity, declared intent, the
human user behind it, and the sensitivity of the data being requested.

Use the pages in the sidebar:

- **Triage** -- chat with the Issue Triage Assistant
- **Review** -- chat with the PR Review Assistant
- **Admin** -- audit log, per-agent kill switches

On the Triage and Review pages, pick who you are and what you've
authorized this session to do before chatting. Every tool call the
assistant makes shows inline with an **ALLOW** or **DENY** badge and the
rule ID that decided it.
"""
)
