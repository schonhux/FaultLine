"""find_traces / get_trace -- Class 0 (harmless read) tools, backed by otel.otel_traces.

find_traces discovers candidate traces (root spans) matching filters -- e.g. "show me
the slowest requests to catalog in the last 10 minutes". get_trace then pulls the full
span waterfall for one trace_id so the agent can see where time went or where an error
originated.
"""

from __future__ import annotations

from clickhouse_client import ClickHouseClient, sql_quote
from tools.common import ToolInputError, service_filter_clause, validate_limit, validate_minutes, validate_service

_STATUS_MAP = {"ok": "Ok", "error": "Error", "unset": "Unset"}

# Attributes worth surfacing by default on every span -- present or not, ClickHouse's
# Map access returns '' rather than erroring, so this is safe even for spans that don't
# set a given key.
_DEFAULT_ATTRS = ("http.status_code", "http.method", "http.route")


async def find_traces(
    ch: ClickHouseClient,
    service: str | None = None,
    span_name: str | None = None,
    min_duration_ms: float | None = None,
    status: str | None = None,
    since_minutes: int = 15,
    limit: int = 20,
) -> dict:
    """Find root spans (i.e. one row per trace) matching filters, slowest first.

    status, if given, must be "ok" or "error".
    """
    service = validate_service(service)
    since_minutes = validate_minutes(since_minutes)
    limit = validate_limit(limit, max_limit=100)

    clauses = [
        "ParentSpanId = ''",
        f"Timestamp > now() - INTERVAL {since_minutes} MINUTE",
    ]
    if service is not None:
        clauses.append(f"ServiceName = '{sql_quote(service)}'")
    if span_name is not None:
        clauses.append(f"SpanName = '{sql_quote(span_name)}'")
    if min_duration_ms is not None:
        if min_duration_ms < 0:
            raise ToolInputError("min_duration_ms must be >= 0")
        clauses.append(f"Duration >= {min_duration_ms * 1e6}")
    if status is not None:
        status_key = status.lower()
        if status_key not in _STATUS_MAP:
            raise ToolInputError("status must be 'ok', 'error', or 'unset'")
        clauses.append(f"StatusCode = '{_STATUS_MAP[status_key]}'")

    where = " AND ".join(clauses)
    attr_cols = ", ".join(f"SpanAttributes['{a}'] AS `{a}`" for a in _DEFAULT_ATTRS)
    sql = (
        f"SELECT TraceId, SpanId, ServiceName, SpanName, Timestamp, "
        f"Duration / 1e6 AS duration_ms, StatusCode, {attr_cols} "
        f"FROM otel.otel_traces WHERE {where} "
        f"ORDER BY Duration DESC LIMIT {limit}"
    )
    rows = await ch.query_rows(sql)
    return {
        "service": service,
        "span_name": span_name,
        "min_duration_ms": min_duration_ms,
        "status": status,
        "since_minutes": since_minutes,
        "count": len(rows),
        "traces": rows,
    }


async def get_trace(ch: ClickHouseClient, trace_id: str) -> dict:
    """Return every span belonging to one trace_id, ordered to form a waterfall.

    Use find_traces first to discover a trace_id, then call this to see exactly which
    service/span the time (or error) came from.
    """
    if not trace_id or len(trace_id) > 64:
        raise ToolInputError("trace_id looks invalid")

    attr_cols = ", ".join(f"SpanAttributes['{a}'] AS `{a}`" for a in _DEFAULT_ATTRS)
    sql = (
        f"SELECT SpanId, ParentSpanId, ServiceName, SpanName, Timestamp, "
        f"Duration / 1e6 AS duration_ms, StatusCode, StatusMessage, {attr_cols} "
        f"FROM otel.otel_traces WHERE TraceId = '{sql_quote(trace_id)}' "
        f"ORDER BY Timestamp ASC LIMIT 200"
    )
    rows = await ch.query_rows(sql)
    if not rows:
        raise ToolInputError(f"no spans found for trace_id {trace_id!r}")
    return {"trace_id": trace_id, "span_count": len(rows), "spans": rows}
