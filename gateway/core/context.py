"""Turns a tool name + arguments into a DataContext: repo, visibility,
resource type, tier, action. No I/O -- the one exception (get_pr_diff's
changed-file lookup) is orchestrated by the server, not here; this module
only classifies paths it's given.
"""

from __future__ import annotations

import fnmatch
import posixpath

from gateway.core.models import (
    ClassificationConfig,
    DataContext,
    PolicyConfig,
    Tier,
)

_TIER_ORDER: dict[Tier, int] = {"T0": 0, "T1": 1, "T2": 2, "T3": 3}

# Tools whose write action is a write; everything else here is a read.
_WRITE_TOOLS = {"add_labels", "post_review_comment"}


def classify_path(path: str, classification: ClassificationConfig) -> tuple[str, Tier]:
    """First-match-wins over path_rules, most restrictive first.

    A bare filename pattern (".env*", "*.pem", ...) matches at any depth --
    checked against the basename as well as the full path -- so
    "docs/.env" classifies as secret, not docs, same as a root-level
    ".env". A "dir/**" pattern only matches under that directory, checked
    against the full path alone.
    """
    basename = posixpath.basename(path)
    for rule in classification.path_rules:
        if fnmatch.fnmatch(path, rule.pattern) or fnmatch.fnmatch(
            basename, rule.pattern
        ):
            return rule.resource_type, rule.tier
    # path_rules always ends in a catch-all ("**"), but fall back defensively
    return "code", "T1"


def tier_for_paths(paths: list[str], classification: ClassificationConfig) -> Tier:
    """A PR diff inherits the tier of its riskiest changed path."""
    if not paths:
        return "T1"
    tiers = (classify_path(p, classification)[1] for p in paths)
    return max(tiers, key=lambda t: _TIER_ORDER[t])


def resolve_context(
    tool: str,
    args: dict,
    policy: PolicyConfig,
    classification: ClassificationConfig,
) -> DataContext:
    """Resolve the full, final context for every tool except get_pr_diff.

    get_pr_diff is handled by resolve_pr_diff_provisional + the server's
    two-phase orchestration, since its tier depends on a GitHub call that
    must not happen before the cheap checks pass.
    """
    repo = args["repo"]
    repo_entry = policy.repos.get(repo)
    visibility = repo_entry.visibility if repo_entry else None
    action = "write" if tool in _WRITE_TOOLS else "read"

    if tool == "read_file":
        resource_type, tier = classify_path(args["path"], classification)
        return DataContext(
            repo=repo,
            visibility=visibility,
            resource_type=resource_type,
            tier=tier,
            action=action,
            paths=[args["path"]],
        )

    entry = classification.tool_resource_types.get(tool)
    if entry is None:
        # Unknown tool: resource_type left generic so R06 (tool known) is the
        # rule that denies it, not a classification lookup failure.
        return DataContext(
            repo=repo,
            visibility=visibility,
            resource_type="unknown",
            tier="T3",
            action=action,
        )

    return DataContext(
        repo=repo,
        visibility=visibility,
        resource_type=entry.resource_type,
        tier=entry.tier,
        action=action,
    )


def resolve_pr_diff_provisional(
    tool: str,
    args: dict,
    policy: PolicyConfig,
) -> DataContext:
    """Partial context for get_pr_diff: everything except tier, which is
    unknown until the server runs the changed-file metadata lookup.
    """
    repo = args["repo"]
    repo_entry = policy.repos.get(repo)
    return DataContext(
        repo=repo,
        visibility=repo_entry.visibility if repo_entry else None,
        resource_type="pr_diff",
        tier=None,
        action="read",
    )
