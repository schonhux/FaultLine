"""The actual side-effecting operations a remediation can perform -- invoked only
after policy checks pass AND a human has approved (see tools/remediation.py). These
are the same two primitives platform/controlplane already uses for scenario
setup/teardown (docker restart, POST /internal/fault/reset), not a new class of
capability: this just gates *when* and *whether* they run behind an approval
workflow.
"""

from __future__ import annotations

import os
import subprocess

import httpx

SERVICE_URLS = {
    "gateway": os.environ.get("GATEWAY_URL", "http://gateway:8080"),
    "checkout": os.environ.get("CHECKOUT_URL", "http://checkout:8081"),
    "catalog": os.environ.get("CATALOG_URL", "http://catalog:8082"),
    "notifications": os.environ.get("NOTIFICATIONS_URL", "http://notifications:8083"),
}


def restart_service(service: str) -> str:
    """`docker restart faultline-<service>-1` -- the same mechanism controlplane uses
    for scenarios whose fault permanently consumes a resource a config-level reset
    can't reclaim (e.g. db-pool-exhaustion's leaked connections)."""
    container = f"faultline-{service}-1"
    result = subprocess.run(
        ["docker", "restart", container],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"docker restart {container} failed: {result.stderr.strip()}")
    return f"restarted {container}"


def rollback_deployment(service: str) -> str:
    """Every 'bad deployment' in this simulated environment is represented as a live
    fault-config flag rather than a real build artifact (see ADR-001), so 'rolling
    back a deployment' means clearing that service's fault state -- exactly what
    POST /internal/fault/reset does. This is the same endpoint controlplane calls at
    the end of every scenario run."""
    if service not in SERVICE_URLS:
        raise ValueError(f"unknown service: {service!r}")
    url = f"{SERVICE_URLS[service]}/internal/fault/reset"
    resp = httpx.post(url, timeout=10.0)
    resp.raise_for_status()
    return f"reset fault config on {service}: {resp.json()}"
