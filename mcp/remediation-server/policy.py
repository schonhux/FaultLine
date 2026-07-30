"""Server-side policy engine for FaultLine's guarded remediation tools.

Every proposed action must pass these checks before a human is even asked to approve
it -- this mirrors docs/safety-model.md's "Policy engine checks" list exactly. Check 5
in that list ("is approval required, and has it been granted?") is deliberately NOT
handled here: it's the job of the approval-gate/execute flow in tools/remediation.py,
since it depends on a human decision that hasn't happened yet at proposal time.

This module has no I/O and no side effects -- it's pure logic over plain arguments,
specifically so it can be unit tested without a database, Docker, or network access.
"""

from __future__ import annotations

from dataclasses import dataclass

# Only these four application services may ever be a remediation target. This is the
# practical enforcement of "Class 3 actions are never exposed": infrastructure
# services (postgres, redis, clickhouse, kafka/redpanda, otel-collector) are simply
# never valid here, for either tool, regardless of what the agent asks for.
ALLOWED_SERVICES = ("gateway", "checkout", "catalog", "notifications")

# tool name -> safety class, per docs/safety-model.md's Class 1/2 definitions.
ALLOWED_TOOLS = {
    "restart_service": 1,
    "rollback_deployment": 2,
}

MIN_JUSTIFICATION_LENGTH = 20
MAX_REMEDIATIONS_PER_RUN = 1


@dataclass
class PolicyResult:
    allowed: bool
    reason: str
    risk_class: int | None = None


def evaluate_policy(
    tool: str,
    target: str,
    justification: str,
    run_id: str | None,
    prior_allowed_count: int,
) -> PolicyResult:
    """Run every policy check that doesn't depend on a human decision. Checks are
    numbered to match docs/safety-model.md's "Policy engine checks" list."""

    # 1. Is the tool allowed for this scenario? (There's no per-scenario config yet --
    #    every scenario currently allows both tools, subject to the checks below.)
    if tool not in ALLOWED_TOOLS:
        return PolicyResult(False, f"unknown or disallowed tool: {tool!r}")
    risk_class = ALLOWED_TOOLS[tool]

    # 2. Is the target service allowed?
    if target not in ALLOWED_SERVICES:
        return PolicyResult(
            False,
            f"target {target!r} is not an application service; only "
            f"{ALLOWED_SERVICES} may be targeted -- infrastructure services can "
            "never be remediation targets",
            risk_class,
        )

    # 3. Is the requested scope acceptable (single service, not fleet-wide)? The tool
    #    schema only ever accepts one target string, and check 2 above already
    #    excludes any "all"/fleet-wide value, so a single valid target always
    #    satisfies this by construction.

    # 4. Did the agent supply supporting evidence for the action?
    if not justification or len(justification.strip()) < MIN_JUSTIFICATION_LENGTH:
        return PolicyResult(
            False,
            f"justification must be at least {MIN_JUSTIFICATION_LENGTH} characters "
            "and cite the evidence behind this action",
            risk_class,
        )

    # 6. Is the action rate limit respected? (Check 5, approval, happens later.)
    if run_id and prior_allowed_count >= MAX_REMEDIATIONS_PER_RUN:
        return PolicyResult(
            False,
            f"rate limit: this run already has {prior_allowed_count} remediation(s) "
            f"that passed policy; at most {MAX_REMEDIATIONS_PER_RUN} is allowed per "
            "investigation",
            risk_class,
        )

    # 7. Is a rollback path available? Both tools here are inherently reversible on
    #    their own terms -- restart_service can simply be run again, and
    #    rollback_deployment's entire effect *is* the rollback -- so this always
    #    passes for the two tools that exist today. Listed for parity with the safety
    #    doc, and would matter if a less-reversible tool were ever added.

    return PolicyResult(True, "all policy checks passed", risk_class)
