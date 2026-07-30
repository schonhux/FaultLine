"""Tests for main.py's streaming helper (_stream_graph), which replaced a plain
graph.ainvoke() call so the console can relay progress over SSE. The one thing
that can't change is the evaluation harness's contract: the final stdout line
is still a bare `{"root_cause": ..., ...}` diagnosis. These tests check that
_stream_graph returns the same final state ainvoke() would, that events come
back in the graph's actual node order, and that no progress event ever carries
a top-level "root_cause" key that could be mistaken for the real diagnosis.

Self-contained (duplicates the small fake-model/fake-ClickHouse harness from
test_graph.py rather than importing across test files, since there's no
conftest.py here and cross-file imports would depend on how pytest was
invoked).
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
from langchain_core.tools import StructuredTool

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from graph import DiagnosisModel, HypothesesDraft, HypothesisModel, build_graph  # noqa: E402
from main import _stream_graph  # noqa: E402
from mcp_tools import build_mcp_client, load_tools  # noqa: E402

HERE = os.path.dirname(__file__)
FIXTURES_RUNBOOKS = os.path.join(HERE, "fixtures", "runbooks")

_REMEDIATION_TOOL_NAMES = {"propose_restart_service", "propose_rollback_deployment", "execute_remediation"}


class _StructuredBinding:
    def __init__(self, canned):
        self._canned = canned

    async def ainvoke(self, messages):
        return self._canned


class _ToolsBinding:
    def __init__(self, script: list[AIMessage]):
        self._script = script
        self._i = 0

    async def ainvoke(self, messages):
        msg = self._script[min(self._i, len(self._script) - 1)]
        self._i += 1
        return msg


class FakeModel:
    """Same minimal BaseChatModel stand-in as test_graph.py's -- see that file for
    the full rationale. Duplicated here to keep this file independently runnable."""

    def __init__(self, tool_call_script, hypotheses, diagnosis):
        self._tool_call_script = tool_call_script
        self._hypotheses = hypotheses
        self._diagnosis = diagnosis

    def bind_tools(self, tools):
        names = {t.name for t in tools}
        if names & _REMEDIATION_TOOL_NAMES:
            raise AssertionError("this test never enables remediation")
        return _ToolsBinding(self._tool_call_script)

    def with_structured_output(self, schema):
        if schema is HypothesesDraft:
            return _StructuredBinding(self._hypotheses)
        return _StructuredBinding(self._diagnosis)

    async def ainvoke(self, messages):
        return AIMessage(content="Budget exhausted, here's what I found so far.")


def _tool_call_message(name: str, args: dict, call_id: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


def _final_text_message() -> AIMessage:
    return AIMessage(content="I've gathered enough evidence.")


def _make_fake_clickhouse_handler():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"")

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


def _make_model_and_script():
    hypotheses = HypothesesDraft(
        hypotheses=[HypothesisModel(statement="Consumer paused", affected_service="notifications", confidence=0.6)]
    )
    diagnosis = DiagnosisModel(
        root_cause="consumer stopped polling",
        affected_service="notifications",
        triggering_change=None,
        confidence=0.8,
        evidence_summary="Runbook confirmed the pattern.",
        hypotheses_considered=["Consumer paused"],
    )
    script = [_tool_call_message("read_runbook", {"topic": "kafka lag"}, "call_1"), _final_text_message()]
    return FakeModel(tool_call_script=script, hypotheses=hypotheses, diagnosis=diagnosis)


_INITIAL_STATE = {
    "alert_name": "notifications-consumer-lag-rising",
    "alert_condition": "queue.consumer_lag rose by >= 5 since injection",
    "run_id": "test-run-stream",
}


@pytest.mark.asyncio
async def test_stream_graph_matches_ainvoke_result(real_tools):
    model_for_invoke = _make_model_and_script()
    model_for_stream = _make_model_and_script()

    invoked = await build_graph(model_for_invoke, real_tools, max_tool_calls=15).ainvoke(_INITIAL_STATE)
    streamed = await _stream_graph(
        build_graph(model_for_stream, real_tools, max_tool_calls=15), _INITIAL_STATE
    )

    assert streamed["diagnosis"] == invoked["diagnosis"]
    assert streamed["tool_call_count"] == invoked["tool_call_count"] == 1
    assert len(streamed["hypotheses"]) == len(invoked["hypotheses"]) == 1
    assert len(streamed["messages"]) == len(invoked["messages"])


@pytest.mark.asyncio
async def test_stream_graph_emits_one_event_per_node_in_order(monkeypatch, real_tools):
    model = _make_model_and_script()
    emitted: list[dict] = []
    monkeypatch.setattr("main._emit", lambda event: emitted.append(event))

    graph = build_graph(model, real_tools, max_tool_calls=15)
    await _stream_graph(graph, _INITIAL_STATE)

    node_order = [e["node"] for e in emitted]
    # context_collection -> hypothesize -> investigate -> tools -> investigate -> rank,
    # matching the one-tool-call-then-final-answer script exactly.
    assert node_order == [
        "context_collection",
        "hypothesize",
        "investigate",
        "tools",
        "investigate",
        "rank",
    ]
    assert all(e["event"] == "node_complete" for e in emitted)
    assert all("root_cause" not in e for e in emitted)

    rank_event = emitted[-1]
    assert rank_event["summary"]["root_cause_preview"] == "consumer stopped polling"
    assert rank_event["summary"]["affected_service"] == "notifications"


def _make_fake_remediation_tools(propose_result: dict, execute_result: dict):
    """Duplicated from test_graph.py's helper of the same name -- see there for the
    full rationale (JSON *strings*, matching how a real MCP tool result actually
    arrives through langchain_mcp_adapters)."""

    def _propose_restart_service(target: str, justification: str, run_id: str | None = None) -> str:
        """Fake stand-in for the real propose_restart_service MCP tool."""
        return json.dumps(propose_result)

    def _propose_rollback_deployment(target: str, justification: str, run_id: str | None = None) -> str:
        """Fake stand-in for the real propose_rollback_deployment MCP tool."""
        return json.dumps(propose_result)

    def _execute_remediation(approval_id: str, timeout_seconds: float = 90.0) -> str:
        """Fake stand-in for the real execute_remediation MCP tool."""
        return json.dumps(execute_result)

    return [
        StructuredTool.from_function(func=_propose_restart_service, name="propose_restart_service"),
        StructuredTool.from_function(func=_propose_rollback_deployment, name="propose_rollback_deployment"),
        StructuredTool.from_function(func=_execute_remediation, name="execute_remediation"),
    ]


@pytest.mark.asyncio
async def test_stream_graph_surfaces_approval_id_the_moment_its_proposed(monkeypatch, real_tools):
    """The console's live view has to show an Approve/Deny control as soon as a
    remediation is proposed -- not just once execute_remediation finally returns
    (which can take up to 90s waiting on a human). Proves the remediation_tools
    node's progress event carries the propose_* tool's raw result, approval_id
    included, exactly as mcp/remediation-server/tools/remediation.py returns it."""
    hypotheses = HypothesesDraft(
        hypotheses=[HypothesisModel(statement="X", affected_service="checkout", confidence=0.9)]
    )
    diagnosis = DiagnosisModel(
        root_cause="connection leak",
        affected_service="checkout",
        triggering_change="v1.8.3-buggy",
        confidence=0.95,
        evidence_summary="Pool pinned at max right after deployment.",
        hypotheses_considered=["X"],
    )
    remediation_script = [
        _tool_call_message(
            "propose_restart_service",
            {"target": "checkout", "justification": "pool pinned at 20/20 since deploy", "run_id": "run-1"},
            "rem_call_1",
        ),
        _final_text_message(),
    ]

    class RemediationFakeModel(FakeModel):
        def __init__(self):
            super().__init__(
                tool_call_script=[_final_text_message()], hypotheses=hypotheses, diagnosis=diagnosis
            )
            self._remediation_script = remediation_script

        def bind_tools(self, tools):
            names = {t.name for t in tools}
            if names & _REMEDIATION_TOOL_NAMES:
                return _ToolsBinding(self._remediation_script)
            return _ToolsBinding(self._tool_call_script)

    remediation_tools = _make_fake_remediation_tools(
        propose_result={"status": "pending_approval", "approval_id": "approval-abc", "risk_class": 1},
        execute_result={"status": "executed", "result": "restarted faultline-checkout-1"},
    )

    emitted: list[dict] = []
    monkeypatch.setattr("main._emit", lambda event: emitted.append(event))

    graph = build_graph(
        RemediationFakeModel(), real_tools, max_tool_calls=15, remediation_tools=remediation_tools
    )
    await _stream_graph(
        graph, {"alert_name": "checkout-pool-exhausted", "alert_condition": "db.pool.active >= 18", "run_id": "run-1"}
    )

    remediation_tool_events = [e for e in emitted if e["node"] == "remediation_tools"]
    assert len(remediation_tool_events) == 1
    last_result = remediation_tool_events[0]["summary"]["last_tool_result"]
    assert last_result["approval_id"] == "approval-abc"
    assert last_result["status"] == "pending_approval"

    # The proposal's target/justification only ever appear in the *remediate*
    # node's own tool_calls (the propose_* result itself never echoes them back) --
    # the console needs both this event and the remediation_tools one above to
    # reconstruct a full proposal to show a human.
    remediate_events = [e for e in emitted if e["node"] == "remediate"]
    propose_calls = [
        c for e in remediate_events for c in e["summary"].get("tool_calls", []) if c["name"] == "propose_restart_service"
    ]
    assert len(propose_calls) == 1
    assert propose_calls[0]["args"]["target"] == "checkout"
    assert propose_calls[0]["args"]["justification"] == "pool pinned at 20/20 since deploy"
