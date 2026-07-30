"""CLI entrypoint for a single Layer 3 investigation run.

    python main.py --alert-name checkout-pool-exhausted \\
        --alert-condition "db.pool.active >= 18 (of pool_max 20)" \\
        --run-id <controlplane run uuid, optional, for tagging only>

Prints the final diagnosis as JSON on stdout (one line, so a Layer 4 harness can
capture it directly from the process's stdout). If --transcript-out is given, also
writes the full message transcript + hypotheses + context to that path as JSON, for
"record and replay" per the project's design decisions -- so a demo or a later
evaluation run never needs to re-run the model.

This process is deliberately given only an alert name/condition -- never a scenario_id,
fault_config, or ground_truth. Whatever orchestrates this (a human, or the Layer 4
harness) is responsible for keeping it that way: don't add a --scenario-id flag here.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import BaseMessage

from graph import DEFAULT_MAX_TOOL_CALLS, build_graph
from mcp_tools import build_mcp_client, load_tools_session

DEFAULT_MODEL = "claude-sonnet-4-5-20250929"


def _message_to_dict(message: BaseMessage) -> dict:
    return {
        "type": message.__class__.__name__,
        "content": message.content,
        "tool_calls": getattr(message, "tool_calls", None),
        "tool_call_id": getattr(message, "tool_call_id", None),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a single FaultLine Layer 3 investigation.")
    parser.add_argument("--alert-name", required=True)
    parser.add_argument("--alert-condition", required=True)
    parser.add_argument("--run-id", default=None, help="controlplane run uuid, used only for tagging output")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--max-tool-calls", type=int, default=DEFAULT_MAX_TOOL_CALLS)
    parser.add_argument("--transcript-out", default=None, help="optional path to write the full run transcript")
    return parser.parse_args(argv)


async def run(args: argparse.Namespace) -> dict:
    model = ChatAnthropic(model=args.model, temperature=0)
    client = build_mcp_client()
    tools = await load_tools(client)
    graph = build_graph(model, tools, max_tool_calls=args.max_tool_calls)

    result = await graph.ainvoke(
        {
            "alert_name": args.alert_name,
            "alert_condition": args.alert_condition,
            "run_id": args.run_id,
        }
    )

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
        }
        with open(args.transcript_out, "w", encoding="utf-8") as fh:
            json.dump(transcript, fh, indent=2, default=str)

    return result.get("diagnosis") or {}


def main() -> None:
    args = parse_args()
    diagnosis = asyncio.run(run(args))
    print(json.dumps(diagnosis))


if __name__ == "__main__":
    main()
