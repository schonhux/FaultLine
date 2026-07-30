"""Unit tests for the telemetry MCP tools against a mocked ClickHouse HTTP interface.

These do not require a running ClickHouse/Docker Compose stack -- they verify SQL
shape, response parsing, and input validation using httpx.MockTransport. Run with:

    cd mcp/telemetry-server
    pip install -r requirements.txt
    pip install pytest pytest-asyncio
    pytest tests/ -v

Live verification against real ClickHouse (with actual scenario data) is a separate,
manual step against the running Docker Compose stack.
"""

from __future__ import annotations

import json
import os
import sys

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from clickhouse_client import ClickHouseClient  # noqa: E402
from tools.common import ToolInputError  # noqa: E402
from tools.deployments import get_recent_deployments  # noqa: E402
from tools.logs import search_logs  # noqa: E402
from tools.metrics import list_metric_names, query_metrics  # noqa: E402
from tools.runbooks import list_runbooks, read_runbook  # noqa: E402
from tools.traces import find_traces, get_trace  # noqa: E402


def _ndjson(*rows: dict) -> str:
    return "\n".join(json.dumps(r) for r in rows)


def fake_clickhouse_handler(request: httpx.Request) -> httpx.Response:
    body = request.content.decode()
    if "otel_metrics_gauge" in body or "otel_metrics_sum" in body:
        if "toStartOfInterval" in body:
            text = _ndjson({"bucket": "2026-07-29 23:00:00", "value": 10.0}, {"bucket": "2026-07-29 23:00:10", "value": 18.0})
        elif "DISTINCT MetricName" in body:
            text = _ndjson({"MetricName": "db.pool.active", "ServiceName": "checkout"})
        else:
            text = json.dumps({"value": 18.0})
    elif "otel_logs" in body:
        text = json.dumps(
            {"Timestamp": "2026-07-29 23:00:00.000", "ServiceName": "catalog", "SeverityText": "ERROR", "Body": "invalid internal token"}
        )
    elif "otel_traces" in body and "TraceId = " in body:
        text = _ndjson(
            {"SpanId": "s1", "ParentSpanId": "", "ServiceName": "gateway", "SpanName": "http.request", "duration_ms": 12.3, "StatusCode": "Ok"},
            {"SpanId": "s2", "ParentSpanId": "s1", "ServiceName": "catalog", "SpanName": "redis.get", "duration_ms": 800.1, "StatusCode": "Ok"},
        )
    elif "otel_traces" in body:
        text = json.dumps(
            {"TraceId": "t1", "SpanId": "s1", "ServiceName": "catalog", "SpanName": "http.request", "duration_ms": 801.9, "StatusCode": "Ok"}
        )
    elif "deployment_events" in body:
        text = json.dumps(
            {"service": "checkout", "version": "v1.8.3-buggy", "git_commit": "scenario-db-pool-exhaustion", "deployed_at": "2026-07-29 23:00:00.000", "config": "{}"}
        )
    else:
        text = ""
    return httpx.Response(200, text=text)


@pytest.fixture
def ch():
    client = ClickHouseClient(url="http://fake-clickhouse:8123")
    transport = httpx.MockTransport(fake_clickhouse_handler)

    async def query_rows(sql: str) -> list[dict]:
        body = sql.strip().rstrip(";") + "\nFORMAT JSONEachRow"
        async with httpx.AsyncClient(transport=transport) as http_client:
            resp = await http_client.post(client.url + "/", content=body.encode(), auth=(client.user, client.password))
        text = resp.text.strip()
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    client.query_rows = query_rows
    return client


@pytest.mark.asyncio
async def test_query_metrics_scalar(ch):
    r = await query_metrics(ch, "db.pool.active", service="checkout", aggregation="last")
    assert r["value"] == 18.0


@pytest.mark.asyncio
async def test_query_metrics_series(ch):
    r = await query_metrics(ch, "db.pool.active", bucket_seconds=10)
    assert [p["value"] for p in r["series"]] == [10.0, 18.0]


@pytest.mark.asyncio
async def test_query_metrics_rejects_bad_name(ch):
    with pytest.raises(ToolInputError):
        await query_metrics(ch, "bad metric name!!")


@pytest.mark.asyncio
async def test_list_metric_names(ch):
    r = await list_metric_names(ch)
    assert r["metrics"][0]["MetricName"] == "db.pool.active"


@pytest.mark.asyncio
async def test_search_logs(ch):
    r = await search_logs(ch, service="catalog", contains="invalid internal token", severity="error")
    assert r["logs"][0]["Body"] == "invalid internal token"


@pytest.mark.asyncio
async def test_search_logs_rejects_unknown_service(ch):
    with pytest.raises(ToolInputError):
        await search_logs(ch, service="not-a-real-service")


@pytest.mark.asyncio
async def test_find_traces_and_get_trace(ch):
    found = await find_traces(ch, service="catalog", min_duration_ms=100)
    assert found["traces"][0]["TraceId"] == "t1"

    detail = await get_trace(ch, "t1")
    assert detail["span_count"] == 2
    assert detail["spans"][0]["ServiceName"] == "gateway"


@pytest.mark.asyncio
async def test_get_trace_rejects_empty_id(ch):
    with pytest.raises(ToolInputError):
        await get_trace(ch, "")


@pytest.mark.asyncio
async def test_get_recent_deployments(ch):
    r = await get_recent_deployments(ch, service="checkout")
    assert r["deployments"][0]["version"] == "v1.8.3-buggy"


@pytest.mark.asyncio
async def test_runbooks(tmp_path):
    (tmp_path / "db-pool-exhaustion.md").write_text("# DB Pool Exhaustion Runbook\n\nSome content.\n")

    listing = await list_runbooks(runbooks_dir=str(tmp_path))
    assert listing["runbooks"] == [{"topic": "db-pool-exhaustion", "title": "DB Pool Exhaustion Runbook"}]

    # fuzzy match: spaces/case/hyphen-insensitive
    r = await read_runbook("DB Pool Exhaustion", runbooks_dir=str(tmp_path))
    assert "Some content." in r["content"]


@pytest.mark.asyncio
async def test_read_runbook_unknown_topic_lists_available(tmp_path):
    (tmp_path / "redis-latency.md").write_text("# Redis Latency\n")
    with pytest.raises(ToolInputError, match="redis-latency"):
        await read_runbook("nonexistent-topic", runbooks_dir=str(tmp_path))
