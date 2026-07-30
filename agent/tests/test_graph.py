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
from langchain_core.tools import BaseTool, StructuredTool

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


_REMEDIATION_TOOL_NAMES = {"propose_restart_service", "propose_rollback_deployment", "execute_remediation"}


class FakeModel:
    """A minimal stand-in for BaseChatModel that only supports what graph.py calls:
    bind_tools, with_structured_output, and a plain ainvoke for the budget-exhausted
    branch. Not a real LangChain model -- a hand-rolled test double.

    bind_tools is called twice in a Layer 5 run (once for investigation tools, once
    for remediation tools) -- this distinguishes the two calls by tool name so each
    gets its own scripted response sequence and its own position counter.
    """

    def __init__(
        self,
        tool_call_script: list[AIMessage],
        hypotheses: HypothesesDraft,
        diagnosis: DiagnosisModel,
        remediation_tool_call_script: list[AIMessage] | None = None,
    ):
        self._tool_call_script = tool_call_script
        self._hypotheses = hypotheses
        self._diagnosis = diagnosis
        self._remediation_tool_call_script = remediation_tool_call_script
        self.plain_ainvoke_calls = 0

    def bind_tools(self, tools):
        names = {t.name for t in tools}
        if names & _REMEDIATION_TOOL_NAMES:
            return _ToolsBinding(self._remediation_tool_call_script)
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


def _make_fake_remediation_tools(propose_result: dict, execute_result: dict) -> list[BaseTool]:
    """Stand-ins for the real remediation-server tools (mcp/remediation-server/), so
    the graph's remediate <-> remediation_tools wiring can be exercised without a real
    Postgres, Docker, or the remediation container. Returns JSON *strings*, matching
    how a real MCP tool result actually arrives through langchain_mcp_adapters (a text
    content block) -- this is what _parse_tool_json is written to unwrap."""

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
async def test_remediate_phase_proposes_and_executes_an_action(real_tools):
    hypotheses = HypothesesDraft(hypotheses=[HypothesisModel(statement="X", affected_service="checkout", confidence=0.9)])
    diagnosis = DiagnosisModel(
        root_cause="connection leak",
        affected_service="checkout",
        triggering_change="v1.8.3-buggy",
        confidence=0.95,
        evidence_summary="Pool pinned at max right after deployment.",
        hypotheses_considered=["X"],
    )
    investigate_script = [_final_text_message()]
    remediation_script = [
        _tool_call_message(
            "propose_restart_service",
            {"target": "checkout", "justification": "pool pinned at 20/20 since deploy", "run_id": "run-1"},
            "rem_call_1",
        ),
        _tool_call_message("execute_remediation", {"approval_id": "approval-abc"}, "rem_call_2"),
        _final_text_message(),
    ]
    model = FakeModel(
        tool_call_script=investigate_script,
        hypotheses=hypotheses,
        diagnosis=diagnosis,
        remediation_tool_call_script=remediation_script,
    )
    remediation_tools = _make_fake_remediation_tools(
        propose_result={"status": "pending_approval", "approval_id": "approval-abc", "risk_class": 1},
        execute_result={"status": "executed", "result": "restarted faultline-checkout-1"},
    )

    graph = build_graph(model, real_tools, max_tool_calls=15, remediation_tools=remediation_tools)
    result = await graph.ainvoke(
        {"alert_name": "checkout-pool-exhausted", "alert_condition": "db.pool.active >= 18", "run_id": "run-1"}
    )

    assert result["diagnosis"]["root_cause"] == "connection leak"
    assert result["remediation"]["proposed"] is True
    assert result["remediation"]["tool"] == "propose_restart_service"
    assert result["remediation"]["target"] == "checkout"
    assert result["remediation"]["status"] == "executed"
    assert result["remediation_tool_call_count"] == 2


