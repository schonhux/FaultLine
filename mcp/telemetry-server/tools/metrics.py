"""query_metrics / list_metric_names -- Class 0 (harmless read) tools.

Backed by ClickHouse tables otel_metrics_gauge and otel_metrics_sum (the two metric
types ShopGrid actually emits today; histogram/summary/exp_histogram are unused by any
current scenario and are intentionally out of scope for v1 -- see the comment on
_METRIC_TABLES below).
"""

from __future__ import annotations

import re

from clickhouse_client import ClickHouseClient, sql_quote
from tools.common import ToolInputError, service_filter_clause, validate_minutes, validate_service

# Every metric ShopGrid emits today is a gauge or a (monotonic) sum; nothing uses
# histogram/summary/exponential_histogram yet, so those tables are left out of the
# UNION ALL to keep the query cheap. Add them here if a future scenario needs one.
_METRIC_TABLES = ("otel_metrics_gauge", "otel_metrics_sum")

_METRIC_NAME_RE = re.compile(r"^[A-Za-z0-9_.]{1,128}$")

_AGGREGATIONS = {
    "last": "anyLast(Value)",
    "avg": "avg(Value)",
    "max": "max(Value)",
    "min": "min(Value)",
    "sum": "sum(Value)",
}


def _validate_metric_name(metric_name: str) -> str:
    if not _METRIC_NAME_RE.match(metric_name):
        raise ToolInputError(
            "metric_name must look like 'db.pool.active' (letters, digits, '.', '_' only); "
            "call list_metric_names() if unsure what's available"
        )
    return metric_name


async def list_metric_names(ch: ClickHouseClient, since_minutes: int = 60) -> dict:
    """Return every distinct MetricName currently being reported, across all services.

    Useful as a first investigative step: rather than guessing metric names, discover
    what's actually being emitted right now.
    """
    since_minutes = validate_minutes(since_minutes)
    union = " UNION ALL ".join(
        f"SELECT DISTINCT MetricName, ServiceName FROM otel.{table} "
        f"WHERE TimeUnix > now() - INTERVAL {since_minutes} MINUTE"
        for table in _METRIC_TABLES
    )
    rows = await ch.query_rows(f"SELECT DISTINCT MetricName, ServiceName FROM ({union})")
    return {"since_minutes": since_minutes, "metrics": rows}


async def query_metrics(
    ch: ClickHouseClient,
    metric_name: str,
    service: str | None = None,
    since_minutes: int = 15,
    aggregation: str = "last",
    bucket_seconds: int = 0,
) -> dict:
    """Read a metric's value over a recent time window.

    If bucket_seconds is 0 (default), returns one aggregated value for the whole
    window. If bucket_seconds > 0, returns a time-bucketed series instead (e.g.
    bucket_seconds=10 to see a gauge climb over 10-second steps) -- aggregation still
    controls how each bucket is reduced. aggregation must be one of: last, avg, max,
    min, sum.
    """
    metric_name = _validate_metric_name(metric_name)
    service = validate_service(service)
    since_minutes = validate_minutes(since_minutes)
    if aggregation not in _AGGREGATIONS:
        raise ToolInputError(f"aggregation must be one of {', '.join(_AGGREGATIONS)}")

    svc_clause = service_filter_clause(service)
    union = " UNION ALL ".join(
        f"SELECT TimeUnix, Value, ServiceName FROM otel.{table} "
        f"WHERE MetricName = '{sql_quote(metric_name)}' "
        f"AND TimeUnix > now() - INTERVAL {since_minutes} MINUTE {svc_clause}"
        for table in _METRIC_TABLES
    )

    if bucket_seconds <= 0:
        agg_expr = _AGGREGATIONS[aggregation]
        sql = f"SELECT {agg_expr} AS value FROM ({union})"
        rows = await ch.query_rows(sql)
        value = rows[0]["value"] if rows and rows[0].get("value") is not None else None
        return {
            "metric_name": metric_name,
            "service": service,
            "since_minutes": since_minutes,
            "aggregation": aggregation,
            "value": value,
        }

    bucket_seconds = max(1, min(bucket_seconds, 3600))
    agg_expr = _AGGREGATIONS[aggregation]
    sql = (
        f"SELECT toStartOfInterval(TimeUnix, INTERVAL {bucket_seconds} SECOND) AS bucket, "
        f"{agg_expr} AS value FROM ({union}) "
        f"GROUP BY bucket ORDER BY bucket ASC LIMIT 500"
    )
    rows = await ch.query_rows(sql)
    return {
        "metric_name": metric_name,
        "service": service,
        "since_minutes": since_minutes,
        "aggregation": aggregation,
        "bucket_seconds": bucket_seconds,
        "series": rows,
    }
