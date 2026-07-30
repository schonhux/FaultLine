"""Unit tests for the propose_*/execute_remediation flow in tools/remediation.py.

Mocks db.py entirely (no real Postgres) and actions.py (no real Docker/HTTP), so these
exercise the actual policy-gate + approval-poll + dispatch logic deterministically and
fast. POLL_INTERVAL_SECONDS is patched down to keep the polling tests from taking real
wall-clock seconds.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import actions  # noqa: E402
import db  # noqa: E402
import tools.remediation as remediation  # noqa: E402

GOOD_JUSTIFICATION = "db.pool.active pinned at 20/20 since the v1.8.3-buggy deploy; checkout is leaking connections."


class _FakeStore:
    """In-memory stand-in for db.py's Postgres-backed remediations table."""

    def __init__(self):
        self.rows: dict[str, dict] = {}
        self._next_id = 0

    def insert_remediation(self, tool, target, risk_class, justification, run_id, policy_decision, policy_reason, status):
        self._next_id += 1
        remediation_id = f"fake-{self._next_id}"
        self.rows[remediation_id] = {
            "id": remediation_id,
            "run_id": run_id,
            "tool": tool,
            "target": target,
            "class": risk_class,
            "justification": justification,
            "policy_decision": policy_decision,
            "policy_reason": policy_reason,
            "status": status,
            "decided_by": None,
        }
        return remediation_id

    def count_allowed_for_run(self, run_id):
        if not run_id:
            return 0
        return sum(1 for r in self.rows.values() if r["run_id"] == run_id and r["policy_decision"] == "allowed")

    def get_remediation(self, remediation_id):
        return self.rows.get(remediation_id)

    def set_executed(self, remediation_id, execution_result):
        self.rows[remediation_id]["status"] = "executed"
        self.rows[remediation_id]["execution_result"] = execution_result

    def set_execution_failed(self, remediation_id, error):
        self.rows[remediation_id]["status"] = "execution_failed"
        self.rows[remediation_id]["execution_result"] = error


def _install_fake_store(monkeypatch) -> _FakeStore:
    store = _FakeStore()
    monkeypatch.setattr(db, "insert_remediation", store.insert_remediation)
    monkeypatch.setattr(db, "count_allowed_for_run", store.count_allowed_for_run)
    monkeypatch.setattr(db, "get_remediation", store.get_remediation)
    monkeypatch.setattr(db, "set_executed", store.set_executed)
    monkeypatch.setattr(db, "set_execution_failed", store.set_execution_failed)
    monkeypatch.setattr(remediation, "POLL_INTERVAL_SECONDS", 0.01)
    return store


def test_propose_with_bad_target_is_denied_and_never_creates_an_approval(monkeypatch):
    store = _install_fake_store(monkeypatch)
    result = remediation.propose_restart_service("postgres", GOOD_JUSTIFICATION, run_id="run-1")

    assert result["status"] == "denied"
    assert "not an application service" in result["reason"]
    # Still audited, just with a terminal 'denied' status and no approval_id.
    assert len(store.rows) == 1
    assert store.rows["fake-1"]["status"] == "denied"


def test_propose_with_good_args_creates_a_pending_approval(monkeypatch):
    store = _install_fake_store(monkeypatch)
    result = remediation.propose_restart_service("checkout", GOOD_JUSTIFICATION, run_id="run-1")

    assert result["status"] == "pending_approval"
    approval_id = result["approval_id"]
    assert store.rows[approval_id]["status"] == "pending_approval"
    assert store.rows[approval_id]["policy_decision"] == "allowed"


def test_execute_remediation_runs_the_action_once_approved(monkeypatch):
    store = _install_fake_store(monkeypatch)
    proposal = remediation.propose_restart_service("checkout", GOOD_JUSTIFICATION, run_id="run-1")
    approval_id = proposal["approval_id"]

    calls = {"n": 0}

    def fake_restart_service(service):
        calls["n"] += 1
        assert service == "checkout"
        return "restarted faultline-checkout-1"

    monkeypatch.setattr(actions, "restart_service", fake_restart_service)

    # Simulate a human approving after the poll loop has already checked twice.
    poll_count = {"n": 0}
    real_get = store.get_remediation

    def flaky_get(remediation_id):
        poll_count["n"] += 1
        if poll_count["n"] >= 3:
            store.rows[remediation_id]["status"] = "approved"
        return real_get(remediation_id)

    monkeypatch.setattr(db, "get_remediation", flaky_get)

    result = remediation.execute_remediation(approval_id, timeout_seconds=5.0)

    assert result["status"] == "executed"
    assert calls["n"] == 1
    assert store.rows[approval_id]["status"] == "executed"


def test_execute_remediation_denied_by_human_never_executes(monkeypatch):
    store = _install_fake_store(monkeypatch)
    proposal = remediation.propose_rollback_deployment("catalog", GOOD_JUSTIFICATION, run_id="run-2")
    approval_id = proposal["approval_id"]
    store.rows[approval_id]["status"] = "denied_by_human"

    called = {"n": 0}
    monkeypatch.setattr(actions, "rollback_deployment", lambda service: called.__setitem__("n", called["n"] + 1))

    result = remediation.execute_remediation(approval_id, timeout_seconds=5.0)

    assert result["status"] == "denied_by_human"
    assert called["n"] == 0


def test_execute_remediation_times_out_without_a_decision(monkeypatch):
    store = _install_fake_store(monkeypatch)
    proposal = remediation.propose_restart_service("checkout", GOOD_JUSTIFICATION, run_id="run-3")
    approval_id = proposal["approval_id"]

    called = {"n": 0}
    monkeypatch.setattr(actions, "restart_service", lambda service: called.__setitem__("n", called["n"] + 1))

    # Never gets approved -- stays pending_approval for the whole poll window.
    result = remediation.execute_remediation(approval_id, timeout_seconds=0.05)

    assert result["status"] == "timed_out"
    assert called["n"] == 0
    assert store.rows[approval_id]["status"] == "pending_approval"


def test_execute_remediation_unknown_approval_id(monkeypatch):
    _install_fake_store(monkeypatch)
    result = remediation.execute_remediation("does-not-exist", timeout_seconds=1.0)
    assert result["status"] == "error"


def test_rate_limit_denies_a_second_proposal_in_the_same_run(monkeypatch):
    _install_fake_store(monkeypatch)
    first = remediation.propose_restart_service("checkout", GOOD_JUSTIFICATION, run_id="run-4")
    assert first["status"] == "pending_approval"

    second = remediation.propose_rollback_deployment("catalog", GOOD_JUSTIFICATION, run_id="run-4")
    assert second["status"] == "denied"
    assert "rate limit" in second["reason"]
