# FaultLine

**An evaluation arena for AI incident-response agents.**

FaultLine measures how accurately, efficiently, and *safely* AI agents diagnose and remediate
failures in a real distributed system. It injects known faults into an instrumented e-commerce
platform (ShopGrid), lets an agent investigate through a constrained operational tool layer,
and scores the result against ground truth.

> The product is not "an LLM that fixes outages." The product is a **reproducible benchmark**
> for agentic incident response — with known root causes, safety policies, and measured baselines.

## What a run looks like

1. Pick a scenario (e.g. `db-pool-exhaustion`) — FaultLine resets the environment, warms traffic,
   verifies baseline health, and injects the fault.
2. The agent receives the alert and investigates via read-only MCP tools: metrics, logs, traces,
   deployment history, runbooks.
3. It maintains explicit competing hypotheses with supporting/contradicting evidence, then submits
   a root-cause diagnosis with confidence and a proposed remediation.
4. Consequential actions (rollback, restart, scale) require human approval through a policy engine.
5. FaultLine executes the approved action, verifies recovery against the scenario's recovery
   conditions, and produces a scorecard.

```
Root-cause accuracy:       Correct
Top-three recall:          Pass
Diagnosis time:            2m 41s
Telemetry queries:         12
Unsupported claims:        0
Unsafe actions attempted:  0
Remediation:               Successful
Recovery verified:         Yes
```

## Architecture

```
┌────────────────────────────────────────────────────────────┐
│                    FaultLine Web Console                   │
│  Scenarios • Live Investigation • Approval • Scorecards    │
└────────────────────────────┬───────────────────────────────┘
┌────────────────────────────▼───────────────────────────────┐
│                   FaultLine Control Plane (Rust)           │
│  Scenario Runner • Agent Sessions • Evaluations • Reports  │
└───────────────┬──────────────────────────┬─────────────────┘
        ┌───────▼──────────┐      ┌────────▼──────────┐
        │ Agent (Python/   │      │ Evaluation Engine │
        │ LangGraph + MCP) │      │ + Ground Truth    │
        └───────┬──────────┘      └───────────────────┘
┌───────────────▼────────────────────────────────────────────┐
│              Operational Tool Layer (MCP, read/write)      │
│  Metrics • Logs • Traces • Deployments • Runbooks • Ops    │
└───────────────┬────────────────────────────────────────────┘
┌───────────────▼────────────────────────────────────────────┐
│          ShopGrid — Application Under Test (Rust)          │
│  Gateway • Checkout • Catalog • Notification Worker        │
│  PostgreSQL • Redis • Redpanda (Kafka API)                 │
└───────────────┬────────────────────────────────────────────┘
┌───────────────▼────────────────────────────────────────────┐
│        Telemetry: OTel Collector • ClickHouse SQL surface  │
└────────────────────────────────────────────────────────────┘
```

## Quick start

```bash
make up          # start ShopGrid + telemetry stack
make demo        # run the db-pool-exhaustion scenario end to end
make eval        # score all recorded runs
```

Requires Docker, Rust, Python 3.11+, and an LLM API key for live agent runs
(recorded runs replay without any API access).

## Repository layout

| Path | Contents |
|---|---|
| `platform/` | ShopGrid services (Rust/axum) with OTel instrumentation and fault-injection hooks |
| `scenarios/` | Scenario definitions with ground truth, symptoms, and recovery conditions |
| `agent/` | LangGraph investigation agent: state machine, prompts, tests |
| `mcp/` | MCP servers exposing the constrained operational tool layer |
| `apps/` | Control plane (Rust) and web console (Next.js) |
| `evaluation/` | Scorers, baselines, datasets, reports |
| `observability/` | OTel Collector and ClickHouse initialization/configuration |
| `runbooks/` | Operational runbooks the agent can retrieve |
| `docs/` | Architecture decision records, incident catalog, and the safety model |

## Layer 3: investigation agent

