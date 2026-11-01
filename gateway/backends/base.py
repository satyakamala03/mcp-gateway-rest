"""The GitHubBackend protocol. The core only ever talks to this interface;
RestBackend and FakeBackend both return the same typed objects.

v1 ships RestBackend and FakeBackend only. A proxy backend calling GitHub's
official MCP server (McpBackend) is deliberately not built -- this Protocol
is the only trace of that seam. To add it later: implement every method
below against an MCP client pointed at `github-mcp-server`, mapping each
canonical method to one upstream tool (see docs/MCP Gateway Technical
Design.md's proxy-mode translation table), register it as
gateway.backends.mcp_proxy.McpBackend, and switch config/gateway.yaml's
`backend:` field to `mcp`. No other code changes.
"""

from __future__ import annotations

from typing import Protocol

from gateway.backends.models import Comment, Commit, FileContent, Issue, PrDiff, PullRequest


class BackendError(Exception):
    """Raised by a backend when the underlying GitHub call fails after
    policy has already ALLOWed -- the caller records this as an audit
    fetch_error rather than changing the decision.
    """


class GitHubBackend(Protocol):
    # reads
    def read_file(self, repo: str, path: str) -> FileContent: ...
    def list_issues(self, repo: str, state: str) -> list[Issue]: ...
    def get_issue(self, repo: str, number: int) -> Issue: ...
    def get_issue_comments(self, repo: str, number: int) -> list[Comment]: ...
    def search_issues(self, repo: str, query: str) -> list[Issue]: ...
    def list_labels(self, repo: str) -> list[str]: ...
    def get_pr(self, repo: str, number: int) -> PullRequest: ...
    def list_pr_files(self, repo: str, number: int) -> list[str]: ...  # paths only
    def get_pr_diff(self, repo: str, number: int) -> PrDiff: ...
    def list_commits(self, repo: str, limit: int) -> list[Commit]: ...

    # writes
    def add_labels(self, repo: str, number: int, labels: list[str]) -> None: ...
    def post_review_comment(self, repo: str, number: int, body: str) -> None: ...
