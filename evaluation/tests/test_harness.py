"""Unit tests for evaluation/harness.py's parsing/orchestration logic.

These do not shell out to real docker/docker-compose -- `_run` is monkeypatched to
return canned CompletedProcess-like output matching what controlplane/agent actually
print (verified against real transcripts from live runs). What they exercise: run_id
extraction from controlplane's JSON log lines, alert parsing from psql's `-A -F'|'`
output, diagnosis extraction from the agent's stdout, and the summary aggregation
math. Real docker orchestration itself is only verified by actually running
`python3 evaluation/harness.py` against a live `make up` stack.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import harness  # noqa: E402


class _FakeProc:
    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


CONTROLPLANE_SUCCESS_OUTPUT = "\n".join(
    [
        '{"timestamp":"2026-07-29T23:55:40Z","level":"INFO","fields":{"message":"telemetry initialized"},"target":"shared::telemetry"}',
        '{"timestamp":"2026-07-29T23:55:40Z","level":"INFO","fields":{"message":"scenario run starting","run_id":"661228ac-2944-4694-b820-1b7800e79288","scenario":"db-pool-exhaustion","seed":42},"target":"faultline"}',
        '{"timestamp":"2026-07-29T23:56:56Z","level":"INFO","fields":{"message":"stage complete: reset (final)","run_id":"661228ac-2944-4694-b820-1b7800e79288"},"target":"faultline"}',
        "run 661228ac-2944-4694-b820-1b7800e79288 (db-pool-exhaustion, seed 42): symptom confirmed at value 20.00, still present after session window: true (verify value 20.00)",
    ]
)

AGENT_STDOUT = (
    '{"root_cause": "Database connection leak in checkout v1.8.3-buggy", '
    '"affected_service": "checkout", "triggering_change": "v1.8.3-buggy", '
    '"confidence": 0.95, "evidence_summary": "...", "hypotheses_considered": ["a", "b"]}'
)


def test_discover_scenarios_finds_all_six():
    scenarios = harness.discover_scenarios()
    assert scenarios == sorted(
        [
            "bad-deployment",
            "db-pool-exhaustion",
            "expired-credentials",
            "kafka-lag",
            "redis-latency",
            "retry-storm",
        ]
    )


def test_load_ground_truth_db_pool_exhaustion():
    gt = harness.load_ground_truth("db-pool-exhaustion")
    assert gt["affected_service"] == "checkout"
    assert gt["triggering_change"] == "deployment_v1.8.3-buggy"


def test_run_scenario_extracts_run_id_on_success(monkeypatch):
    monkeypatch.setattr(harness, "_run", lambda cmd, **kw: _FakeProc(stdout=CONTROLPLANE_SUCCESS_OUTPUT, returncode=0))
    run_id, ok, output = harness.run_scenario("db-pool-exhaustion", 42)
    assert run_id == "661228ac-2944-4694-b820-1b7800e79288"
    assert ok is True


def test_run_scenario_extracts_run_id_even_on_failure(monkeypatch):
    # baseline-health gate failures etc. still log "scenario run starting" first --
    # the harness should still recover the run_id and just flag scenario_ok=False.
    failing_output = CONTROLPLANE_SUCCESS_OUTPUT.replace(
        "run 661228ac", 'Error: "baseline-health gate failed" run 661228ac'
    )
    monkeypatch.setattr(harness, "_run", lambda cmd, **kw: _FakeProc(stdout=failing_output, returncode=1))
    run_id, ok, output = harness.run_scenario("db-pool-exhaustion", 42)
    assert run_id == "661228ac-2944-4694-b820-1b7800e79288"
    assert ok is False


def test_run_scenario_raises_if_no_run_id_found(monkeypatch):
    monkeypatch.setattr(harness, "_run", lambda cmd, **kw: _FakeProc(stdout="nothing useful here", returncode=1))
    with pytest.raises(harness.RunFailure):
        harness.run_scenario("db-pool-exhaustion", 42)


def test_fetch_alert_parses_psql_output(monkeypatch):
    monkeypatch.setattr(
        harness, "_run", lambda cmd, **kw: _FakeProc(stdout="checkout-pool-exhausted|db.pool.active >= 18\n")
    )
    result = harness.fetch_alert("some-run-id")
    assert result == ("checkout-pool-exhausted", "db.pool.active >= 18")


def test_fetch_alert_returns_none_when_empty(monkeypatch):
    monkeypatch.setattr(harness, "_run", lambda cmd, **kw: _FakeProc(stdout=""))
    assert harness.fetch_alert("some-run-id") is None


def test_run_agent_extracts_diagnosis_and_reads_transcript(monkeypatch, tmp_path):
    monkeypatch.setattr(harness, "_run", lambda cmd, **kw: _FakeProc(stdout=AGENT_STDOUT, returncode=0))

    transcript_filename = "db-pool-exhaustion-42-transcript.json"
    (tmp_path / transcript_filename).write_text(json.dumps({"messages": [{"type": "AIMessage", "content": "hi"}]}))

    diagnosis, elapsed, messages, agent_output = harness.run_agent(
        "checkout-pool-exhausted", "db.pool.active >= 18", "run-id", transcript_filename, tmp_path, "model", 15
    )
    assert diagnosis["root_cause"] == "Database connection leak in checkout v1.8.3-buggy"
    assert messages == [{"type": "AIMessage", "content": "hi"}]
    assert elapsed >= 0
    assert AGENT_STDOUT in agent_output


def test_run_agent_handles_missing_transcript_gracefully(monkeypatch, tmp_path):
    monkeypatch.setattr(harness, "_run", lambda cmd, **kw: _FakeProc(stdout=AGENT_STDOUT, returncode=0))
    diagnosis, elapsed, messages, agent_output = harness.run_agent(
        "alert", "condition", "run-id", "missing.json", tmp_path, "model", 15
    )
    assert diagnosis is not None
    assert messages == []


def test_run_agent_surfaces_raw_output_when_diagnosis_missing(monkeypatch, tmp_path):
    # This is the exact failure mode a live `make eval` run just hit: the agent
    # produced no parseable JSON on stdout. The harness must still hand back
    # whatever it did print/error so the caller isn't left guessing why.
    monkeypatch.setattr(
        harness, "_run", lambda cmd, **kw: _FakeProc(stdout="", stderr="Traceback: something exploded", returncode=1)
    )
    diagnosis, elapsed, messages, agent_output = harness.run_agent(
        "alert", "condition", "run-id", "missing.json", tmp_path, "model", 15
    )
    assert diagnosis is None
    assert "something exploded" in agent_output


def test_summarize_computes_accuracy_and_ignores_failed_runs():
    records = [
        {"score": {"root_cause_correct": True, "affected_service_correct": True, "triggering_change_correct": True, "unsupported_claims": []}, "diagnosis_time_seconds": 10.0},
        {"score": {"root_cause_correct": False, "affected_service_correct": True, "triggering_change_correct": False, "unsupported_claims": ["a claim"]}, "diagnosis_time_seconds": 20.0},
        {"error": "no alert fired for this run"},
    ]
    summary = harness.summarize(records)
    assert summary["total_runs"] == 3
    assert summary["scored_runs"] == 2
    assert summary["failed_runs"] == 1
    assert summary["root_cause_accuracy"] == 0.5
    assert summary["affected_service_accuracy"] == 1.0
    assert summary["triggering_change_accuracy"] == 0.5
    assert summary["total_unsupported_claims"] == 1
    assert summary["avg_diagnosis_time_seconds"] == 15.0
