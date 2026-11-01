"""Own-gateway mode: calls the GitHub REST API directly with a fine-grained
token limited to the two demo repos. The only backend code that knows
GitHub exists, alongside FakeBackend.
"""

from __future__ import annotations

import base64
import os

import httpx

from gateway.backends.base import BackendError
from gateway.backends.models import Comment, Commit, FileContent, Issue, PrDiff, PullRequest

GITHUB_API = "https://api.github.com"


class RestBackend:
    def __init__(self, token: str, owner: str | None = None, timeout: float = 15.0):
        self._owner = owner or os.environ.get("GITHUB_OWNER", "")
        self._client = httpx.Client(
            base_url=GITHUB_API,
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    def _full(self, repo: str) -> str:
        return f"{self._owner}/{repo}" if self._owner else repo

    def _get(self, path: str, **kwargs) -> httpx.Response:
        try:
            resp = self._client.get(path, **kwargs)
            resp.raise_for_status()
            return resp
        except httpx.HTTPStatusError as exc:
            raise BackendError(f"GitHub GET {path} failed: {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise BackendError(f"GitHub GET {path} failed: {exc}") from exc

    def _post(self, path: str, json: dict) -> httpx.Response:
        try:
            resp = self._client.post(path, json=json)
            resp.raise_for_status()
            return resp
        except httpx.HTTPStatusError as exc:
            raise BackendError(f"GitHub POST {path} failed: {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise BackendError(f"GitHub POST {path} failed: {exc}") from exc

    # --- reads ---------------------------------------------------------

    def read_file(self, repo: str, path: str) -> FileContent:
        resp = self._get(f"/repos/{self._full(repo)}/contents/{path}")
        data = resp.json()
        content = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        return FileContent(repo=repo, path=path, content=content)

    def list_issues(self, repo: str, state: str) -> list[Issue]:
        resp = self._get(f"/repos/{self._full(repo)}/issues", params={"state": state})
        return [
            self._issue_from_json(repo, item)
            for item in resp.json()
            if "pull_request" not in item  # the issues endpoint also returns PRs
        ]

    def get_issue(self, repo: str, number: int) -> Issue:
        resp = self._get(f"/repos/{self._full(repo)}/issues/{number}")
        return self._issue_from_json(repo, resp.json())

    def get_issue_comments(self, repo: str, number: int) -> list[Comment]:
        resp = self._get(f"/repos/{self._full(repo)}/issues/{number}/comments")
        return [Comment(author=c["user"]["login"], body=c["body"] or "") for c in resp.json()]

    def search_issues(self, repo: str, query: str) -> list[Issue]:
        scoped_query = f"{query} repo:{self._full(repo)} is:issue"
        resp = self._get("/search/issues", params={"q": scoped_query})
        return [self._issue_from_json(repo, item) for item in resp.json().get("items", [])]

    def list_labels(self, repo: str) -> list[str]:
        resp = self._get(f"/repos/{self._full(repo)}/labels")
        return [item["name"] for item in resp.json()]

    def get_pr(self, repo: str, number: int) -> PullRequest:
        resp = self._get(f"/repos/{self._full(repo)}/pulls/{number}")
        data = resp.json()
        return PullRequest(
            repo=repo, number=number, title=data["title"],
            body=data.get("body") or "", state=data["state"],
        )

    def list_pr_files(self, repo: str, number: int) -> list[str]:
        resp = self._get(f"/repos/{self._full(repo)}/pulls/{number}/files")
        return [item["filename"] for item in resp.json()]

    def get_pr_diff(self, repo: str, number: int) -> PrDiff:
        changed_files = self.list_pr_files(repo, number)
        resp = self._get(
            f"/repos/{self._full(repo)}/pulls/{number}",
            headers={"Accept": "application/vnd.github.v3.diff"},
        )
        return PrDiff(repo=repo, number=number, changed_files=changed_files, patch=resp.text)

    def list_commits(self, repo: str, limit: int) -> list[Commit]:
        resp = self._get(f"/repos/{self._full(repo)}/commits", params={"per_page": limit})
        return [
            Commit(
                sha=item["sha"],
                message=item["commit"]["message"],
                author=(item["commit"]["author"] or {}).get("name", "unknown"),
            )
            for item in resp.json()
        ]

    # --- writes ----------------------------------------------------------

    def add_labels(self, repo: str, number: int, labels: list[str]) -> None:
        self._post(f"/repos/{self._full(repo)}/issues/{number}/labels", json={"labels": labels})

    def post_review_comment(self, repo: str, number: int, body: str) -> None:
        # A plain issue/PR comment -- there is no approve/request-changes
        # capability on this method, which is the point.
        self._post(f"/repos/{self._full(repo)}/issues/{number}/comments", json={"body": body})

    @staticmethod
    def _issue_from_json(repo: str, data: dict) -> Issue:
        return Issue(
            repo=repo,
            number=data["number"],
            title=data["title"],
            body=data.get("body") or "",
            state=data["state"],
            labels=[label["name"] if isinstance(label, dict) else label for label in data.get("labels", [])],
        )
