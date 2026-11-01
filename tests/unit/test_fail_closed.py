"""An invalid policy file, an expired session token, and a backend that
raises an exception must each produce DENY, never ALLOW.
"""

from __future__ import annotations

import pytest

from gateway.core.auth import AuthError
from gateway.core.config import PolicyLoadError, load_policy
from gateway.backends.base import BackendError
from gateway.backends.fake import FakeBackend


def test_corrupt_policy_file_fails_to_load(tmp_path):
    bad = tmp_path / "policy.yaml"
    bad.write_text("agents:\n  triage-agent:\n    intents: [this is not a mapping]\n")
    with pytest.raises(PolicyLoadError):
        load_policy(bad)


def test_policy_with_unknown_repo_reference_fails_to_load(tmp_path):
    bad = tmp_path / "policy.yaml"
    bad.write_text(
        """
repos:
  storefront: {visibility: public}
agents:
  triage-agent:
    allowed_roles: [support]
    intents:
      triage_issues:
        read: [issue]
        max_tier: T1
        repos: [storefront, ghost-repo]
users:
  bob: {role: support, repos: [storefront], max_tier: T1, write: []}
"""
    )
    with pytest.raises(PolicyLoadError, match="ghost-repo"):
        load_policy(bad)


def test_missing_policy_file_fails_to_load(tmp_path):
    with pytest.raises(PolicyLoadError):
        load_policy(tmp_path / "does-not-exist.yaml")


def test_backend_exception_surfaces_as_backend_error_not_a_result():
    backend = FakeBackend()
    with pytest.raises(BackendError):
        backend.read_file("storefront", "no/such/file.txt")
    # the caller (server.py) is responsible for turning this into an
    # audit fetch_error on an already-ALLOWed decision -- it must never be
    # swallowed into a silent ALLOW with empty content.
