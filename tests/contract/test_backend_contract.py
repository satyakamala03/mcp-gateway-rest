"""The same assertions run against every backend -- a backend is done when
it passes this suite. FakeBackend runs offline and always executes; the
RestBackend parametrization is marked integration and skipped unless
GITHUB_TOKEN/GITHUB_OWNER are set, since it needs the real demo repos.
"""

from __future__ import annotations

import os

import pytest

from gateway.backends.fake import FakeBackend
from gateway.backends.rest import RestBackend

REQUIRED_ENV = ("GITHUB_TOKEN", "GITHUB_OWNER")
HAS_LIVE_CREDS = all(os.environ.get(v) for v in REQUIRED_ENV)


def _fake_backend():
    return FakeBackend()


def _rest_backend():
    return RestBackend(token=os.environ["GITHUB_TOKEN"], owner=os.environ["GITHUB_OWNER"])


@pytest.fixture(
    params=[
        pytest.param(_fake_backend, id="fake"),
        pytest.param(
            _rest_backend, id="rest",
            marks=pytest.mark.skipif(not HAS_LIVE_CREDS, reason="GITHUB_TOKEN/GITHUB_OWNER not set"),
        ),
    ]
)
def backend(request):
    return request.param()


@pytest.mark.integration
def test_read_file(backend):
    content = backend.read_file("storefront", "README.md")
    assert content.repo == "storefront"
    assert content.path == "README.md"
    assert isinstance(content.content, str)


@pytest.mark.integration
def test_list_and_get_issue(backend):
    issues = backend.list_issues("storefront", "open")
    assert isinstance(issues, list)
    if issues:
        first = backend.get_issue("storefront", issues[0].number)
        assert first.number == issues[0].number


@pytest.mark.integration
def test_list_labels(backend):
    labels = backend.list_labels("storefront")
    assert isinstance(labels, list)


@pytest.mark.integration
def test_add_labels_round_trip(backend):
    issues = backend.list_issues("storefront", "open")
    assert issues, "demo repo needs at least one open issue for this test"
    number = issues[0].number
    backend.add_labels("storefront", number, ["needs-triage"])
    updated = backend.get_issue("storefront", number)
    assert "needs-triage" in updated.labels
