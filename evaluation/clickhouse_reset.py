"""Clear ClickHouse telemetry between scenario runs in an evaluation sweep.

Controlplane already resets fault-injection config and (for db-pool-exhaustion) the
connection pool between runs -- but nothing resets ClickHouse itself. Every trace,
log line, metric point, and deployment marker from every previous scenario in the
sweep stays there indefinitely. An agent investigating scenario N's alert can query
`get_recent_deployments` or `find_traces` and get back data from scenario N-1, N-2,
etc, and misattribute the wrong cause entirely -- this is exactly what happened
live: an agent investigating `redis-latency` found `bad-deployment`'s leftover
deployment marker (still sitting in ClickHouse from an earlier run in the same
sweep) and confidently diagnosed the wrong scenario.

This truncates the tables an investigating agent can read Class-0 tools would
otherwise return contaminated results, plus deployment_events, before each run
in the evaluation sweep. It intentionally also wipes Layer 0's seed baseline
deployment rows (dated 2026-07-27) -- that's a deliberate tradeoff: a stale
3-day-old fixture deployment is not useful context for diagnosing a fresh
incident either, and `make down && make up` restores it if ever needed for
something else.
"""

from __future__ import annotations

import os

import httpx

TABLES = (
    "otel.deployment_events",
    "otel.otel_traces",
    "otel.otel_logs",
    "otel.otel_metrics_gauge",
    "otel.otel_metrics_sum",
    "otel.otel_metrics_histogram",
    "otel.otel_metrics_summary",
    "otel.otel_metrics_exp_histogram",
)


def reset_telemetry(
    url: str | None = None,
    user: str | None = None,
    password: str | None = None,
    timeout: float = 15.0,
) -> None:
    """Truncate every telemetry table so the next scenario run starts from a clean
    ClickHouse slate. Best-effort per table: a table that doesn't exist yet (e.g. a
    metric type nothing has ever emitted) shouldn't abort the whole sweep.
    """
    url = (url or os.environ.get("CLICKHOUSE_URL", "http://localhost:8123")).rstrip("/")
    user = user or os.environ.get("CLICKHOUSE_USER", "default")
    password = password if password is not None else os.environ.get("CLICKHOUSE_PASSWORD", "faultline_otel")

    with httpx.Client(timeout=timeout) as client:
        for table in TABLES:
            resp = client.post(url + "/", content=f"TRUNCATE TABLE IF EXISTS {table}".encode(), auth=(user, password))
            if resp.status_code != 200:
                # Best-effort: log to stderr via print and move on rather than aborting
                # the whole evaluation sweep over one table.
                print(f"warning: could not truncate {table}: {resp.status_code} {resp.text[:200]}")
