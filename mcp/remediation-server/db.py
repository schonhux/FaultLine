"""Postgres-backed store for remediation proposals/approvals/executions.

The `remediations` table (infrastructure/docker/postgres/003-remediations.sql) is
simultaneously the approval queue -- a human approves/denies a pending row via
evaluation/approve.py -- and the permanent audit log the safety model requires:
nothing is ever deleted, only status-updated.

Uses plain synchronous psycopg calls. These are short, local-network queries against
Postgres and the tool functions that call them are themselves plain (non-async)
functions run by FastMCP in a worker thread, so blocking here doesn't stall an event
loop -- see tools/remediation.py.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import psycopg


def _dsn() -> str:
    return os.environ.get(
        "POSTGRES_DSN", "postgresql://shopgrid:shopgrid@postgres:5432/shopgrid"
    )


def connect() -> psycopg.Connection:
    return psycopg.connect(_dsn(), autocommit=True)


def count_allowed_for_run(run_id: str | None) -> int:
    if not run_id:
        return 0
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM remediations WHERE run_id = %s AND policy_decision = 'allowed'",
            (run_id,),
        )
        row = cur.fetchone()
        return row[0] if row else 0


def insert_remediation(
    tool: str,
    target: str,
    risk_class: int | None,
    justification: str,
    run_id: str | None,
    policy_decision: str,
    policy_reason: str,
    status: str,
) -> str:
    remediation_id = str(uuid.uuid4())
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO remediations
                (id, run_id, tool, target, class, justification, policy_decision,
                 policy_reason, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                remediation_id,
                run_id,
                tool,
                target,
                risk_class,
                justification,
                policy_decision,
                policy_reason,
                status,
            ),
        )
    return remediation_id


_ROW_COLUMNS = [
    "id",
    "run_id",
    "tool",
    "target",
    "class",
    "justification",
    "policy_decision",
    "policy_reason",
    "status",
    "decided_by",
    "execution_result",
]


def get_remediation(remediation_id: str) -> dict[str, Any] | None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT {', '.join(_ROW_COLUMNS)} FROM remediations WHERE id = %s",
            (remediation_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return dict(zip(_ROW_COLUMNS, row))


def set_executed(remediation_id: str, execution_result: str) -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE remediations SET status = 'executed', executed_at = now(), "
            "execution_result = %s WHERE id = %s",
            (execution_result, remediation_id),
        )


def set_execution_failed(remediation_id: str, error: str) -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE remediations SET status = 'execution_failed', executed_at = now(), "
            "execution_result = %s WHERE id = %s",
            (error, remediation_id),
        )


def list_pending(run_id: str | None = None) -> list[dict[str, Any]]:
    query = (
        "SELECT id, run_id, tool, target, class, justification, requested_at "
        "FROM remediations WHERE status = 'pending_approval'"
    )
    params: tuple = ()
    if run_id:
        query += " AND run_id = %s"
        params = (run_id,)
    query += " ORDER BY requested_at ASC"
    with connect() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        cols = ["id", "run_id", "tool", "target", "class", "justification", "requested_at"]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
