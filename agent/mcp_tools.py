"""Wires the agent to the FaultLine telemetry MCP server, and (optionally, Layer 5)
the guarded-remediation MCP server.

The telemetry server is spawned as a stdio subprocess -- not a network service -- so
the agent and telemetry-server live in the same container image (see Dockerfile) and
talk over stdin/stdout, the standard MCP pattern for a client that owns its own tool
server. This also means there is no network port to misconfigure or leave open: the
process boundary is the security boundary.

The remediation server is different on purpose: it's a separate container with its
own Docker-socket and service-network privileges the agent itself is deliberately
denied (see docker-compose.yml), so the agent reaches it over the network
(streamable-http) instead of spawning it as a local subprocess. See
mcp/remediation-server/ and docs/safety-model.md.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools

TELEMETRY_SERVER_NAME = "telemetry"
REMEDIATION_SERVER_NAME = "remediation"


def build_mcp_client(
    telemetry_server_dir: str | None = None,
    include_remediation: bool = False,
) -> MultiServerMCPClient:
    """Build a client that spawns `python3 server.py` in the telemetry-server directory.

    All of this process's environment is forwarded to the subprocess, so the usual
    CLICKHOUSE_URL / CLICKHOUSE_USER / CLICKHOUSE_PASSWORD / RUNBOOKS_DIR variables
    (set at the container level, same convention as platform/controlplane) reach it
    unchanged -- nothing extra needs to be threaded through here by hand.

    If include_remediation is True, the client also gets a network connection entry
    for the Layer 5 remediation server (REMEDIATION_SERVER_URL, default
    http://remediation:9500/mcp) -- opt-in, since most investigation runs (Layers 3/4)
    have no need for it.
    """
    telemetry_server_dir = telemetry_server_dir or os.environ.get(
        "TELEMETRY_SERVER_DIR", "/app/mcp/telemetry-server"
    )
    servers = {
        TELEMETRY_SERVER_NAME: {
            "transport": "stdio",
            "command": "python3",
            "args": ["server.py"],
            "cwd": telemetry_server_dir,
            "env": dict(os.environ),
        }
    }
    if include_remediation:
        servers[REMEDIATION_SERVER_NAME] = {
            "transport": "streamable_http",
            "url": os.environ.get("REMEDIATION_SERVER_URL", "http://remediation:9500/mcp"),
            # execute_remediation blocks server-side for up to its own timeout_seconds
            # (default 90s, see mcp/remediation-server/tools/remediation.py) waiting
            # for a human decision, over this SAME request/response. The underlying
            # mcp client's own HTTP timeout defaults to 30s, which would silently cut
            # the connection before the server ever gets to answer -- comfortably
            # exceed the server's longest possible wait here.
            "timeout": 120,
        }
    return MultiServerMCPClient(servers)


async def load_tools(client: MultiServerMCPClient | None = None) -> list[BaseTool]:
    """Return every Class-0 tool the telemetry server exposes, as LangChain tools.

    Convenience/one-off helper: `client.get_tools()` opens a fresh session (i.e.
    spawns a new `python3 server.py` subprocess) for every single tool call, which is
    correct but wasteful for a whole investigation that may make a dozen-plus calls.
    Prefer `load_tools_session` for an actual graph run; this is kept for quick
    scripts and tests where call volume is low and simplicity matters more.
    """
    client = client or build_mcp_client()
    return await client.get_tools()


@asynccontextmanager
async def load_tools_session(client: MultiServerMCPClient | None = None):
    """Hold one telemetry-server subprocess/session open for the duration of the
    `with` block and return tools bound to that single session, instead of spawning a
    fresh subprocess per tool call. Use this for an actual investigation run:

        client = build_mcp_client()
        async with load_tools_session(client) as tools:
            graph = build_graph(model, tools)
            result = await graph.ainvoke(...)
    """
    client = client or build_mcp_client()
    async with client.session(TELEMETRY_SERVER_NAME) as session:
        tools = await load_mcp_tools(session)
        yield tools


@asynccontextmanager
async def load_remediation_tools_session(client: MultiServerMCPClient | None = None):
    """Same pattern as load_tools_session, but for the Layer 5 remediation server.

    Kept as a separate function (rather than folding into load_tools_session) so
    every existing Layer 3/4 caller -- main.py's default path, both graph tests, the
    session-reuse test -- is completely untouched: none of them pass
    include_remediation, so nothing about their behavior changes.

    `client` must have been built with build_mcp_client(include_remediation=True), or
    this will fail to find the "remediation" server entry.

        client = build_mcp_client(include_remediation=True)
        async with load_tools_session(client) as tools:
            async with load_remediation_tools_session(client) as remediation_tools:
                graph = build_graph(model, tools, remediation_tools=remediation_tools)
                result = await graph.ainvoke(...)
    """
    client = client or build_mcp_client(include_remediation=True)
    async with client.session(REMEDIATION_SERVER_NAME) as session:
        tools = await load_mcp_tools(session)
        yield tools
