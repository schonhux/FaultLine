"""Thin async ClickHouse HTTP client for the telemetry MCP server.

Same query surface already verified live in platform/controlplane (Rust): POST the SQL
body to the ClickHouse HTTP interface with basic auth. Unlike the Rust scenario runner
-- which only ever needs a single scalar (e.g. "is the pool gauge >= 18?") and parses a
bare TSV number -- the agent-facing tools need real rows back, so every query here
appends `FORMAT JSONEachRow` and parses newline-delimited JSON into plain dicts.

This client only ever talks to ClickHouse's read-only telemetry tables (otel_traces,
otel_logs, otel_metrics_gauge/sum, deployment_events). It never touches Postgres, so
it has no code path that could reach the `runs.ground_truth` column, and it never calls
any service's `/internal/fault` endpoint. Both of those are structural guarantees, not
just conventions: the client's only constructor argument is the ClickHouse HTTP URL.
"""

from __future__ import annotations

import json
import os

import httpx


class ClickHouseError(RuntimeError):
    pass


def sql_quote(value: str) -> str:
    """Escape a string for safe embedding in a single-quoted SQL literal.

    Tool arguments ultimately come from LLM-generated tool calls. Even though these
    tools are narrow and don't expose raw SQL execution to the agent, every string
    argument that gets interpolated into a query is escaped here as defense in depth
    against a prompt-injected agent trying to break out of its intended query shape.
    """
    return value.replace("\\", "\\\\").replace("'", "\\'")


class ClickHouseClient:
    def __init__(
        self,
        url: str | None = None,
        user: str | None = None,
        password: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.url = (url or os.environ.get("CLICKHOUSE_URL", "http://clickhouse:8123")).rstrip("/")
        self.user = user or os.environ.get("CLICKHOUSE_USER", "default")
        self.password = password if password is not None else os.environ.get("CLICKHOUSE_PASSWORD", "")
        self.timeout = timeout

    async def query_rows(self, sql: str) -> list[dict]:
        """Run a SQL statement and return rows as a list of dicts.

        Appends `FORMAT JSONEachRow` to whatever SQL is passed in, so callers should
        not include their own FORMAT clause or trailing semicolon.
        """
        body = sql.strip().rstrip(";") + "\nFORMAT JSONEachRow"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                self.url + "/",
                content=body.encode("utf-8"),
                auth=(self.user, self.password),
            )
        if resp.status_code != 200:
            raise ClickHouseError(f"ClickHouse query failed ({resp.status_code}): {resp.text[:500]}")
        text = resp.text.strip()
        if not text:
            return []
        rows = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
        return rows
