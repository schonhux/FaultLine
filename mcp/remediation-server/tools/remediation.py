"""The Layer 5 remediation tools: propose_restart_service, propose_rollback_deployment,
and execute_remediation. Nothing else is exposed anywhere in this server -- there is
no tool for touching infrastructure services, deleting data, changing credentials, or
disabling observability. Those aren't blocked by a check; they simply don't exist as
callable tools, which is what docs/safety-model.md means by "Class 3: never exposed."

Flow: propose_* runs the policy engine (policy.py) and, if it passes, inserts a
pending_approval row and returns its id -- no side effect has happened yet. A human
approves or denies that row out-of-band (evaluation/approve.py). execute_remediation
then polls for that decision (bounded wait) and only performs the real action
(actions.py) if a human approved it in time; on timeout or denial, nothing is executed.
"""

from __future__ import annotations

import time

import actions
import db
from policy import evaluate_policy

DEFAULT_POLL_TIMEOUT_SECONDS = 90.0
POLL_INTERVAL_SECONDS = 3.0


def _propose(tool: str, target: str, justification: str, run_id: str | None) -> dict:
    prior_allowed = db.count_allowed_for_run(run_id)
    result = evaluate_policy(tool, target, justification, run_id, prior_allowed)

    if not result.allowed:
        db.insert_remediation(
            tool, target, result.risk_class, justification, run_id,
            policy_decision="denied", policy_reason=result.reason, status="denied",
        )
        return {"status": "denied", "reason": result.reason}

    remediation_id = db.insert_remediation(
        tool, target, result.risk_class, justification, run_id,
        policy_decision="allowed", policy_reason=result.reason, status="pending_approval",
    )
    return {
        "status": "pending_approval",
        "approval_id": remediation_id,
        "risk_class": result.risk_class,
        "message": (
            f"Policy checks passed (class {result.risk_class}). This action requires "
            "human approval before anything executes -- nothing has happened yet. "
            f"Call execute_remediation with approval_id={remediation_id!r}; it will "
            "wait for a decision and only act if approved."
        ),
    }


def propose_restart_service(target: str, justification: str, run_id: str | None = None) -> dict:
    """Propose restarting one application service: gateway, checkout, catalog, or
    notifications. Class 1 (low-risk) -- still requires human approval before
    executing; this call only records the proposal and returns an approval_id, it
    never takes action itself. `target` must be exactly one of the four application
    services; infrastructure services (postgres, redis, clickhouse, kafka/redpanda)
    are never valid targets and will be denied by policy."""
    return _propose("restart_service", target, justification, run_id)


def propose_rollback_deployment(target: str, justification: str, run_id: str | None = None) -> dict:
    """Propose rolling back the most recent deployment on one application service.
    Class 2 (consequential) -- always requires human approval before executing; this
    call only records the proposal and returns an approval_id, it never takes action
    itself."""
    return _propose("rollback_deployment", target, justification, run_id)


def _terminal_reason(row: dict) -> str | None:
    """The one useful detail to surface for a row that's already in a terminal state
    -- which field that is depends entirely on *why* it's terminal. policy_reason
    only means something for a policy denial; for anything execution-related,
    execution_result has the real detail (e.g. the actual docker/HTTP error), and
    policy_reason would just repeat the unhelpful "all policy checks passed" every
    time. denied_by_human has no useful stored field at all (a human's decision
    isn't accompanied by a stored reason here), so it gets a fixed message."""
    status = row["status"]
    if status == "denied_by_human":
        return "a human denied this action"
    if status in ("executed", "execution_failed"):
        return row.get("execution_result")
    return row.get("policy_reason")


def execute_remediation(approval_id: str, timeout_seconds: float = DEFAULT_POLL_TIMEOUT_SECONDS) -> dict:
    """Wait for a human to approve or deny the remediation identified by approval_id,
    then perform it if (and only if) approved. Polls for up to timeout_seconds; if no
    decision is made in time, the action is NOT taken -- that's the safe default -- and
    this returns status='timed_out'."""
    row = db.get_remediation(approval_id)
    if row is None:
        return {"status": "error", "reason": f"no such approval_id: {approval_id}"}
    if row["status"] not in ("pending_approval", "approved"):
        # Already resolved by a previous execute_remediation call (or was never
        # approvable in the first place) -- report the existing terminal state
        # instead of trying to act on it again. Deliberately no silent retries on the
        # same approval_id; a genuine retry needs a fresh propose_* call, which keeps
        # the audit trail as one row per real attempt.
        return {"status": row["status"], "reason": _terminal_reason(row)}

    deadline = time.monotonic() + timeout_seconds
    while row["status"] == "pending_approval" and time.monotonic() < deadline:
        time.sleep(POLL_INTERVAL_SECONDS)
        row = db.get_remediation(approval_id)

    if row["status"] == "denied_by_human":
        return {"status": "denied_by_human", "reason": "a human denied this action"}
    if row["status"] != "approved":
        return {
            "status": "timed_out",
            "reason": f"no approval decision within {timeout_seconds:.0f}s -- action was NOT taken",
        }

    try:
        if row["tool"] == "restart_service":
            result = actions.restart_service(row["target"])
        elif row["tool"] == "rollback_deployment":
            result = actions.rollback_deployment(row["target"])
        else:
            raise RuntimeError(f"unknown tool on approved remediation: {row['tool']}")
    except Exception as e:  # noqa: BLE001 -- surface any execution failure to the caller
        db.set_execution_failed(approval_id, str(e))
        return {"status": "execution_failed", "reason": str(e)}

    db.set_executed(approval_id, result)
    return {"status": "executed", "result": result}
