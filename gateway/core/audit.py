"""SQLite-backed audit log (append-only) and agent_state (kill switch).

One row per end-user tool call, including get_pr_diff's preliminary
list_pr_files lookup -- that lookup never gets its own row; it only sets
paths_checked on the one row written for the call it's part of.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

from gateway.core.models import AuditRecord, DataContext, Decision

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    agent TEXT NOT NULL,
    user TEXT NOT NULL,
    intent TEXT NOT NULL,
    tool TEXT NOT NULL,
    repo TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    tier TEXT,
    action TEXT NOT NULL,
    outcome TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    redactions INTEGER NOT NULL DEFAULT 0,
    latency_ms REAL NOT NULL,
    fetch_error TEXT,
    paths_checked TEXT
);

CREATE TABLE IF NOT EXISTS agent_state (
    agent_id TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);
"""


class AuditLog:
    def __init__(self, db_path: str | Path):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    def record(
        self,
        *,
        agent: str,
        user: str,
        intent: str,
        tool: str,
        context: DataContext,
        decision: Decision,
        latency_ms: float,
        redactions: int = 0,
        fetch_error: str | None = None,
    ) -> AuditRecord:
        now = datetime.now(UTC)
        paths_checked_json = (
            json.dumps(context.paths_checked)
            if context.paths_checked is not None
            else None
        )
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO audit
                    (timestamp, agent, user, intent, tool, repo, resource_type,
                     tier, action, outcome, rule_id, reason, redactions,
                     latency_ms, fetch_error, paths_checked)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    now.isoformat(),
                    agent,
                    user,
                    intent,
                    tool,
                    context.repo,
                    context.resource_type,
                    context.tier,
                    context.action,
                    decision.outcome,
                    decision.rule_id,
                    decision.reason,
                    redactions,
                    latency_ms,
                    fetch_error,
                    paths_checked_json,
                ),
            )
            conn.commit()

        return AuditRecord(
            timestamp=now,
            agent=agent,
            user=user,
            intent=intent,
            tool=tool,
            data_context=context,
            decision=decision,
            redactions=redactions,
            latency_ms=latency_ms,
            fetch_error=fetch_error,
        )

    def query(
        self,
        *,
        agent: str | None = None,
        user: str | None = None,
        outcome: str | None = None,
        limit: int = 200,
    ) -> list[dict]:
        clauses, params = [], []
        if agent:
            clauses.append("agent = ?")
            params.append(agent)
        if user:
            clauses.append("user = ?")
            params.append(user)
        if outcome:
            clauses.append("outcome = ?")
            params.append(outcome)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"SELECT * FROM audit {where} ORDER BY id DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
            return [dict(row) for row in rows]

    # --- kill switch ---------------------------------------------------

    def is_agent_enabled(self, agent_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT enabled FROM agent_state WHERE agent_id = ?", (agent_id,)
            ).fetchone()
            return bool(row[0]) if row else True  # default enabled

    def set_agent_enabled(self, agent_id: str, enabled: bool) -> None:
        now = datetime.now(UTC).isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO agent_state (agent_id, enabled, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(agent_id) DO UPDATE SET enabled = ?, updated_at = ?""",
                (agent_id, int(enabled), now, int(enabled), now),
            )
            conn.commit()

    def list_agent_states(self) -> dict[str, bool]:
        with self._connect() as conn:
            rows = conn.execute("SELECT agent_id, enabled FROM agent_state").fetchall()
            return {agent_id: bool(enabled) for agent_id, enabled in rows}