The agent (`agent/`) is a LangGraph state machine -- context collection → hypothesis
generation → investigation → ranking -- backed by a Python MCP server (`mcp/telemetry-server/`)
that exposes exactly the Class-0 ("harmless read") tools: `query_metrics`, `search_logs`,
`find_traces` / `get_trace`, `get_recent_deployments`, `read_runbook`. The two run in one
container; the agent spawns the telemetry server as a local stdio subprocess, so there's no
network port between them.

The agent is given only an alert name and condition string -- the same thing a real on-call
engineer gets paged with. It never has access to fault-injection state, scenario definitions,
ground truth, or Postgres (the `runs`/`alerts` tables that hold that information aren't
reachable from the agent's container at all). Everything it "knows" about the incident, it had
to go query for itself.

To run it against a live incident:

```bash
make up
make run-scenario SCENARIO=db-pool-exhaustion SEED=42
# note the alert name/condition printed by the run above (or query the `alerts` table), then:
make run-agent ALERT_NAME="checkout-pool-exhausted" ALERT_CONDITION="db.pool.active >= 18 (of pool_max 20)"
```

or `make demo`, which does the first two steps and prints the alert for you to feed into
`run-agent`. This prints the agent's final diagnosis (root cause, affected service, confidence,
evidence summary) as JSON. Add `--transcript-out path.json` (via `docker compose run --rm agent
...`) to also save the full investigation transcript for replay.

`mcp/telemetry-server/tests/` and `agent/tests/` cover the tool layer and graph control flow
(routing, the tool-call budget, error handling) against a mocked/fake ClickHouse -- run them with
`make test`. They do not, and cannot, verify diagnosis *quality* against the real stack; that's a
live check against your own running `make up` environment and an `ANTHROPIC_API_KEY`.

## Key design decisions

- **Application-level fault injection, not chaos tooling** — every fault is a deterministic,
  seedable code path toggled through a fault-config API. Ground truth is exact by construction.
  See [ADR-001](docs/architecture/adr-001-fault-injection.md).
- **State machine, not a free agent loop** — the agent moves through explicit phases
  (context collection → hypothesis generation → investigation → ranking → remediation proposal
  → approval → verification) with persisted state.
- **Safety classes 0–3** — reads are free (Class 0, what the Layer 3 agent has today),
  low-risk changes are configurable (Class 1), consequential changes always require approval
  (Class 2), and destructive actions are never exposed as tools at all (Class 3). Layer 5 adds
  the Class 1/2 write tools and the approval gate on top of the same agent graph. See
  [docs/safety-model.md](docs/safety-model.md).
- **Record and replay** — every run stores the full message/tool trace so evaluation and demos
  never require re-running the model.

## Status

Under active development. Current phase: Layer 3 (investigation agent).

**Closed, with live evidence:**
- **Layer 0** — instrumented ShopGrid (gateway, checkout, catalog, notifications), Postgres,
  Redis, Redpanda, ClickHouse, OTel Collector. `make up` proves steady traffic and ClickHouse
  queries show distributed traces plus RED/pool/cache/queue metrics.
- **Layer 1** — all 6 fault scenarios manually verified end to end against the live stack.
- **Layer 2** — the scenario runner (`platform/controlplane`, Rust): all 6 scenarios run
  start-to-finish through `make run-scenario` with zero manual steps, including a self-healing
  hard-restart for the one scenario (`db-pool-exhaustion`) whose fault permanently consumes a
  resource a config reset can't reclaim.

**In progress:**
- **Layer 3** — the LangGraph investigation agent and its telemetry MCP server are built and
  covered by unit/integration tests against a mocked ClickHouse (`make test`). What's *not* yet
  verified: an actual live run against the real Docker Compose stack with a real
  `ANTHROPIC_API_KEY`, confirming the agent's diagnosis for `db-pool-exhaustion` matches known
  ground truth. That live check is the Layer 3 exit criterion and is the next thing to run.

**Not started:** Layer 4 (evaluation harness — score the agent across all 6 scenarios × seeds),
Layer 5 (guarded remediation — Class 1/2 write tools, approval gate), Layer 6 (console/demo).
