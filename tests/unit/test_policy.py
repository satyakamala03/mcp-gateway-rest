"""One test per rule ID, against the real config/policy.yaml."""

from __future__ import annotations

from gateway.core.context import resolve_context, tier_for_paths
from gateway.core.models import DataContext
from gateway.core.policy import decide
from tests.conftest import make_call


def ctx(policy, classification, tool, **args):
    return resolve_context(tool, args, policy, classification)


def test_r04_intent_not_registered(policy, classification):
    call = make_call("alice", "review-agent", "no_such_intent", "get_pr", repo="storefront", number=10)
    d = decide(call, ctx(policy, classification, "get_pr", repo="storefront", number=10), policy)
    assert d.outcome == "DENY"
    assert d.rule_id == "R04"


def test_r05_role_not_entitled(policy, classification):
    # bob is "support", triage-agent's allowed_roles includes support, but
    # review-agent requires "engineer" -- bob may not use review-agent at all.
    call = make_call("bob", "review-agent", "explain_code", "get_pr", repo="storefront", number=10)
    d = decide(call, ctx(policy, classification, "get_pr", repo="storefront", number=10), policy)
    assert d.outcome == "DENY"
    assert d.rule_id == "R05"


def test_r06_unmapped_tool(policy, classification):
    call = make_call("alice", "review-agent", "review_pr", "merge_pr", repo="storefront", number=10)
    context = ctx(policy, classification, "get_pr", repo="storefront", number=10)
    d = decide(call, context, policy)
    assert d.outcome == "DENY"
    assert d.rule_id == "R06"


def test_r07_arg_constraint_violated(policy, classification):
    # add_labels under triage_issues only allows the labels field.
    call = make_call(
        "bob", "triage-agent", "triage_issues", "add_labels",
        repo="storefront", number=1, labels=["bug"], title="renamed",
    )
    context = ctx(policy, classification, "add_labels", repo="storefront", number=1)
    d = decide(call, context, policy)
    assert d.outcome == "DENY"
    assert d.rule_id == "R07"


def test_post_review_comment_allowed_with_canonical_args_only(policy, classification):
    # No event/approve field exists on this tool at all -- "comment only"
    # is structural (the backend method has no approve capability), not an
    # R07 argument check.
    call = make_call(
        "alice", "review-agent", "review_pr", "post_review_comment",
        repo="storefront", number=10, body="lgtm",
    )
    context = ctx(policy, classification, "post_review_comment", repo="storefront", number=10)
    d = decide(call, context, policy)
    assert d.outcome == "ALLOW"


def test_secret_path_denied_via_r09_no_intent_grants_secret(policy, classification):
    # "secret" is never in any intent's read list, so a direct read_file on
    # .env is denied at R09 (outside intent) before R08 is even reached --
    # R08 is a separate backstop (see test below) for the one path that
    # reaches phase 2 with resource_type already granted but a dynamically
    # computed T3 tier: get_pr_diff.
    call = make_call("alice", "review-agent", "explain_code", "read_file", repo="storefront", path=".env")
    context = ctx(policy, classification, "read_file", repo="storefront", path=".env")
    assert context.tier == "T3"
    d = decide(call, context, policy)
    assert d.outcome == "DENY"
    assert d.rule_id == "R09"


def test_secret_path_first_match_wins_docs_env(policy, classification):
    # docs/.env must classify as secret (T3), not docs (T0) -- path rules
    # run most-restrictive-first. Still denied via R09 for the same reason
    # as above.
    call = make_call("alice", "review-agent", "explain_code", "read_file", repo="storefront", path="docs/.env")
    context = ctx(policy, classification, "read_file", repo="storefront", path="docs/.env")
    assert context.resource_type == "secret"
    assert context.tier == "T3"
    d = decide(call, context, policy)
    assert d.outcome == "DENY"
    assert d.rule_id == "R09"


def test_r08_restricted_data_via_pr_diff_tier_escalation(policy, classification):
    # pr_diff IS granted by review_pr, so this is the one path that reaches
    # phase 2 with a resource type the intent allows but a tier escalated
    # to T3 by the riskiest changed file -- this is what R08 actually
    # guards against, with no override.
    paths = ["src/app.py", ".env"]
    tier = tier_for_paths(paths, classification)
    assert tier == "T3"
    context = DataContext(
        repo="storefront", visibility="public", resource_type="pr_diff",
        tier=tier, action="read", paths_checked=paths,
    )
    call = make_call("alice", "review-agent", "review_pr", "get_pr_diff", repo="storefront", number=10)
    d = decide(call, context, policy)
    assert d.outcome == "DENY"
    assert d.rule_id == "R08"


