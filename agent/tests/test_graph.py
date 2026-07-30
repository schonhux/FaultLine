"""End-to-end test of the investigation graph's control flow -- routing, the ReAct
tool loop, and the tool-call budget cutoff -- using a scripted fake model and the
*real* telemetry MCP server (spawned as a real stdio subprocess, exercising the real
langchain_mcp_adapters <-> FastMCP round trip).

This deliberately only exercises list_runbooks/read_runbook tool calls, since those
are the only Class-0 tools that don't require a live ClickHouse -- everything else
in graph.py's wiring (context collection, message accumulation, routing, budget
enforcement, structured-output parsing) is fully exercised regardless of which real
tool gets called. Live verification against real ClickHouse data happens separately,
against the actual Docker Compose stack -- see the Layer 3 section of the README.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import pytest_asyncio
from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from graph import DiagnosisModel, HypothesesDraft, HypothesisModel, build_graph  # noqa: E402
from mcp_tools import build_mcp_client, load_tools, load_tools_session  # noqa: E402

HERE = os.path.dirname(__file__)
FIXTURES_RUNBOOKS = os.path.join(HERE, "fixtures", "runbooks")


class _StructuredBinding:
    """Stands in for `model.with_structured_output(Schema)`."""

    def __init__(self, canned):
        self._canned = canned

    async def ainvoke(self, messages):
        return self._canned


class _ToolsBinding:
    """Stands in for `model.bind_tools(tools)` -- returns a scripted sequence of
    AIMessages: N tool-call turns, then one plain text turn with no tool calls."""

    def __init__(self, script: list[AIMessage]):
        self._script = script
        self._i = 0

    async def ainvoke(self, messages):
        msg = self._script[min(self._i, len(self._script) - 1)]
        self._i += 1
        return msg


class FakeModel:
    """A minimal stand-in for BaseChatModel that only supports what graph.py calls:
    bind_tools, with_structured_output, and a plain ainvoke for the budget-exhausted
    branch. Not a real LangChain model -- a hand-rolled test double."""

    def __init__(self, tool_call_script: list[AIMessage], hypotheses: HypothesesDraft, diagnosis: DiagnosisModel):
        self._tool_call_script = tool_call_script
        self._hypotheses = hypotheses
        self._diagnosis = diagnosis
        self.plain_ainvoke_calls = 0

    def bind_tools(self, tools):
        return _ToolsBinding(self._tool_call_script)

    def with_structured_output(self, schema):
        if schema is HypothesesDraft:
            return _StructuredBinding(self._hypotheses)
        return _StructuredBinding(self._diagnosis)

    async def ainvoke(self, messages):
        self.plain_ainvoke_calls += 1
        return AIMessage(content="Budget exhausted, here's what I found so far.")


def _tool_call_message(name: str, args: dict, call_id: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


def _final_text_message() -> AIMessage:
    return AIMessage(content="I've gathered enough evidence.")


def _make_fake_clickhouse_handler():
    """A tiny stand-in ClickHouse HTTP endpoint, just real enough for
    context_collection's three ClickHouse-backed calls (get_recent_deployments,
    find_traces, list_metric_names) to succeed against a real socket, exercising the
    same real telemetry-server -> httpx -> ClickHouse HTTP path used in production
    instead of only the graceful-degradation path."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode()
            if "deployment_events" in body:
                rows = [
                    {
                        "service": "checkout",
                        "version": "v1.8.3-buggy",
                        "git_commit": "scenario-db-pool-exhaustion",
                        "deployed_at": "2026-07-29 23:00:00.000",
                        "config": "{}",
                    }
                ]
            elif "DISTINCT MetricName" in body:
                rows = [{"MetricName": "db.pool.active", "ServiceName": "checkout"}]
            else:
                rows = []
            text = "\n".join(json.dumps(r) for r in rows)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(text.encode())

    return Handler


@pytest.fixture(scope="module")
def fake_clickhouse_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _make_fake_clickhouse_handler())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest_asyncio.fixture
async def real_tools(fake_clickhouse_url):
    os.environ["TELEMETRY_SERVER_DIR"] = os.path.join(HERE, "..", "..", "mcp", "telemetry-server")
    os.environ["RUNBOOKS_DIR"] = FIXTURES_RUNBOOKS
    os.environ["CLICKHOUSE_URL"] = fake_clickhouse_url
    os.environ["CLICKHOUSE_USER"] = "default"
    os.environ["CLICKHOUSE_PASSWORD"] = "x"
    client = build_mcp_client()
    return await load_tools(client)


