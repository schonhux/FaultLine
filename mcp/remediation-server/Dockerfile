# The remediation server is deliberately its OWN container, separate from the agent
# image: it needs the Docker socket (to restart application containers) and network
# routes to gateway/checkout/catalog/notifications/postgres that the investigation
# agent is intentionally denied (see docker-compose.yml's `agent` service comment).
# The agent only ever reaches this over the network via two narrow MCP tools.
#
# Pinned to -bookworm explicitly (not the floating `python:3.11-slim` tag): this needs
# to match platform/controlplane/Dockerfile's `debian:bookworm-slim` base, which is
# the one proven (live, repeatedly) to give apt's `docker.io` package a working
# `docker` CLI binary. The unpinned tag can resolve to a different Debian release
# where that isn't true.
FROM python:3.11-slim-bookworm

# docker.io provides the `docker` CLI client used by actions.py's restart_service.
# Requires the host Docker socket to be mounted at runtime (see docker-compose.yml).
# The `docker --version` check makes a future base-image drift fail the build loudly
# instead of silently shipping a container with no `docker` binary again.
RUN apt-get update \
    && apt-get install -y --no-install-recommends docker.io \
    && rm -rf /var/lib/apt/lists/* \
    && docker --version

WORKDIR /app

COPY mcp/remediation-server/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY mcp/remediation-server ./

EXPOSE 9500
CMD ["python3", "server.py"]
