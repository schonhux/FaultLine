"""Direct, deterministic verification of the guarded-remediation mechanism -- calls
propose_restart_service / propose_rollback_deployment / execute_remediation straight
through a real MCP client/server round trip against the running `remediation`
container, Postgres, and Docker socket. Bypasses the LLM entirely: this proves the
approval gate itself works (policy checks, pending state, human approval unblocking
execution, denial blocking it, timeout defaulting to no action), independent of
whether any particular agent run chooses to propose an action.

Usage (run on the host; the remediation service must already be up -- `make
remediation-up` -- since this connects to its published port directly):

    python3 evaluation/verify_remediation.py propose --target checkout --run-id manual-1
    # note the approval_id it prints, then in another terminal:
    python3 evaluation/approve.py list
    python3 evaluation/approve.py approve <id>
    # back here, once approved (or to prove a timeout/denial path, skip approving):
    python3 evaluation/verify_remediation.py execute --approval-id <id>

Try a full round of all three real outcomes:
    1. propose + approve + execute  -> status should be "executed"
    2. propose + deny + execute     -> status should be "denied_by_human"
    3. propose (bad target, e.g. --target postgres) -> denied immediately, no approval_id
"""

from __future__ import annotations

import argparse
import asyncio
import os

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

REMEDIATION_URL = os.environ.get("REMEDIATION_SERVER_URL", "http://localhost:9500/mcp")


async def _call(tool_name: str, args: dict) -> None:
    # timeout=120 for the same reason agent/mcp_tools.py sets it: execute_remediation
    # can legitimately block server-side for up to its own timeout_seconds (default
    # 90s) waiting on a human decision, over this same request.
    async with streamablehttp_client(REMEDIATION_URL, timeout=120) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, args)
            for block in result.content:
                text = getattr(block, "text", None)
                print(text if text is not None else block)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    propose_p = sub.add_parser("propose")
    propose_p.add_argument("--target", required=True, help="gateway, checkout, catalog, or notifications")
    propose_p.add_argument(
        "--justification",
        default="Manual verification run: confirming the approval gate blocks/allows execution correctly.",
    )
    propose_p.add_argument("--run-id", default=None)
    propose_p.add_argument(
        "--rollback", action="store_true", help="propose rollback_deployment instead of restart_service"
    )

    execute_p = sub.add_parser("execute")
    execute_p.add_argument("--approval-id", required=True)
    execute_p.add_argument("--timeout-seconds", type=float, default=90.0)

    args = parser.parse_args()
    if args.command == "propose":
        tool = "propose_rollback_deployment" if args.rollback else "propose_restart_service"
        asyncio.run(
            _call(tool, {"target": args.target, "justification": args.justification, "run_id": args.run_id})
        )
    elif args.command == "execute":
        asyncio.run(_call("execute_remediation", {"approval_id": args.approval_id, "timeout_seconds": args.timeout_seconds}))


if __name__ == "__main__":
    main()
