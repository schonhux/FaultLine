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
| `agent/` | LangGraph investigation agent: state machine, hypotheses, policies, prompts |
| `mcp/` | MCP servers exposing the constrained operational tool layer |
| `apps/` | Control plane (Rust) and web console (Next.js) |
| `evaluation/` | Scorers, baselines, datasets, reports |
| `observability/` | OTel Collector and ClickHouse initialization/configuration |
| `runbooks/` | Operational runbooks the agent can retrieve |
| `docs/` | Architecture, incident catalog, safety model, evaluation methodology |

## Key design decisions

- **Application-level fault injection, not chaos tooling** — every fault is a deterministic,
  seedable code path toggled through a fault-config API. Ground truth is exact by construction.
  See [ADR-001](docs/architecture/adr-001-fault-injection.md).
- **State machine, not a free agent loop** — the agent moves through explicit phases
  (context collection → hypothesis generation → investigation → ranking → remediation proposal
  → approval → verification) with persisted state.
- **Safety classes 0–3** — reads are free, low-risk changes are configurable, consequential
  changes always require approval, destructive actions are never exposed. See
  [docs/safety-model.md](docs/safety-model.md).
- **Record and replay** — every run stores the full message/tool trace so evaluation and demos
  never require re-running the model.

## Status

Under active development. Current phase: Layer 0 (instrumented ShopGrid).

Done so far: Rust workspace, shared telemetry/fault/HTTP crate, gateway, checkout, catalog,
notifications, deterministic traffic generator, Compose dependencies, ClickHouse exporter wiring,
Postgres seed data, and ClickHouse deployment-event registry.

Layer 0 exit criterion is not complete until `make up` proves steady traffic and ClickHouse queries
show one distributed trace plus RED, pool, cache, and queue metrics.
