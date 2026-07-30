"""CLI for approving/denying Layer 5 remediation proposals -- the human half of the
guarded-remediation approval gate. Run this in a separate terminal while an agent
investigation with --enable-remediation is in progress; the agent's execute_remediation
tool call blocks (for a bounded time) waiting for exactly the decision this script
records.

    python3 evaluation/approve.py list
    python3 evaluation/approve.py approve <id> [--by NAME]
    python3 evaluation/approve.py deny <id> [--by NAME]

Runs on the host (like harness.py and clickhouse_reset.py), so POSTGRES_DSN defaults
to localhost:5432, matching docker-compose.yml's published postgres port.
"""

from __future__ import annotations

import argparse
import os

import psycopg


def _dsn() -> str:
    return os.environ.get(
        "POSTGRES_DSN", "postgresql://shopgrid:shopgrid@localhost:5432/shopgrid"
    )


def list_pending() -> None:
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, run_id, tool, target, class, justification, requested_at "
            "FROM remediations WHERE status = 'pending_approval' ORDER BY requested_at ASC"
        )
        rows = cur.fetchall()
    if not rows:
        print("No pending remediations.")
        return
    for remediation_id, run_id, tool, target, risk_class, justification, requested_at in rows:
        print(f"[{remediation_id}] class={risk_class} {tool} -> {target}  (run_id={run_id}, requested {requested_at})")
        print(f"    justification: {justification}")


def decide(remediation_id: str, status: str, decided_by: str) -> None:
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE remediations SET status = %s, decided_by = %s, decided_at = now() "
            "WHERE id = %s AND status = 'pending_approval' RETURNING id",
            (status, decided_by, remediation_id),
        )
        row = cur.fetchone()
    if row is None:
        print(f"No pending remediation with id {remediation_id} (already decided, or it doesn't exist).")
    else:
        print(f"{remediation_id}: set to {status}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Approve or deny pending FaultLine remediations.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")

    approve_p = sub.add_parser("approve")
    approve_p.add_argument("id")
    approve_p.add_argument("--by", default=os.environ.get("USER", "human"))

    deny_p = sub.add_parser("deny")
    deny_p.add_argument("id")
    deny_p.add_argument("--by", default=os.environ.get("USER", "human"))

    args = parser.parse_args()
    if args.command == "list":
        list_pending()
    elif args.command == "approve":
        decide(args.id, "approved", args.by)
    elif args.command == "deny":
        decide(args.id, "denied_by_human", args.by)


if __name__ == "__main__":
    main()
