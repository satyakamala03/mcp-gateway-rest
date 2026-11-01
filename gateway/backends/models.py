"""Typed objects every GitHubBackend implementation returns. Shared across
RestBackend and FakeBackend so redaction and audit behave identically
regardless of which backend served the call.
"""

from __future__ import annotations

from pydantic import BaseModel


class FileContent(BaseModel):
    repo: str
    path: str
    content: str


class Comment(BaseModel):
    author: str
    body: str


class Issue(BaseModel):
    repo: str
    number: int
    title: str
    body: str
    state: str
    labels: list[str] = []


class PullRequest(BaseModel):
    repo: str
    number: int
    title: str
    body: str
    state: str


class PrDiff(BaseModel):
    repo: str
    number: int
    changed_files: list[str]
    patch: str


class Commit(BaseModel):
    sha: str
    message: str
    author: str