def test_r09_outside_intent_same_call_different_agents(policy, classification):
    context = ctx(policy, classification, "read_file", repo="storefront", path="src/app.py")
    assert context.resource_type == "code"

    review_call = make_call("alice", "review-agent", "explain_code", "read_file", repo="storefront", path="src/app.py")
    assert decide(review_call, context, policy).outcome == "ALLOW"

    triage_call = make_call("alice", "triage-agent", "triage_issues", "read_file", repo="storefront", path="src/app.py")
    d = decide(triage_call, context, policy)
    assert d.outcome == "DENY"
    assert d.rule_id == "R09"


def test_r10_repo_not_permitted(policy, classification):
    # bob's user entry only lists storefront.
    call = make_call("bob", "triage-agent", "triage_issues", "get_issue", repo="payments-api", number=1)
    context = ctx(policy, classification, "get_issue", repo="payments-api", number=1)
    d = decide(call, context, policy)
    assert d.outcome == "DENY"
    assert d.rule_id == "R10"


def test_r09_outside_intent_for_ungranted_resource_type(policy, classification):
    # "config" is never in any intent's read list, so this denies at R09,
    # not R11 -- even though alice's own max_tier (T2) could cover it.
    call = make_call("alice", "review-agent", "review_pr", "read_file", repo="payments-api", path="config/prod.yaml")
    context = ctx(policy, classification, "read_file", repo="payments-api", path="config/prod.yaml")
    assert context.tier == "T2"
    d = decide(call, context, policy)
    assert d.outcome == "DENY"
    assert d.rule_id == "R09"


def test_r11_tier_exceeded_via_pr_diff_tier_escalation(policy, classification):
    # pr_diff IS granted by review_pr (max_tier T1). A PR touching a
    # config/ file (T2, not T3) reaches phase 2 with a granted resource
    # type but a tier above the cap -- this is what R11 guards, distinct
    # from R08's T3-always-denied backstop.
    paths = ["src/app.py", "config/prod.yaml"]
    tier = tier_for_paths(paths, classification)
    assert tier == "T2"
    context = DataContext(
        repo="payments-api", visibility="private", resource_type="pr_diff",
        tier=tier, action="read", paths_checked=paths,
    )
    call = make_call("alice", "review-agent", "review_pr", "get_pr_diff", repo="payments-api", number=11)
    d = decide(call, context, policy)
    assert d.outcome == "DENY"
    assert d.rule_id == "R11"


def test_r07_write_outside_intent_write_list(policy, classification):
    # find_duplicates grants no writes at all, so add_labels is denied at
    # R07 (the intent simply has no constraint entry for this write) before
    # repo/tier/user-write checks are ever reached.
    call = make_call("bob", "triage-agent", "find_duplicates", "add_labels", repo="storefront", number=1, labels=["bug"])
    context = ctx(policy, classification, "add_labels", repo="storefront", number=1)
    d = decide(call, context, policy)
    assert d.outcome == "DENY"
    assert d.rule_id == "R07"


def test_r12_write_not_permitted(policy, classification):
    # R12 only fires once the intent itself permits the write (R07/R09
    # pass) but the human user's own write list doesn't. No user in
    # policy.yaml exercises this in isolation (bob lacks the role for
    # review-agent before he'd ever reach R12), so build a minimal policy
    # fixture with a correctly-roled, write-restricted user.
    from gateway.core.models import AgentConfig, Intent, PolicyConfig, RepoConfig, UserConfig, WriteConstraint

    local_policy = PolicyConfig(
        repos={"storefront": RepoConfig(visibility="public")},
        agents={
            "triage-agent": AgentConfig(
                allowed_roles=["support"],
                intents={
                    "triage_issues": Intent(
                        read=["issue", "label"],
                        write={"add_labels": WriteConstraint(only_fields=["labels"])},
                        max_tier="T1",
                        repos=["storefront"],
                    )
                },
            )
        },
        users={
            "carol": UserConfig(role="support", repos=["storefront"], max_tier="T1", write=[]),
        },
    )
    call = make_call("carol", "triage-agent", "triage_issues", "add_labels", repo="storefront", number=1, labels=["bug"])
    context = ctx(local_policy, classification, "add_labels", repo="storefront", number=1)
    d = decide(call, context, local_policy)
    assert d.outcome == "DENY"
    assert d.rule_id == "R12"


def test_allow_happy_path(policy, classification):
    call = make_call("bob", "triage-agent", "triage_issues", "add_labels", repo="storefront", number=1, labels=["bug"])
    context = ctx(policy, classification, "add_labels", repo="storefront", number=1)
    d = decide(call, context, policy)
    assert d.outcome == "ALLOW"
    assert d.rule_id == "ALLOW"
