from __future__ import annotations

from gateway.core.redact import redact, redact_text


def test_redacts_github_token():
    cleaned, count = redact_text("token=ghp_abcdefghijklmnopqrstuvwxyz0123456789 end")
    assert "ghp_" not in cleaned
    assert count == 1


def test_redacts_email():
    cleaned, count = redact_text("contact oncall@example.com for help")
    assert "oncall@example.com" not in cleaned
    assert count == 1


def test_redacts_private_key_header():
    cleaned, count = redact_text("-----BEGIN RSA PRIVATE KEY-----\nabc\n")
    assert "PRIVATE KEY" not in cleaned
    assert count == 1


def test_no_redaction_for_clean_text():
    cleaned, count = redact_text("just a normal issue body")
    assert cleaned == "just a normal issue body"
    assert count == 0


def test_redact_recurses_into_nested_structures():
    obj = {
        "title": "normal",
        "comments": [
            {"author": "a", "body": "leaked key sk-abcdefghijklmnopqrstuvwx"},
        ],
    }
    cleaned, count = redact(obj)
    assert count == 1
    assert "sk-" not in cleaned["comments"][0]["body"]
    assert cleaned["title"] == "normal"
