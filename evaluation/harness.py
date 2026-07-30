"""Layer 4 evaluation harness: run every scenario (x seed) through the real stack --
controlplane injects the fault, the agent investigates the resulting alert -- and
score each diagnosis against that scenario's ground truth.

This is a host-side script, not a container: it shells out to `docker compose` (same
commands `make run-scenario` / `make run-agent` wrap) and to `psql` inside the
postgres container to read the alert a run fired. It reads scenario ground_truth
directly from scenarios/<id>/scenario.yaml on disk -- that's the answer key this
harness is allowed to see; the agent itself never does.

Usage:
    export ANTHROPIC_API_KEY=...
    python3 evaluation/harness.py --scenarios db-pool-exhaustion,redis-latency --seeds 42,7
    python3 evaluation/harness.py   # defaults to every scenario in scenarios/, seed 42

Requires: `make up` already running, and this script run from the repo root (it shells
out to `docker compose`, which needs docker-compose.yml in the current directory).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
from clickhouse_reset import reset_telemetry  # noqa: E402
from report_html import write_html_report  # noqa: E402
from scorers.diagnosis_scorer import DEFAULT_JUDGE_MODEL, score_run  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
SCENARIOS_DIR = REPO_ROOT / "scenarios"
DEFAULT_REPORTS_DIR = REPO_ROOT / "evaluation" / "reports"


class RunFailure(RuntimeError):
    """Raised for a specific, identifiable failure point in one (scenario, seed) run
    so the harness can record it and move on rather than crashing the whole sweep."""


def discover_scenarios() -> list[str]:
    return sorted(p.parent.name for p in SCENARIOS_DIR.glob("*/scenario.yaml"))


def load_ground_truth(scenario_id: str) -> dict:
    path = SCENARIOS_DIR / scenario_id / "scenario.yaml"
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data["ground_truth"]


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, **kwargs)


def run_scenario(scenario_id: str, seed: int) -> tuple[str, bool, str]:
    """Inject the fault via controlplane. Returns (run_id, succeeded, raw_output).
    run_id is extracted from the "scenario run starting" log line, which is emitted
    before any of the stages that can fail -- so it's available even on failure."""
    proc = _run(["docker", "compose", "run", "--rm", "controlplane", "run", scenario_id, "--seed", str(seed)])
    output = proc.stdout + proc.stderr
    run_id = None
    for line in output.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        fields = record.get("fields", {})
        if fields.get("message") == "scenario run starting" and "run_id" in fields:
            run_id = fields["run_id"]
            break
    if run_id is None:
        raise RunFailure(f"could not find a run_id in controlplane output for {scenario_id} seed={seed}:\n{output}")
    return run_id, proc.returncode == 0, output


def fetch_alert(run_id: str) -> tuple[str, str] | None:
    """Query Postgres directly (not through the agent) for the alert this run fired."""
    proc = _run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "shopgrid",
            "-d",
            "shopgrid",
            "-t",
            "-A",
            "-F",
            "|",
            "-c",
            f"SELECT name, condition FROM alerts WHERE run_id = '{run_id}' ORDER BY fired_at ASC LIMIT 1;",
        ]
    )
    line = proc.stdout.strip()
    if not line or "|" not in line:
        return None
    name, condition = line.split("|", 1)
    return name.strip(), condition.strip()


