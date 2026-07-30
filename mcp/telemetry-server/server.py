"""FaultLine telemetry MCP server -- the agent's entire view of the world.

Exposes exactly the Class-0 ("harmless read") tools defined in the project's safety
model: query_metrics, search_logs, get_trace (+ find_traces), get_recent_deployments,
read_runbook (+ list_runbooks). Every tool is backed by a narrow, parameterized query
against ClickHouse's telemetry tables or the local runbooks/ directory.

Structural safety properties (not just conventions):
  - No Postgres connection exists anywhere in this process, so there is no code path
    that could read the `runs.ground_truth` column.
  - No tool calls any service's `/internal/fault` endpoint, so this process cannot
    read or change fault state.
  - No tool accepts raw SQL; every query shape is fixed in the tools/ package and only
    validated, escaped arguments are interpolated into it.

Run with: python server.py   (stdio transport, for a parent process such as the
LangGraph agent to spawn and talk to over stdin/stdout).

Deliberately does NOT use `from __future__ import annotations`: this mcp SDK version's
tool registration (mcp/server/fastmcp/tools/base.py) inspects each parameter's live
annotation object via plain inspect.signature() (no eval_str), so a lazily-stringified
annotation breaks its Context-parameter detection. Every tool function's type hints
here must stay real objects at import time, not strings.
"""

from mcp.server.fastmcp import FastMCP

from clickhouse_client import ClickHouseClient
from tools.common import ToolInputError
from tools.deployments import get_recent_deployments as _get_recent_deployments
from tools.logs import search_logs as _search_logs
from tools.metrics import list_metric_names as _list_metric_names
from tools.metrics import query_metrics as _query_metrics
from tools.runbooks import list_runbooks as _list_runbooks
from tools.runbooks import read_runbook as _read_runbook
from tools.traces import find_traces as _find_traces
from tools.traces import get_trace as _get_trace

mcp = FastMCP(
    "faultline-telemetry",
    instructions=(
        "Read-only telemetry access for investigating a FaultLine incident. You cannot "
        "see fault configuration, scenario definitions, or ground truth through these "
        "tools -- diagnose from metrics, logs, traces, deployments, and runbooks only, "
        "the same way an on-call engineer would."
    ),
)

_ch = ClickHouseClient()


@mcp.tool()
async def list_metric_names(since_minutes: int = 60) -> dict:
    """List every metric name currently being reported, across all services."""
    try:
        return await _list_metric_names(_ch, since_minutes=since_minutes)
    except ToolInputError as e:
        return {"error": str(e)}


@mcp.tool()
async def query_metrics(
    metric_name: str,
    service: str | None = None,
    since_minutes: int = 15,
    aggregation: str = "last",
    bucket_seconds: int = 0,
) -> dict:
    """Read a metric's value over a recent time window (single value, or a bucketed
    series if bucket_seconds > 0). aggregation: last|avg|max|min|sum."""
    try:
        return await _query_metrics(
            _ch,
            metric_name=metric_name,
            service=service,
            since_minutes=since_minutes,
            aggregation=aggregation,
            bucket_seconds=bucket_seconds,
        )
    except ToolInputError as e:
        return {"error": str(e)}


@mcp.tool()
async def search_logs(
    service: str | None = None,
    contains: str | None = None,
    severity: str | None = None,
    since_minutes: int = 15,
    limit: int = 50,
) -> dict:
    """Search recent log lines by service, substring, and/or severity."""
    try:
        return await _search_logs(
            _ch,
            service=service,
            contains=contains,
            severity=severity,
            since_minutes=since_minutes,
            limit=limit,
        )
    except ToolInputError as e:
        return {"error": str(e)}


@mcp.tool()
async def find_traces(
    service: str | None = None,
    span_name: str | None = None,
    min_duration_ms: float | None = None,
    status: str | None = None,
    since_minutes: int = 15,
    limit: int = 20,
) -> dict:
    """Find candidate traces (root spans), slowest first, matching filters."""
    try:
        return await _find_traces(
            _ch,
            service=service,
            span_name=span_name,
            min_duration_ms=min_duration_ms,
            status=status,
            since_minutes=since_minutes,
            limit=limit,
        )
    except ToolInputError as e:
        return {"error": str(e)}


@mcp.tool()
async def get_trace(trace_id: str) -> dict:
    """Get the full span waterfall for one trace_id (call find_traces first to get one)."""
    try:
        return await _get_trace(_ch, trace_id=trace_id)
    except ToolInputError as e:
        return {"error": str(e)}


@mcp.tool()
async def get_recent_deployments(
    service: str | None = None,
    since_minutes: int | None = None,
    limit: int = 20,
) -> dict:
    """List recent deployment events: service, version, git_commit, deployed_at, config."""
    try:
        return await _get_recent_deployments(_ch, service=service, since_minutes=since_minutes, limit=limit)
    except ToolInputError as e:
        return {"error": str(e)}


@mcp.tool()
async def list_runbooks() -> dict:
    """List every runbook available to read_runbook."""
    try:
        return await _list_runbooks()
    except ToolInputError as e:
        return {"error": str(e)}


@mcp.tool()
async def read_runbook(topic: str) -> dict:
    """Read one runbook's full content by topic, e.g. 'db-pool-exhaustion'."""
    try:
        return await _read_runbook(topic)
    except ToolInputError as e:
        return {"error": str(e)}


if __name__ == "__main__":
    mcp.run(transport="stdio")