@pytest.mark.asyncio
async def test_remediate_phase_records_no_action_when_model_proposes_nothing(real_tools):
    hypotheses = HypothesesDraft(hypotheses=[HypothesisModel(statement="X", affected_service=None, confidence=0.4)])
    diagnosis = DiagnosisModel(
        root_cause="unclear",
        affected_service="gateway",
        triggering_change=None,
        confidence=0.3,
        evidence_summary="Not confident enough to act.",
        hypotheses_considered=["X"],
    )
    investigate_script = [_final_text_message()]
    # The model decides no remediation is warranted -- plain text, no tool call.
    remediation_script = [AIMessage(content="I'm not confident enough to propose an action here.")]
    model = FakeModel(
        tool_call_script=investigate_script,
        hypotheses=hypotheses,
        diagnosis=diagnosis,
        remediation_tool_call_script=remediation_script,
    )
    remediation_tools = _make_fake_remediation_tools(propose_result={}, execute_result={})

    graph = build_graph(model, real_tools, max_tool_calls=15, remediation_tools=remediation_tools)
    result = await graph.ainvoke(
        {"alert_name": "test-alert", "alert_condition": "test-condition", "run_id": "run-2"}
    )

    assert result["remediation"]["proposed"] is False
    assert result["remediation"]["status"] == "none_proposed"
    assert result["remediation_tool_call_count"] == 0


@pytest.mark.asyncio
async def test_remediate_phase_respects_its_own_tool_call_budget(real_tools):
    hypotheses = HypothesesDraft(hypotheses=[HypothesisModel(statement="X", affected_service="checkout", confidence=0.9)])
    diagnosis = DiagnosisModel(
        root_cause="connection leak",
        affected_service="checkout",
        triggering_change="v1.8.3-buggy",
        confidence=0.95,
        evidence_summary="Pool pinned at max.",
        hypotheses_considered=["X"],
    )
    investigate_script = [_final_text_message()]
    # Script always wants to keep calling execute_remediation -- the graph must cut
    # it off at max_remediation_tool_calls rather than looping forever, exactly like
    # the investigate/tools budget test above.
    remediation_script = [
        _tool_call_message("execute_remediation", {"approval_id": "approval-abc"}, f"rem_call_{i}") for i in range(50)
    ]
    model = FakeModel(
        tool_call_script=investigate_script,
        hypotheses=hypotheses,
        diagnosis=diagnosis,
        remediation_tool_call_script=remediation_script,
    )
    remediation_tools = _make_fake_remediation_tools(
        propose_result={"status": "pending_approval"},
        execute_result={"status": "timed_out", "reason": "no approval decision within 90s"},
    )

    graph = build_graph(
        model, real_tools, max_tool_calls=15, remediation_tools=remediation_tools, max_remediation_tool_calls=2
    )
    result = await graph.ainvoke(
        {"alert_name": "test-alert", "alert_condition": "test-condition", "run_id": "run-3"}
    )

    assert result["remediation_tool_call_count"] == 2
    assert result["remediation"]["status"] == "timed_out"


def test_build_graph_without_remediation_tools_is_unchanged(real_tools):
    """Regression guard: passing no remediation_tools (the Layer 3/4 default) must
    produce a graph with no remediate/remediation_tools/finalize_remediation nodes at
    all, so every existing Layer 3/4 caller is provably unaffected by Layer 5."""
    hypotheses = HypothesesDraft(hypotheses=[HypothesisModel(statement="X", affected_service=None, confidence=0.5)])
    diagnosis = DiagnosisModel(
        root_cause="unknown",
        affected_service="catalog",
        triggering_change=None,
        confidence=0.3,
        evidence_summary="n/a",
        hypotheses_considered=["X"],
    )
    model = FakeModel(tool_call_script=[_final_text_message()], hypotheses=hypotheses, diagnosis=diagnosis)
    graph = build_graph(model, real_tools, max_tool_calls=15)
    node_names = set(graph.get_graph().nodes.keys())
    assert "remediate" not in node_names
    assert "remediation_tools" not in node_names
    assert "finalize_remediation" not in node_names
