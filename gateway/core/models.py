"""Core data models shared by policy, context, auth, audit and both backends.

These are plain pydantic models with no I/O and no GitHub-specific shape —
RestBackend and FakeBackend both return the same types.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Tier = Literal["T0", "T1", "T2", "T3"]
Action = Literal["read", "write"]
Outcome = Literal["ALLOW", "DENY"]
Visibility = Literal["public", "private"]

RESOURCE_TYPES = (
    "secret",
    "config",
    "ci_log",
    "security_alert",
    "org_member",
    "code",
    "pr_diff",
    "pull_request",
    "commit",
    "issue",
    "issue_comment",
    "docs",
    "label",
    "repo_meta",
)


# --- Identity / session -----------------------------------------------------


class SessionClaims(BaseModel):
    """Decoded, verified contents of the session JWT minted by Streamlit."""

    user: str
    agent: str
    intent: str
    issued_at: datetime
    expires_at: datetime
    session_id: str


class ToolCall(BaseModel):
    agent_id: str
    session: SessionClaims
    tool: str
    args: dict


# --- Policy decision ---------------------------------------------------------


class DataContext(BaseModel):
    repo: str
    # None when the repo isn't registered in policy.yaml's repos: block --
    # R10 (repo permitted) denies that case on list membership alone, so
    # visibility staying unknown doesn't block the decision.
    visibility: Visibility | None
    resource_type: str
    action: Action
    # None only transiently, before get_pr_diff's metadata lookup resolves it
    tier: Tier | None = None
    paths: list[str] = Field(default_factory=list)
    # set only for get_pr_diff, once list_pr_files has actually run
    paths_checked: list[str] | None = None


class Decision(BaseModel):
    outcome: Outcome
    rule_id: str  # e.g. "R08", or "ALLOW"
    reason: str


# --- Audit --------------------------------------------------------------------


class AuditRecord(BaseModel):
    id: int | None = None
    timestamp: datetime
    agent: str
    user: str
    intent: str
    tool: str
    data_context: DataContext
    decision: Decision
    redactions: int = 0
    latency_ms: float
    # set only when policy ALLOWed but the backend fetch itself then failed
    # (404 / 403 / 500 / rate-limited); decision.outcome stays ALLOW in that case
    fetch_error: str | None = None


# --- Policy config (parsed policy.yaml / classification.yaml) ---------------


class WriteConstraint(BaseModel):
    only_fields: list[str] = Field(default_factory=list)
    require: dict[str, str] = Field(default_factory=dict)


class Intent(BaseModel):
    read: list[str] = Field(default_factory=list)
    write: dict[str, WriteConstraint] = Field(default_factory=dict)
    max_tier: Tier
    repos: list[str]


class AgentConfig(BaseModel):
    allowed_roles: list[str]
    intents: dict[str, Intent]


class UserConfig(BaseModel):
    role: str
    repos: list[str]
    max_tier: Tier
    write: Literal["any"] | list[str] = Field(default_factory=list)

    def may_write(self, action_name: str) -> bool:
        if self.write == "any":
            return True
        return action_name in self.write


class RepoConfig(BaseModel):
    visibility: Visibility


class PolicyConfig(BaseModel):
    repos: dict[str, RepoConfig]
    agents: dict[str, AgentConfig]
    users: dict[str, UserConfig]


class PathRule(BaseModel):
    pattern: str
    resource_type: str
    tier: Tier


class ToolResourceType(BaseModel):
    resource_type: str
    tier: Tier


class ClassificationConfig(BaseModel):
    path_rules: list[PathRule]
    tool_resource_types: dict[str, ToolResourceType]
