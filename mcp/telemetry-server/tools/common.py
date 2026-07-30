"""Shared constants and small validators used by every Class-0 tool.

Every tool in this package is read-only: it builds a fixed SQL shape against ClickHouse
and interpolates only validated/escaped arguments. None of them accept raw SQL from the
caller (the agent), per FaultLine's safety model -- "every operational capability is a
narrow, schema-validated MCP tool."
"""

from __future__ import annotations

from clickhouse_client import sql_quote

# The four ShopGrid services that actually emit telemetry. Kept in sync by hand with
# platform/controlplane/src/main.rs's ALL_SERVICES constant -- if a service is ever
# added there, add it here too.
KNOWN_SERVICES = ("gateway", "checkout", "catalog", "notifications")


class ToolInputError(ValueError):
    """Raised when a tool argument fails validation; surfaced to the agent as a normal
    tool error so it can retry with corrected arguments, rather than crashing the server."""


def validate_service(service: str | None) -> str | None:
    if service is None:
        return None
    if service not in KNOWN_SERVICES:
        raise ToolInputError(
            f"unknown service {service!r}; must be one of {', '.join(KNOWN_SERVICES)}"
        )
    return service


def validate_minutes(minutes: int, *, max_minutes: int = 24 * 60) -> int:
    if minutes <= 0:
        raise ToolInputError("since_minutes must be positive")
    if minutes > max_minutes:
        raise ToolInputError(f"since_minutes too large; max is {max_minutes}")
    return minutes


def validate_limit(limit: int, *, max_limit: int = 500) -> int:
    if limit <= 0:
        raise ToolInputError("limit must be positive")
    if limit > max_limit:
        raise ToolInputError(f"limit too large; max is {max_limit}")
    return limit


def service_filter_clause(service: str | None, column: str = "ServiceName") -> str:
    if service is None:
        return ""
    return f" AND {column} = '{sql_quote(service)}'"
