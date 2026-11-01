from __future__ import annotations

from tests.scenarios.conftest import gateway_session


async def test_triage_agent_catalog_excludes_pr_and_file_tools(deps):
    async with gateway_session(deps, agent_id="triage-agent", user="bob", intent="triage_issues") as session:
        result = await session.list_tools()
        names = {t.name for t in result.tools}

    assert names == {
        "list_issues", "get_issue", "get_issue_comments", "search_issues",
        "list_labels", "add_labels",
    }


async def test_review_agent_catalog_excludes_label_tools(deps):
    async with gateway_session(deps, agent_id="review-agent", user="alice", intent="review_pr") as session:
        result = await session.list_tools()
        names = {t.name for t in result.tools}

    # review-agent's intents never grant the "label" resource type.
    assert "list_labels" not in names
    assert "add_labels" not in names
    # but it does grant pull_request/pr_diff/code/docs/commit/issue/issue_comment.
    assert {"read_file", "get_pr", "get_pr_diff", "list_commits", "post_review_comment"} <= names


async def test_discovery_is_convenience_not_the_control(deps):
    # Filtered discovery hides add_labels from review-agent's catalog, but
    # call-time checks still enforce if it's called anyway.
    async with gateway_session(deps, agent_id="review-agent", user="alice", intent="review_pr") as session:
        result = await session.call_tool("add_labels", {"repo": "storefront", "number": 1, "labels": ["bug"]})
        assert result.isError is True
