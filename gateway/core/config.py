"""Loads and validates policy.yaml / classification.yaml.

Kept separate from policy.py (which must stay pure, no I/O) and done once at
startup. An invalid file raises here, which stops the gateway from starting
rather than running with a partial policy -- fail-closed applies to policy
loading itself, not just to call-time decisions.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from gateway.core.models import ClassificationConfig, PolicyConfig


class PolicyLoadError(Exception):
    pass


def load_policy(path: str | Path) -> PolicyConfig:
    raw = _read_yaml(path)
    try:
        policy = PolicyConfig.model_validate(raw)
    except ValidationError as exc:
        raise PolicyLoadError(f"invalid policy file {path}: {exc}") from exc

    known_repos = set(policy.repos)
    for agent_id, agent in policy.agents.items():
        for intent_id, intent in agent.intents.items():
            unknown = set(intent.repos) - known_repos
            if unknown:
                raise PolicyLoadError(
                    f"agents.{agent_id}.intents.{intent_id}.repos references "
                    f"unknown repo(s) {sorted(unknown)}; add them to the "
                    f"top-level repos: block"
                )
    for user_id, user in policy.users.items():
        unknown = set(user.repos) - known_repos
        if unknown:
            raise PolicyLoadError(
                f"users.{user_id}.repos references unknown repo(s) "
                f"{sorted(unknown)}; add them to the top-level repos: block"
            )

    return policy


def load_classification(path: str | Path) -> ClassificationConfig:
    raw = _read_yaml(path)
    try:
        return ClassificationConfig.model_validate(raw)
    except ValidationError as exc:
        raise PolicyLoadError(f"invalid classification file {path}: {exc}") from exc


def _read_yaml(path: str | Path) -> dict:
    try:
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise PolicyLoadError(f"could not read {path}: {exc}") from exc
