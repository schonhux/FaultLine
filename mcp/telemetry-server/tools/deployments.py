"""get_recent_deployments -- Class 0 (harmless read) tool, backed by otel.deployment_events.

Note on time filtering: the Layer 0 seed data is deliberately dated 2026-07-27 (fixed,
historical timestamps), while a scenario's own deployment_marker (if any) is inserted
with the real wall-clock time when the scenario runner injects the fault. A naive
"only look at the last N minutes" filter would silently miss the seed rows. So
since_minutes here is opt-in: by default this returns the most recent `limit` rows
regardless of age, which works for both cases.
"""

from __future__ import annotations

from clickhouse_client import ClickHouseClient, sql_quote
from tools.common import validate_limit, validate_minutes, validate_service


async def get_recent_deployments(
    ch: ClickHouseClient,
    service: str | None = None,
    since_minutes: int | None = None,
    limit: int = 20,
) -> dict:
    """List recent deployment events (service, version, git_commit, deployed_at, config).

    since_minutes is optional -- omit it to just get the most recent `limit` deploys
    regardless of how long ago they happened.
    """
    service = validate_service(service)
    limit = validate_limit(limit, max_limit=100)

    clauses = []
    if service is not None:
        clauses.append(f"service = '{sql_quote(service)}'")
    if since_minutes is not None:
        since_minutes = validate_minutes(since_minutes)
        clauses.append(f"deployed_at > now() - INTERVAL {since_minutes} MINUTE")

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = (
        "SELECT service, version, git_commit, deployed_at, config "
        f"FROM otel.deployment_events {where} "
        f"ORDER BY deployed_at DESC LIMIT {limit}"
    )
    rows = await ch.query_rows(sql)
    return {"service": service, "since_minutes": since_minutes, "count": len(rows), "deployments": rows}
