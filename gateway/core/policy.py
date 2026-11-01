"""The decision procedure. Pure function, no I/O -- every outcome is
reproducible from (call, context, policy) alone.

Split into two phases so a single reordering fixes a real bug in the
original design: get_pr_diff's tier (needed by R08/R11) isn't known until
the backend lists the diff's changed files, but that must not happen before
the cheap, context-free checks (role, repo, intent, write-constraints) have
already passed -- otherwise an unauthorized caller triggers a GitHub call
before being denied.

decide_phase1: R01-R07, R09, R10, R12 -- never need context.tier.
decide_phase2: R08, R11 -- require a final context.tier.
decide(): the common case where tier is already known synchronously, so
both phases run back to back. get_pr_diff's handler (in server.py) calls
the two phases separately with a GitHub call in between.
"""

from __future__ import annotations

from gateway.core.models import DataContext, Decision, PolicyConfig, ToolCall

_TIER_ORDER = {"T0": 0, "T1": 1, "T2": 2, "T3": 3}

# The canonical tool catalog. Anything not in here is denied by R06
# regardless of what classification.yaml says, as defense in depth in case
# a tool is ever registered on the MCP server by mistake.
CANONICAL_TOOLS = {
    "read_file",
    "list_issues",
    "get_issue",
    "get_issue_comments",
    "search_issues",
    "list_labels",
    "add_labels",
    "get_pr",
    "get_pr_diff",
    "list_commits",
    "post_review_comment",
}

_WRITE_ACTION_NAME = {
    "add_labels": "add_labels",
    "post_review_comment": "post_review_comment",
}


def _deny(rule_id: str, reason: str) -> Decision:
    return Decision(outcome="DENY", rule_id=rule_id, reason=reason)


def decide_phase1(
    call: ToolCall, context: DataContext, policy: PolicyConfig
) -> Decision | None:
    """Context-free / cheap checks. No GitHub call has happened or will
    happen as a result of anything checked here. Returns a DENY Decision on
    the first failing rule, or None if every phase-1 rule passed.
    """
    session = call.session

    # R04 intent registered to this agent
    agent = policy.agents.get(call.agent_id)
    if agent is None:
        # Not R01 (that's agent-token authentication, checked in auth.py
        # before policy.decide is ever called) -- an authenticated agent
        # calling with an intent that isn't configured for it at all.
        return _deny("R04", f"agent '{call.agent_id}' is not registered")

    intent = agent.intents.get(session.intent)
    if intent is None:
        return _deny(
            "R04", f"intent '{session.intent}' is not registered for this agent"
        )

    # R05 user may use agent
    user = policy.users.get(session.user)
    if user is None:
        return _deny("R05", f"user '{session.user}' is not registered")
    if user.role not in agent.allowed_roles:
        return _deny(
            "R05", f"role '{user.role}' may not use agent '{call.agent_id}'"
        )

    # R06 tool known
    if call.tool not in CANONICAL_TOOLS:
        return _deny("R06", f"tool '{call.tool}' is not in the canonical catalog")

    # R07 argument constraints
    write_action = _WRITE_ACTION_NAME.get(call.tool)
    if write_action is not None:
        constraint = intent.write.get(write_action)
        if constraint is None:
            return _deny(
                "R07", f"'{write_action}' is not a permitted write for this intent"
            )
        if constraint.only_fields:
            extra = set(call.args) - set(constraint.only_fields) - {"repo", "number"}
            if extra:
                return _deny(
                    "R07", f"fields {sorted(extra)} may not be set by this write"
                )
        for field, required_value in constraint.require.items():
            if call.args.get(field) != required_value:
                return _deny(
                    "R07",
                    f"'{field}' must be '{required_value}' for this write",
                )

    # R09 within intent: resource type (+ action) must be granted by the intent
    if context.action == "write":
        if write_action is None or write_action not in intent.write:
            return _deny("R09", "this write is outside the declared intent")
    else:
        if context.resource_type not in intent.read:
            return _deny(
                "R09",
                f"resource type '{context.resource_type}' is outside the "
                f"declared intent",
            )

    # R10 repo permitted: repo in intersection of intent's and user's repos
    if context.repo not in intent.repos or context.repo not in user.repos:
        return _deny("R10", f"repo '{context.repo}' is not permitted")

    # R12 write permitted: the human user must also be allowed this write
    if context.action == "write" and write_action is not None:
        if not user.may_write(write_action):
            return _deny("R12", f"user may not perform '{write_action}'")

    return None


def decide_phase2(
    call: ToolCall, context: DataContext, policy: PolicyConfig
) -> Decision:
    """Tier-dependent checks. context.tier must be final by this point."""
    session = call.session
    agent = policy.agents[call.agent_id]
    intent = agent.intents[session.intent]
    user = policy.users[session.user]

    # R08 restricted data: T3 is always denied, no override
    if context.tier == "T3":
        return _deny("R08", "T3 restricted data is never released to any agent")

    # R11 tier permitted: at or below the lower of the intent's and user's caps
    cap = min(_TIER_ORDER[intent.max_tier], _TIER_ORDER[user.max_tier])
    if context.tier is None or _TIER_ORDER[context.tier] > cap:
        return _deny("R11", f"tier '{context.tier}' exceeds the permitted cap")

    return Decision(outcome="ALLOW", rule_id="ALLOW", reason="all checks passed")


def decide(call: ToolCall, context: DataContext, policy: PolicyConfig) -> Decision:
    """For every tool except get_pr_diff, whose tier isn't known until a
    GitHub metadata call runs between phase 1 and phase 2 (see server.py).
    """
    decision = decide_phase1(call, context, policy)
    if decision is not None:
        return decision
    return decide_phase2(call, context, policy)
