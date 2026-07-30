"""CLI entrypoint for a single investigation run.

    python main.py --alert-name checkout-pool-exhausted \\
        --alert-condition "db.pool.active >= 18 (of pool_max 20)" \\
        --run-id <controlplane run uuid, optional, for tagging only>

Streams one JSON-line progress event per graph node as it completes, then prints the
final diagnosis as its own JSON line on stdout. The console spawns this process and
relays those progress lines to the browser over SSE; the evaluation harness only ever
looks at the last line (the one with a "root_cause" key), so the progress events can
change shape freely as long as they don't add one of their own.

If --transcript-out is given, also writes the full message transcript, hypotheses, and
context to that path as JSON so a demo or later evaluation run can replay it without
calling the model again.

This process only ever receives an alert name/condition, never the scenario id, fault
config, or ground truth -- whatever calls it (a human, or the evaluation harness) is
responsible for keeping it that way.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import BaseMessage, ToolMessage

from graph import DEFAULT_MAX_REMEDIATION_TOOL_CALLS, DEFAULT_MAX_TOOL_CALLS, build_graph
from mcp_tools import build_mcp_client, load_remediation_tools_session, load_tools_session

DEFAULT_MODEL = "claude-sonnet-4-5-20250929"


def _message_to_dict(message: BaseMessage) -> dict:
    return {
        "type": message.__class__.__name__,
        "content": message.content,
        "tool_calls": getattr(message, "tool_calls", None),
        "tool_call_id": getattr(message, "tool_call_id", None),
    }


def _emit(event: dict) -> None:
    """Print one progress event as a single JSON line, flushed immediately -- when
    the console pipes this process's stdout, Python's default full buffering would
    otherwise hold every line until exit."""
    print(json.dumps(event, default=str), flush=True)


def _parse_tool_result(content) -> object:
    """MCP tool results come back as a list of content blocks (usually one text
    block holding a JSON string). Small duplicate of graph.py's _parse_tool_json --
    needed here so a remediation proposal's approval_id shows up as soon as it's
    proposed, not just once the terminal status comes back."""
    if isinstance(content, list) and content and isinstance(content[0], dict) and "text" in content[0]:
        try:
            return json.loads(content[0]["text"])
        except (json.JSONDecodeError, KeyError):
            return content[0]["text"]
    if isinstance(content, str):
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return content
    return content


def _summarize_node_update(node: str, output: dict) -> dict:
    """A small, human-readable preview of what a node just produced -- enough for a
    live progress view to show something meaningful without re-deriving it from the
    raw message list. Best-effort: any node not explicitly handled just gets its raw
    keys listed."""
    if node == "context_collection":
        ctx = output.get("context") or {}
        return {
            "deployments_found": len(ctx.get("deployments") or []),
            "error_traces_found": len(ctx.get("error_traces") or []),
            "metric_names_found": len(ctx.get("metric_names") or []),
            "runbooks_found": len(ctx.get("runbooks") or []),
        }
    if node == "hypothesize":
        hyps = output.get("hypotheses") or []
        return {"hypothesis_count": len(hyps), "statements": [h["statement"] for h in hyps]}
    if node in ("investigate", "remediate"):
        last = (output.get("messages") or [None])[-1]
        tool_calls = getattr(last, "tool_calls", None) or []
        summary = {
            "called_tools": [c.get("name") for c in tool_calls],
            "tool_call_count": output.get("tool_call_count", output.get("remediation_tool_call_count")),
        }
        if node == "remediate":
            # Only remediate's tool args are worth sending on -- they're what a
            # human needs to approve or deny (which service, and why).
            summary["tool_calls"] = [
                {"name": c.get("name"), "args": c.get("args")} for c in tool_calls
            ]
        return summary
    if node in ("tools", "remediation_tools"):
        tool_messages = [m for m in (output.get("messages") or []) if isinstance(m, ToolMessage)]
        last_result = _parse_tool_result(tool_messages[-1].content) if tool_messages else None
        summary = {
            "tool_call_count": output.get("tool_call_count", output.get("remediation_tool_call_count")),
        }
        # Only remediation_tools ever carries an approval_id, and it needs to reach
        # the console right away -- execute_remediation can block up to 90s waiting
        # on a human decision.
        if node == "remediation_tools" and isinstance(last_result, dict):
            summary["last_tool_result"] = last_result
        return summary
    if node == "rank":
        diagnosis = output.get("diagnosis") or {}
        return {
            "root_cause_preview": diagnosis.get("root_cause"),
            "affected_service": diagnosis.get("affected_service"),
            "confidence": diagnosis.get("confidence"),
        }
    if node == "finalize_remediation":
        return output.get("remediation") or {}
    return {"keys": list(output.keys())}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a single FaultLine investigation.")
    parser.add_argument("--alert-name", required=True)
    parser.add_argument("--alert-condition", required=True)
    parser.add_argument("--run-id", default=None, help="controlplane run uuid, used only for tagging output")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--max-tool-calls", type=int, default=DEFAULT_MAX_TOOL_CALLS)
    parser.add_argument("--transcript-out", default=None, help="optional path to write the full run transcript")
    parser.add_argument(
        "--enable-remediation",
        action="store_true",
        help=(
            "After diagnosing, let the agent propose a guarded remediation action "
            "(restart_service or rollback_deployment). Still requires human approval "
            "via evaluation/approve.py -- this only lets it *propose* one. Requires "
            "the `remediation` service to be running (`make remediation-up`)."
        ),
    )
    parser.add_argument("--max-remediation-tool-calls", type=int, default=DEFAULT_MAX_REMEDIATION_TOOL_CALLS)
    return parser.parse_args(argv)


async def _stream_graph(graph, initial_state: dict) -> dict:
    """Runs the graph via astream instead of ainvoke, emitting one progress line per
    node as it completes, and returns the same final state ainvoke would have.
    Requesting both "updates" and "values" stream modes lets LangGraph do the state
    merging itself (respecting each field's own reducer) instead of us
    reimplementing it by hand."""
    result: dict = dict(initial_state)
    async for mode, chunk in graph.astream(initial_state, stream_mode=["updates", "values"]):
        if mode == "values":
            result = chunk
        elif mode == "updates":
            for node, output in chunk.items():
                _emit(
                    {
                        "event": "node_complete",
                        "node": node,
                        "at": datetime.now(timezone.utc).isoformat(),
                        "summary": _summarize_node_update(node, output or {}),
                    }
                )
    return result


async def run(args: argparse.Namespace) -> dict:
    model = ChatAnthropic(model=args.model, temperature=0)
    client = build_mcp_client(include_remediation=args.enable_remediation)

    initial_state = {
        "alert_name": args.alert_name,
        "alert_condition": args.alert_condition,
        "run_id": args.run_id,
    }

    _emit(
        {
            "event": "run_started",
            "at": datetime.now(timezone.utc).isoformat(),
            "alert_name": args.alert_name,
            "alert_condition": args.alert_condition,
            "run_id": args.run_id,
            "remediation_enabled": args.enable_remediation,
        }
    )

    # One telemetry-server subprocess for the whole investigation, not one per
    # tool call -- an investigation can easily make a dozen-plus calls.
    async with load_tools_session(client) as tools:
        if args.enable_remediation:
            async with load_remediation_tools_session(client) as remediation_tools:
                graph = build_graph(
                    model,
                    tools,
                    max_tool_calls=args.max_tool_calls,
                    remediation_tools=remediation_tools,
                    max_remediation_tool_calls=args.max_remediation_tool_calls,
                )
                result = await _stream_graph(graph, initial_state)
        else:
            graph = build_graph(model, tools, max_tool_calls=args.max_tool_calls)
            result = await _stream_graph(graph, initial_state)

    if args.transcript_out:
        transcript = {
            "alert_name": args.alert_name,
            "alert_condition": args.alert_condition,
            "run_id": args.run_id,
            "model": args.model,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "context": result.get("context"),
            "hypotheses": result.get("hypotheses"),
            "tool_call_count": result.get("tool_call_count"),
            "messages": [_message_to_dict(m) for m in result.get("messages", [])],
            "diagnosis": result.get("diagnosis"),
            "remediation": result.get("remediation"),
        }
        with open(args.transcript_out, "w", encoding="utf-8") as fh:
            json.dump(transcript, fh, indent=2, default=str)

    output = result.get("diagnosis") or {}
    if args.enable_remediation:
        output = dict(output)
        output["remediation"] = result.get("remediation")
    return output


def main() -> None:
    args = parse_args()
    diagnosis = asyncio.run(run(args))
    print(json.dumps(diagnosis))


if __name__ == "__main__":
    main()
