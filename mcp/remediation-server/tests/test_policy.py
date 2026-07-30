"""Unit tests for the policy engine -- pure functions, no I/O, no database."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from policy import MAX_REMEDIATIONS_PER_RUN, MIN_JUSTIFICATION_LENGTH, evaluate_policy  # noqa: E402

GOOD_JUSTIFICATION = "db.pool.active pinned at 20/20 since the v1.8.3-buggy deploy; checkout is leaking connections."


def test_restart_service_on_app_service_is_allowed():
    result = evaluate_policy("restart_service", "checkout", GOOD_JUSTIFICATION, "run-1", prior_allowed_count=0)
    assert result.allowed
    assert result.risk_class == 1


def test_rollback_deployment_is_class_2():
    result = evaluate_policy("rollback_deployment", "catalog", GOOD_JUSTIFICATION, "run-1", prior_allowed_count=0)
    assert result.allowed
    assert result.risk_class == 2


def test_unknown_tool_is_denied():
    result = evaluate_policy("delete_database", "postgres", GOOD_JUSTIFICATION, "run-1", prior_allowed_count=0)
    assert not result.allowed
    assert "unknown or disallowed tool" in result.reason


def test_infrastructure_target_is_denied():
    result = evaluate_policy("restart_service", "postgres", GOOD_JUSTIFICATION, "run-1", prior_allowed_count=0)
    assert not result.allowed
    assert "not an application service" in result.reason


def test_fleet_wide_target_is_denied():
    result = evaluate_policy("restart_service", "all", GOOD_JUSTIFICATION, "run-1", prior_allowed_count=0)
    assert not result.allowed


def test_short_justification_is_denied():
    result = evaluate_policy("restart_service", "checkout", "leak", "run-1", prior_allowed_count=0)
    assert not result.allowed
    assert "justification" in result.reason
    assert len("leak") < MIN_JUSTIFICATION_LENGTH


def test_empty_justification_is_denied():
    result = evaluate_policy("restart_service", "checkout", "", "run-1", prior_allowed_count=0)
    assert not result.allowed


def test_rate_limit_denies_second_remediation_in_same_run():
    result = evaluate_policy(
        "restart_service", "checkout", GOOD_JUSTIFICATION, "run-1",
        prior_allowed_count=MAX_REMEDIATIONS_PER_RUN,
    )
    assert not result.allowed
    assert "rate limit" in result.reason


def test_rate_limit_does_not_apply_without_a_run_id():
    # Standalone/manual testing runs may have no run_id at all -- can't rate-limit
    # something with no identity, so this should not be denied on that basis alone.
    result = evaluate_policy(
        "restart_service", "checkout", GOOD_JUSTIFICATION, None,
        prior_allowed_count=5,
    )
    assert result.allowed