@pytest.mark.asyncio
async def test_graph_runs_one_tool_round_then_ranks(real_tools):
    hypotheses = HypothesesDraft(
        hypotheses=[
            HypothesisModel(statement="Consumer paused", affected_service="notifications", confidence=0.6),
            HypothesisModel(statement="Producer surge", affected_service="checkout", confidence=0.2),
        ]
    )
    diagnosis = DiagnosisModel(
        root_cause="consumer stopped polling",
        affected_service="notifications",
        triggering_change=None,
        confidence=0.8,
        evidence_summary="Runbook confirmed the pattern; lag climbed steadily.",
        hypotheses_considered=["Consumer paused", "Producer surge"],
    )
    script = [
        _tool_call_message("read_runbook", {"topic": "kafka lag"}, "call_1"),
        _final_text_message(),
    ]
    model = FakeModel(tool_call_script=script, hypotheses=hypotheses, diagnosis=diagnosis)

    graph = build_graph(model, real_tools, max_tool_calls=15)
    result = await graph.ainvoke(
        {
            "alert_name": "notifications-consumer-lag-rising",
            "alert_condition": "queue.consumer_lag rose by >= 5 since injection",
            "run_id": "test-run-1",
        }
    )

    assert result["diagnosis"]["root_cause"] == "consumer stopped polling"
    assert result["tool_call_count"] == 1
    assert len(result["hypotheses"]) == 2
    assert result["context"]["runbooks"], "context_collection should have listed runbooks"

    tool_messages = [m for m in result["messages"] if type(m).__name__ == "ToolMessage"]
    assert len(tool_messages) == 1
    assert "kafka-lag" in str(tool_messages[0].content)


@pytest.mark.asyncio
async def test_graph_respects_tool_call_budget(real_tools):
    hypotheses = HypothesesDraft(hypotheses=[HypothesisModel(statement="X", affected_service=None, confidence=0.5)])
    diagnosis = DiagnosisModel(
        root_cause="unknown",
        affected_service="catalog",
        triggering_change=None,
        confidence=0.3,
        evidence_summary="Ran out of budget before confirming.",
        hypotheses_considered=["X"],
    )
    # Script always wants to keep calling tools -- the graph must cut it off at the
    # budget rather than looping forever.
    script = [_tool_call_message("list_runbooks", {}, f"call_{i}") for i in range(50)]
    model = FakeModel(tool_call_script=script, hypotheses=hypotheses, diagnosis=diagnosis)

    graph = build_graph(model, real_tools, max_tool_calls=3)
    result = await graph.ainvoke(
        {"alert_name": "test-alert", "alert_condition": "test-condition", "run_id": None}
    )

    assert result["tool_call_count"] == 3
    assert model.plain_ainvoke_calls == 1, "should call the model exactly once in the budget-exhausted branch"
    assert result["diagnosis"]["root_cause"] == "unknown"


@pytest.mark.asyncio
async def test_load_tools_session_reuses_one_subprocess(fake_clickhouse_url):
    """main.py uses load_tools_session (not load_tools) for real runs specifically to
    avoid spawning a fresh telemetry-server subprocess per tool call. This exercises
    that exact code path: one session, multiple sequential tool calls through it."""
    os.environ["TELEMETRY_SERVER_DIR"] = os.path.join(HERE, "..", "..", "mcp", "telemetry-server")
    os.environ["RUNBOOKS_DIR"] = FIXTURES_RUNBOOKS
    os.environ["CLICKHOUSE_URL"] = fake_clickhouse_url
    os.environ["CLICKHOUSE_USER"] = "default"
    os.environ["CLICKHOUSE_PASSWORD"] = "x"

    client = build_mcp_client()
    async with load_tools_session(client) as tools:
        by_name = {t.name: t for t in tools}
        r1 = await by_name["list_runbooks"].ainvoke({})
        r2 = await by_name["read_runbook"].ainvoke({"topic": "kafka lag"})
        r3 = await by_name["list_metric_names"].ainvoke({"since_minutes": 10})

    assert "kafka-lag" in str(r1)
    assert "kafka-lag" in str(r2)
    assert "db.pool.active" in str(r3)
