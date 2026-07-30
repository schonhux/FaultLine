"""Wires the agent to the FaultLine telemetry MCP server.

The server is spawned as a stdio subprocess -- not a network service -- so the agent
and telemetry-server live in the same container image (see Dockerfile) and talk over
stdin/stdout, the standard MCP pattern for a client that owns its own tool server.
This also means there is no network port to misconfigure or leave open: the process
boundary is the security boundary.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools

TELEMETRY_SERVER_NAME = "telemetry"


def build_mcp_client(telemetry_server_dir: str | None = None) -> MultiServerMCPClient:
    """Build a client that spawns `python3 server.py` in the telemetry-server directory.

    All of this process's environment is forwarded to the subprocess, so the usual
    CLICKHOUSE_URL / CLICKHOUSE_USER / CLICKHOUSE_PASSWORD / RUNBOOKS_DIR variables
    (set at the container level, same convention as platform/controlplane) reach it
    unchanged -- nothing extra needs to be threaded through here by hand.
    """
    telemetry_server_dir = telemetry_server_dir or os.environ.get(
        "TELEMETRY_SERVER_DIR", "/app/mcp/telemetry-server"
    )
    return MultiServerMCPClient(
        {
            TELEMETRY_SERVER_NAME: {
                "transport": "stdio",
                "command": "python3",
                "args": ["server.py"],
                "cwd": telemetry_server_dir,
                "env": dict(os.environ),
            }
        }
    )


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
