from __future__ import annotations

from gateway.core.context import classify_path, resolve_context, tier_for_paths


def test_classify_secret_path(classification):
    resource_type, tier = classify_path(".env", classification)
    assert (resource_type, tier) == ("secret", "T3")


def test_classify_secret_wins_first_match(classification):
    resource_type, tier = classify_path("docs/.env", classification)
    assert (resource_type, tier) == ("secret", "T3")


def test_classify_docs(classification):
    assert classify_path("docs/guide.md", classification) == ("docs", "T0")
    assert classify_path("README.md", classification) == ("docs", "T0")


def test_classify_config(classification):
    assert classify_path("config/prod.yaml", classification) == ("config", "T2")


def test_classify_code_default(classification):
    assert classify_path("src/app.py", classification) == ("code", "T1")
    assert classify_path("random/other/thing.txt", classification) == ("code", "T1")


def test_tier_for_paths_picks_riskiest(classification):
    # a PR touching .env must be classified T3 even though its other files
    # are lower tier
    assert tier_for_paths(["src/app.py", ".env"], classification) == "T3"
    assert tier_for_paths(["src/app.py", "README.md"], classification) == "T1"
    assert tier_for_paths([], classification) == "T1"


def test_resolve_context_read_file_uses_path_classification(policy, classification):
    ctx = resolve_context("read_file", {"repo": "storefront", "path": "src/app.py"}, policy, classification)
    assert ctx.resource_type == "code"
    assert ctx.tier == "T1"
    assert ctx.action == "read"
    assert ctx.visibility == "public"


def test_resolve_context_tool_name_classification(policy, classification):
    ctx = resolve_context("get_issue", {"repo": "payments-api", "number": 1}, policy, classification)
    assert ctx.resource_type == "issue"
    assert ctx.tier == "T1"
    assert ctx.visibility == "private"


def test_resolve_context_write_action(policy, classification):
    ctx = resolve_context("add_labels", {"repo": "storefront", "number": 1, "labels": ["bug"]}, policy, classification)
    assert ctx.action == "write"


def test_resolve_context_unknown_repo_has_no_visibility(policy, classification):
    ctx = resolve_context("get_issue", {"repo": "nonexistent-repo", "number": 1}, policy, classification)
    assert ctx.visibility is None
