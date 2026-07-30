"""The investigation graph: context collection -> hypothesize -> investigate (ReAct
loop, budget-limited) -> rank -> END, optionally extended with a remediate phase:
propose_* -> execute_remediation -> finalize_remediation -> END.

The evaluation harness invokes this graph once per (scenario, seed) run and scores
the resulting `diagnosis` against that run's ground truth; it never passes
remediation_tools, so it gets the plain investigation graph, unchanged. Passing
remediation_tools gets the extended graph -- see agent/main.py's --enable-remediation
flag.

`build_graph` takes the model and tools as arguments (dependency injection) rather
than constructing them internally, so tests can supply a fake model and a real (but
locally-scoped) tool server -- see tests/test_graph.py.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode
from pydantic import BaseModel, Field

from prompts import (
    BUDGET_EXHAUSTED_NUDGE,
    HYPOTHESIZE_INSTRUCTION,
    RANK_INSTRUCTION,
    REMEDIATION_BUDGET_EXHAUSTED_NUDGE,
    SYSTEM_PROMPT,
    render_context_bundle,
    render_remediate_instruction,
)
from state import AgentState

DEFAULT_MAX_TOOL_CALLS = 15
DEFAULT_MAX_REMEDIATION_TOOL_CALLS = 4


class HypothesisModel(BaseModel):
    statement: str = Field(description="A specific, falsifiable candidate root cause.")
    affected_service: str | None = Field(
        default=None, description="gateway/checkout/catalog/notifications, if known yet."
    )
    confidence: float = Field(description="0.0-1.0, how plausible this seems right now.")


class HypothesesDraft(BaseModel):
    hypotheses: list[HypothesisModel]


class DiagnosisModel(BaseModel):
    root_cause: str = Field(description="A short, specific description of the actual mechanism.")
    affected_service: str = Field(description="gateway, checkout, catalog, or notifications.")
    triggering_change: str | None = Field(
        default=None, description="e.g. a deployment version/commit, or null if not deployment-related."
    )
    confidence: float = Field(description="0.0-1.0")
    evidence_summary: str = Field(description="What you observed that supports this conclusion.")
    hypotheses_considered: list[str] = Field(description="Every hypothesis you evaluated, including rejected ones.")


async def _run_deterministic_tool(tools_by_name: dict[str, BaseTool], name: str, args: dict) -> Any:
    return await tools_by_name[name].ainvoke(args)


def _parse_tool_json(result: Any) -> Any:
    """MCP tool results come back as a list of content blocks (usually one text block
    containing a JSON string) once they pass through langchain_mcp_adapters. Unwrap
    that down to plain Python data for building the context bundle."""
    if isinstance(result, list) and result and isinstance(result[0], dict) and "text" in result[0]:
        try:
            return json.loads(result[0]["text"])
        except (json.JSONDecodeError, KeyError):
            return result[0]["text"]
    if isinstance(result, str):
        try:
            return json.loads(result)
        except json.JSONDecodeError:
            return result
    return result


async def _safe_collect(tools_by_name: dict[str, BaseTool], tool_name: str, args: dict, list_key: str) -> list:
    """Call a Class-0 tool and pull out `list_key`, tolerating any failure by
    returning an empty list instead of raising. Used only for the best-effort
    context-collection sweep -- the investigate phase, which does surface real tool
    errors to the LLM so it can react, is unaffected by this."""
    try:
        parsed = _parse_tool_json(await _run_deterministic_tool(tools_by_name, tool_name, args))
    except Exception:
        return []
    if isinstance(parsed, dict) and isinstance(parsed.get(list_key), list):
        return parsed[list_key]
    return []


def _extract_remediation_outcome(messages: list) -> dict:
    """Scan the remediate-phase messages for the propose_* call the model made (if
    any) and the terminal status the tools reported, so the evaluation harness or a
    transcript reader can see the outcome without re-parsing the raw message list."""
    proposed_tool: str | None = None
    proposed_target: str | None = None
    justification: str | None = None
    last_status: str | None = None
    detail: str | None = None

    for m in messages:
        for call in getattr(m, "tool_calls", None) or []:
            if call.get("name") in ("propose_restart_service", "propose_rollback_deployment"):
                proposed_tool = call["name"]
                args = call.get("args", {})
                proposed_target = args.get("target")
                justification = args.get("justification")
        if isinstance(m, ToolMessage):
            parsed = _parse_tool_json(m.content)
            if isinstance(parsed, dict) and "status" in parsed:
                last_status = parsed["status"]
                detail = parsed.get("reason") or parsed.get("message") or parsed.get("result")

    return {
        "proposed": proposed_tool is not None,
        "tool": proposed_tool,
        "target": proposed_target,
        "justification": justification,
        "status": last_status or "none_proposed",
        "detail": detail,
    }


def build_graph(
    model: BaseChatModel,
    tools: list[BaseTool],
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS,
    remediation_tools: list[BaseTool] | None = None,
    max_remediation_tool_calls: int = DEFAULT_MAX_REMEDIATION_TOOL_CALLS,
):
    tools_by_name = {t.name: t for t in tools}
    tool_node = ToolNode(tools)
    model_with_tools = model.bind_tools(tools)
    hypothesize_model = model.with_structured_output(HypothesesDraft)
    rank_model = model.with_structured_output(DiagnosisModel)

    async def context_collection(state: AgentState) -> dict:
        # Each of these is independently best-effort: context_collection is meant to
        # give the agent a head start, not be a hard dependency. If ClickHouse hiccups
        # or one call errors, degrade to an empty list for that piece rather than
        # failing the whole run -- the investigate phase can still query directly for
        # anything missing here.
        deployments = await _safe_collect(tools_by_name, "get_recent_deployments", {"limit": 10}, "deployments")
        error_traces = await _safe_collect(
            tools_by_name, "find_traces", {"status": "error", "since_minutes": 15, "limit": 10}, "traces"
        )
        metric_names = await _safe_collect(tools_by_name, "list_metric_names", {"since_minutes": 15}, "metrics")
        runbooks = await _safe_collect(tools_by_name, "list_runbooks", {}, "runbooks")
        context = {
            "deployments": deployments,
            "error_traces": error_traces,
            "metric_names": metric_names,
            "runbooks": runbooks,
        }
        return {"context": context}

    async def hypothesize(state: AgentState) -> dict:
        context_bundle = render_context_bundle(state["alert_name"], state["alert_condition"], state["context"])
        messages = [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=context_bundle + "\n\n" + HYPOTHESIZE_INSTRUCTION),
        ]
        draft: HypothesesDraft = await hypothesize_model.ainvoke(messages)
        hypotheses = [
            {
                "statement": h.statement,
                "affected_service": h.affected_service,
                "confidence": h.confidence,
                "supporting_evidence": [],
                "contradicting_evidence": [],
            }
            for h in draft.hypotheses
        ]
        summary_lines = "\n".join(
            f"- {h['statement']} (affected_service={h['affected_service']}, confidence={h['confidence']:.2f})"
            for h in hypotheses
        )
        ai_summary = AIMessage(content=f"My initial hypotheses, before investigating:\n{summary_lines}")
        return {
            "hypotheses": hypotheses,
            "messages": messages + [ai_summary],
            "tool_call_count": 0,
        }

    async def investigate(state: AgentState) -> dict:
        tool_call_count = state.get("tool_call_count", 0)
        messages = state["messages"]
        if tool_call_count >= max_tool_calls:
            nudge = HumanMessage(content=BUDGET_EXHAUSTED_NUDGE)
            response = await model.ainvoke(messages + [nudge])
            return {"messages": [nudge, response]}
        response = await model_with_tools.ainvoke(messages)
        return {"messages": [response]}

    async def tools_node(state: AgentState, config) -> dict:
        # ToolNode needs the RunnableConfig that LangGraph's Pregel runtime carries
        # (it looks up CONFIG_KEY_RUNTIME from it) -- calling tool_node.ainvoke(state)
        # without forwarding the config this node function itself received raises
        # "Missing required config key" even during a real compiled-graph run, since
        # the runtime context lives in that config, not in the state dict.
        last = state["messages"][-1]
        n_calls = len(getattr(last, "tool_calls", None) or [])
        result = await tool_node.ainvoke(state, config)
        return {"messages": result["messages"], "tool_call_count": state.get("tool_call_count", 0) + n_calls}

    def route_after_investigate(state: AgentState) -> str:
        last = state["messages"][-1]
        tool_calls = getattr(last, "tool_calls", None) or []
        if tool_calls and state.get("tool_call_count", 0) < max_tool_calls:
            return "tools"
        return "rank"

    async def rank(state: AgentState) -> dict:
        messages = state["messages"] + [HumanMessage(content=RANK_INSTRUCTION)]
        result: DiagnosisModel = await rank_model.ainvoke(messages)
        diagnosis = {
            "root_cause": result.root_cause,
            "affected_service": result.affected_service,
            "triggering_change": result.triggering_change,
            "confidence": result.confidence,
            "evidence_summary": result.evidence_summary,
            "hypotheses_considered": result.hypotheses_considered,
        }
        return {"diagnosis": diagnosis}

    graph = StateGraph(AgentState)
    graph.add_node("context_collection", context_collection)
    graph.add_node("hypothesize", hypothesize)
    graph.add_node("investigate", investigate)
    graph.add_node("tools", tools_node)
    graph.add_node("rank", rank)

    graph.set_entry_point("context_collection")
    graph.add_edge("context_collection", "hypothesize")
    graph.add_edge("hypothesize", "investigate")
    graph.add_conditional_edges("investigate", route_after_investigate, {"tools": "tools", "rank": "rank"})
    graph.add_edge("tools", "investigate")

    if remediation_tools:
        remediation_tool_node = ToolNode(remediation_tools)
        remediation_model = model.bind_tools(remediation_tools)

        async def remediate(state: AgentState) -> dict:
            count = state.get("remediation_tool_call_count", 0)
            messages = state["messages"]
            first_entry = count == 0
            if first_entry:
                instruction = HumanMessage(content=render_remediate_instruction(state.get("run_id")))
                messages = messages + [instruction]

            if count >= max_remediation_tool_calls:
                nudge = HumanMessage(content=REMEDIATION_BUDGET_EXHAUSTED_NUDGE)
                response = await model.ainvoke(messages + [nudge])
                new_messages = ([instruction] if first_entry else []) + [nudge, response]
                return {"messages": new_messages}

            response = await remediation_model.ainvoke(messages)
            new_messages = ([instruction] if first_entry else []) + [response]
            return {"messages": new_messages}

        async def remediation_tools_node(state: AgentState, config) -> dict:
            last = state["messages"][-1]
            n_calls = len(getattr(last, "tool_calls", None) or [])
            result = await remediation_tool_node.ainvoke(state, config)
            return {
                "messages": result["messages"],
                "remediation_tool_call_count": state.get("remediation_tool_call_count", 0) + n_calls,
            }

        def route_after_remediate(state: AgentState) -> str:
            last = state["messages"][-1]
            tool_calls = getattr(last, "tool_calls", None) or []
            if tool_calls and state.get("remediation_tool_call_count", 0) < max_remediation_tool_calls:
                return "remediation_tools"
            return "finalize_remediation"

        async def finalize_remediation(state: AgentState) -> dict:
            # remediation_tool_call_count is only ever set by remediation_tools_node,
            # so if the model never called a tool (e.g. it judged no action was
            # warranted), the key would otherwise be entirely absent from the final
            # state rather than reading as 0 -- make it always present once
            # remediation is enabled for a run.
            return {
                "remediation": _extract_remediation_outcome(state["messages"]),
                "remediation_tool_call_count": state.get("remediation_tool_call_count", 0),
            }

        graph.add_node("remediate", remediate)
        graph.add_node("remediation_tools", remediation_tools_node)
        graph.add_node("finalize_remediation", finalize_remediation)

        graph.add_edge("rank", "remediate")
        graph.add_conditional_edges(
            "remediate",
            route_after_remediate,
            {"remediation_tools": "remediation_tools", "finalize_remediation": "finalize_remediation"},
        )
        graph.add_edge("remediation_tools", "remediate")
        graph.add_edge("finalize_remediation", END)
    else:
        graph.add_edge("rank", END)

    return graph.compile()
