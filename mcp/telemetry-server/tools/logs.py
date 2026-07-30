"""search_logs -- Class 0 (harmless read) tool, backed by otel.otel_logs."""

from __future__ import annotations

from clickhouse_client import ClickHouseClient, sql_quote
from tools.common import ToolInputError, service_filter_clause, validate_limit, validate_minutes, validate_service

_SEVERITIES = {"TRACE", "DEBUG", "INFO", "WARN", "ERROR", "FATAL"}


async def search_logs(
    ch: ClickHouseClient,
    service: str | None = None,
    contains: str | None = None,
    severity: str | None = None,
    since_minutes: int = 15,
    limit: int = 50,
) -> dict:
    """Search recent log lines.

    service: restrict to one ShopGrid service (gateway/checkout/catalog/notifications).
    contains: case-sensitive substring match against the log body, e.g. "invalid internal token".
    severity: one of TRACE/DEBUG/INFO/WARN/ERROR/FATAL.
    """
    service = validate_service(service)
    since_minutes = validate_minutes(since_minutes)
    limit = validate_limit(limit, max_limit=200)

    if severity is not None:
        severity = severity.upper()
        if severity not in _SEVERITIES:
            raise ToolInputError(f"severity must be one of {', '.join(sorted(_SEVERITIES))}")

    clauses = [f"Timestamp > now() - INTERVAL {since_minutes} MINUTE"]
    if service is not None:
        clauses.append(f"ServiceName = '{sql_quote(service)}'")
    if severity is not None:
        clauses.append(f"SeverityText = '{sql_quote(severity)}'")
    if contains is not None:
        if len(contains) > 200:
            raise ToolInputError("contains must be 200 characters or fewer")
        clauses.append(f"Body LIKE '%{sql_quote(contains)}%'")

    where = " AND ".join(clauses)
    sql = (
        "SELECT Timestamp, ServiceName, SeverityText, Body FROM otel.otel_logs "
        f"WHERE {where} ORDER BY Timestamp DESC LIMIT {limit}"
    )
    rows = await ch.query_rows(sql)
    return {
        "service": service,
        "contains": contains,
        "severity": severity,
        "since_minutes": since_minutes,
        "count": len(rows),
        "logs": rows,
    }
