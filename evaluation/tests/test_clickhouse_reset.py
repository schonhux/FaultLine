"""Unit tests for clickhouse_reset.py against a mocked ClickHouse HTTP endpoint."""

from __future__ import annotations

import os
import sys

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from clickhouse_reset import TABLES, reset_telemetry  # noqa: E402


def test_reset_telemetry_truncates_every_table():
    truncated = []

    def handler(request: httpx.Request) -> httpx.Response:
        truncated.append(request.content.decode())
        return httpx.Response(200, text="")

    transport = httpx.MockTransport(handler)
    original_client = httpx.Client

    def patched_client(*args, **kwargs):
        kwargs["transport"] = transport
        return original_client(*args, **kwargs)

    import clickhouse_reset

    clickhouse_reset.httpx.Client = patched_client
    try:
        reset_telemetry(url="http://fake-clickhouse:8123")
    finally:
        clickhouse_reset.httpx.Client = original_client

    assert len(truncated) == len(TABLES)
    for table in TABLES:
        assert any(f"TRUNCATE TABLE IF EXISTS {table}" in body for body in truncated)


def test_reset_telemetry_tolerates_a_failing_table(capsys):
    def handler(request: httpx.Request) -> httpx.Response:
        if "deployment_events" in request.content.decode():
            return httpx.Response(500, text="boom")
        return httpx.Response(200, text="")

    transport = httpx.MockTransport(handler)
    original_client = httpx.Client

    def patched_client(*args, **kwargs):
        kwargs["transport"] = transport
        return original_client(*args, **kwargs)

    import clickhouse_reset

    clickhouse_reset.httpx.Client = patched_client
    try:
        reset_telemetry(url="http://fake-clickhouse:8123")  # should not raise
    finally:
        clickhouse_reset.httpx.Client = original_client

    captured = capsys.readouterr()
    assert "deployment_events" in captured.out
