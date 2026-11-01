"""Regex pass over every ALLOW response: masks token-shaped strings and
emails, and counts what was removed for the audit row.

Applied uniformly regardless of tier, since even allowed content can
accidentally contain a secret-shaped string (e.g. a pasted API key inside
an otherwise-T1 issue body) -- it's defense in depth, not a tier gate.
"""

from __future__ import annotations

import re
from typing import Any

_PATTERNS = [
    re.compile(r"ghp_[A-Za-z0-9]{36}"),  # GitHub personal access token
    re.compile(r"github_pat_[A-Za-z0-9_]{22,}"),  # fine-grained PAT
    re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS access key id
    re.compile(r"sk-[A-Za-z0-9]{20,}"),  # OpenAI/Anthropic-style API key
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),  # private key header
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),  # email address
]

_MASK = "[REDACTED]"


def redact_text(text: str) -> tuple[str, int]:
    count = 0

    def _sub(match: re.Match) -> str:
        nonlocal count
        count += 1
        return _MASK

    for pattern in _PATTERNS:
        text = pattern.sub(_sub, text)
    return text, count


def redact(obj: Any) -> tuple[Any, int]:
    """Recursively redacts every string value in a dict/list/model-dump,
    returning the cleaned structure and the total redaction count.
    """
    total = 0

    def walk(value: Any) -> Any:
        nonlocal total
        if isinstance(value, str):
            cleaned, count = redact_text(value)
            total += count
            return cleaned
        if isinstance(value, dict):
            return {k: walk(v) for k, v in value.items()}
        if isinstance(value, list):
            return [walk(v) for v in value]
        return value

    cleaned = walk(obj)
    return cleaned, total