def run_agent(
    alert_name: str,
    alert_condition: str,
    run_id: str,
    transcript_filename: str,
    reports_dir: Path,
    model: str,
    max_tool_calls: int,
) -> tuple[dict | None, float, list[dict], str]:
    """Invoke the agent against one alert. Returns (diagnosis or None, wall_clock_seconds,
    messages, raw_output). raw_output (stdout+stderr) is always returned, even on
    success, so a caller can persist it when diagnosis parsing fails -- silently
    discarding *why* an agent run failed is exactly the kind of gap that turns one
    real bug into an unproductive guessing match.

    transcript_filename is written by the agent under /app/output inside its
    container, which docker-compose.yml mounts to ./evaluation/reports on the host --
    so reports_dir / transcript_filename is where it actually lands. This avoids
    `docker compose cp`, which can't work here: `--rm` destroys the container the
    instant the process exits, before any copy-out step could run against it.
    """
    started = time.monotonic()
    proc = _run(
        [
            "docker",
            "compose",
            "run",
            "--rm",
            "agent",
            "--alert-name",
            alert_name,
            "--alert-condition",
            alert_condition,
            "--run-id",
            run_id,
            "--model",
            model,
            "--max-tool-calls",
            str(max_tool_calls),
            "--transcript-out",
            f"/app/output/{transcript_filename}",
        ]
    )
    elapsed = time.monotonic() - started

    diagnosis = None
    # main.py's only stdout line is the final `print(json.dumps(diagnosis))`. Scan
    # from the end for the last line that parses as JSON and looks like a diagnosis,
    # as a defensive fallback in case anything else lands on stdout.
    for line in reversed(proc.stdout.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            candidate = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "root_cause" in candidate:
            diagnosis = candidate
            break

    messages: list[dict] = []
    transcript_path = reports_dir / transcript_filename
    if transcript_path.exists():
        with open(transcript_path, encoding="utf-8") as fh:
            messages = json.load(fh).get("messages", [])

    return diagnosis, elapsed, messages, proc.stdout + proc.stderr


async def evaluate_one(
    scenario_id: str,
    seed: int,
    reports_dir: Path,
    judge_model: str,
    agent_model: str,
    max_tool_calls: int,
) -> dict:
    record: dict = {
        "scenario_id": scenario_id,
        "seed": seed,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        # Clear ClickHouse before injecting -- otherwise the agent can (and, in a
        # live run, did) pick up a previous scenario's deployment marker or traces
        # and misattribute this run's alert to the wrong cause entirely.
        reset_telemetry()
        run_id, scenario_ok, scenario_output = run_scenario(scenario_id, seed)
        record["run_id"] = run_id
        record["scenario_run_succeeded"] = scenario_ok
        if not scenario_ok:
            record["error"] = "controlplane run failed (non-zero exit) -- see scenario_output"
            record["scenario_output"] = scenario_output[-4000:]

        alert = fetch_alert(run_id)
        if alert is None:
            record["error"] = record.get("error") or "no alert fired for this run"
            return record
        alert_name, alert_condition = alert
        record["alert_name"] = alert_name
        record["alert_condition"] = alert_condition

        transcript_filename = f"{scenario_id}-{seed}-transcript.json"
        diagnosis, elapsed, messages, agent_output = run_agent(
            alert_name, alert_condition, run_id, transcript_filename, reports_dir, agent_model, max_tool_calls
        )
        record["diagnosis_time_seconds"] = round(elapsed, 2)
        if diagnosis is None:
            record["error"] = "agent produced no parseable diagnosis"
            record["agent_output"] = agent_output[-4000:]
            return record
        record["diagnosis"] = diagnosis

        ground_truth = load_ground_truth(scenario_id)
        record["ground_truth"] = ground_truth
        score = await score_run(diagnosis, ground_truth, messages, model_name=judge_model)
        record["score"] = score
        return record
    except RunFailure as e:
        record["error"] = str(e)
        return record


def summarize(records: list[dict]) -> dict:
    scored = [r for r in records if "score" in r]
    n = len(records)
    n_scored = len(scored)
    root_cause_correct = sum(1 for r in scored if r["score"]["root_cause_correct"])
    affected_service_correct = sum(1 for r in scored if r["score"]["affected_service_correct"])
    triggering_change_correct = sum(1 for r in scored if r["score"]["triggering_change_correct"])
    unsupported_claims_total = sum(len(r["score"]["unsupported_claims"]) for r in scored)
    avg_time = sum(r["diagnosis_time_seconds"] for r in scored if "diagnosis_time_seconds" in r) / n_scored if n_scored else 0
    return {
        "total_runs": n,
        "scored_runs": n_scored,
        "failed_runs": n - n_scored,
        "root_cause_accuracy": root_cause_correct / n_scored if n_scored else None,
        "affected_service_accuracy": affected_service_correct / n_scored if n_scored else None,
        "triggering_change_accuracy": triggering_change_correct / n_scored if n_scored else None,
        "total_unsupported_claims": unsupported_claims_total,
        "avg_diagnosis_time_seconds": round(avg_time, 2),
    }


async def main_async(args: argparse.Namespace) -> None:
    scenarios = args.scenarios.split(",") if args.scenarios else discover_scenarios()
    seeds = [int(s) for s in args.seeds.split(",")] if args.seeds else [42]
    # Fixed, not configurable: this must match docker-compose.yml's agent service
    # volume mount (./evaluation/reports:/app/output) exactly, or transcripts written
    # inside the container won't be found on the host afterwards.
    reports_dir = DEFAULT_REPORTS_DIR
    reports_dir.mkdir(parents=True, exist_ok=True)

    records = []
    for scenario_id in scenarios:
        for seed in seeds:
            print(f"--- {scenario_id} (seed {seed}) ---", file=sys.stderr)
            record = await evaluate_one(
                scenario_id, seed, reports_dir, args.judge_model, args.agent_model, args.max_tool_calls
            )
            records.append(record)
            status = "OK" if "score" in record else f"FAILED: {record.get('error')}"
            print(f"    {status}", file=sys.stderr)

    summary = summarize(records)
    report = {"generated_at": datetime.now(timezone.utc).isoformat(), "summary": summary, "runs": records}

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = reports_dir / f"eval-{stamp}.json"
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, default=str)

    html_path = reports_dir / f"eval-{stamp}.html"
    write_html_report(report, html_path)

    print(json.dumps(summary, indent=2))
    print(f"\nFull report: {out_path}\nHTML report: {html_path}", file=sys.stderr)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run and score FaultLine scenarios end to end.")
    parser.add_argument("--scenarios", default=None, help="comma-separated scenario ids, default: all")
    parser.add_argument("--seeds", default=None, help="comma-separated seeds, default: 42")
    parser.add_argument("--agent-model", default="claude-sonnet-4-5-20250929")
    parser.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    parser.add_argument("--max-tool-calls", type=int, default=15)
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
