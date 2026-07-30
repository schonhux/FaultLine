# The agent and the telemetry MCP server ship in one image: the agent process spawns
# the telemetry server as a local stdio subprocess (see mcp_tools.py), so both need to
# be present in the same filesystem. There's no network port between them and nothing
# to expose -- the process boundary is the whole security boundary.
FROM python:3.11-slim

WORKDIR /app

COPY mcp/telemetry-server/requirements.txt ./mcp/telemetry-server/requirements.txt
COPY agent/requirements.txt ./agent/requirements.txt
RUN pip install --no-cache-dir -r mcp/telemetry-server/requirements.txt \
    && pip install --no-cache-dir -r agent/requirements.txt

COPY mcp/telemetry-server ./mcp/telemetry-server
COPY runbooks ./runbooks
COPY agent ./agent

ENV TELEMETRY_SERVER_DIR=/app/mcp/telemetry-server
ENV RUNBOOKS_DIR=/app/runbooks

WORKDIR /app/agent
ENTRYPOINT ["python3", "main.py"]
