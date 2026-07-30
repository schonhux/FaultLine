"""FaultLine Layer 5 guarded-remediation MCP server.

Runs as its own container (see docker-compose.yml's `remediation` service) with the
Docker socket and application-service network access the investigation agent is
deliberately denied (see agent/ in docker-compose.yml). The agent only ever reaches
these two actions over the network, through narrow, policy-checked, approval-gated
tools -- never raw Docker or service access itself. See docs/safety-model.md for the
full design this implements.

Run with: python server.py   (streamable-http transport, since this is a standalone
network service the agent's container connects to -- not a stdio subprocess of the
agent process itself, unlike the telemetry server).

Deliberately does NOT use `from __future__ import annotations`, for the same reason
as mcp/telemetry-server/server.py: this mcp SDK version's tool registration inspects
each parameter's live annotation object, and a lazily-stringified annotation breaks
that.
"""

from mcp.server.fastmcp import FastMCP

from tools.remediation import execute_remediation as _execute_remediation
from tools.remediation import propose_restart_service as _propose_restart_service
from tools.remediation import propose_rollback_deployment as _propose_rollback_deployment

mcp = FastMCP(
    "faultline-remediation",
    instructions=(
        "Guarded remediation for a FaultLine incident. Every action requires human "
        "approval before it takes effect -- proposing an action never changes the "
        "system by itself. Only application services (gateway, checkout, catalog, "
        "notifications) can ever be targeted; infrastructure is never reachable "
        "through these tools."
    ),
    host="0.0.0.0",
    port=9500,
)


@mcp.tool()
def propose_restart_service(target: str, justification: str, run_id: str | None = None) -> dict:
    """Propose restarting one application service (gateway, checkout, catalog, or
    notifications). Class 1 (low-risk): still requires human approval. Only records
    the proposal and returns an approval_id -- takes no action by itself."""
    return _propose_restart_service(target, justification, run_id)


@mcp.tool()
def propose_rollback_deployment(target: str, justification: str, run_id: str | None = None) -> dict:
    """Propose rolling back the most recent deployment on one application service.
    Class 2 (consequential): always requires human approval. Only records the
    proposal and returns an approval_id -- takes no action by itself."""
    return _propose_rollback_deployment(target, justification, run_id)


@mcp.tool()
def execute_remediation(approval_id: str, timeout_seconds: float = 90.0) -> dict:
    """Wait for a human to approve or deny the remediation identified by approval_id,
    then carry it out if (and only if) approved. Waits up to timeout_seconds; if no
    decision is made in time, nothing is executed."""
    return _execute_remediation(approval_id, timeout_seconds)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
