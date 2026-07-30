# FaultLine

**A benchmark that measures how well an AI agent can diagnose and fix a real production outage.**

Most "AI SRE" demos are a chatbot answering questions about logs. FaultLine is different: it's
a full e-commerce application with a real database, cache, and message queue behind it, wired up
with production-grade observability, that injects an actual failure — a connection leak, a bad
deploy, expired credentials, a retry storm — and then hands an LLM agent nothing but a pager alert.
The agent has to go find the root cause itself, using the same kind of tools a real on-call
engineer would reach for, and it's scored against a known ground truth. Optionally, it can also
propose a fix, but nothing runs without a human clicking approve first.

It's a benchmark, not a toy: every fault is deterministic and reproducible, every diagnosis is
graded automatically, and every write action goes through a policy engine before a human ever
sees it.

## How it works

1. **Pick a scenario.** Six failure modes are defined in `scenarios/`, each with a known root
   cause, expected symptoms, and a recovery condition — a connection pool exhaustion, a bad
   deployment, expired internal credentials, Kafka consumer lag, Redis latency, and a retry storm.
2. **The fault gets injected** into the running application by a Rust control plane, which also
   verifies the app was healthy beforehand and confirms the symptom actually shows up in the
   telemetry before calling it a real incident.
3. **The agent gets paged** — literally just an alert name and a condition string, nothing else.
   It has no access to the scenario definition, the fault config, or the ground truth.
4. **It investigates** using a constrained set of read-only tools: query metrics, search logs,
   find distributed traces, check recent deployments, read runbooks. It builds and narrows down
   competing hypotheses as it goes.
5. **It submits a diagnosis** — root cause, affected service, confidence, and the evidence it's
   standing on — which gets scored against ground truth by an automated evaluation harness.
6. **Optionally, it proposes a fix.** A restart or rollback goes through a policy check and then
   sits as `pending_approval` until a human approves or denies it. Nothing executes unilaterally.

## Architecture

```
┌────────────────────────────────────────────────────────────┐
│                    FaultLine Web Console                   │
│  Scenarios • Live Investigation • Approvals • Scorecards    │
└────────────────────────────┬───────────────────────────────┘
┌────────────────────────────▼───────────────────────────────┐
│                   Control Plane (Rust)                      │
│  Fault injection • run tracking • recovery verification      │
└───────────────┬──────────────────────────┬─────────────────┘
        ┌───────▼──────────┐      ┌────────▼──────────┐
        │ Agent (Python /  │      │ Evaluation Engine │
        │ LangGraph + MCP) │      │ (LLM-as-judge)     │
        └───────┬──────────┘      └───────────────────┘
┌───────────────▼────────────────────────────────────────────┐
│         MCP Tool Layer (read-only + guarded write)          │
│  Metrics • Logs • Traces • Deployments • Runbooks • Ops      │
└───────────────┬────────────────────────────────────────────┘
┌───────────────▼────────────────────────────────────────────┐
│           ShopGrid — the application under test              │
│  Gateway • Checkout • Catalog • Notifications                │
│  PostgreSQL • Redis • Redpanda (Kafka API)                   │
└───────────────┬────────────────────────────────────────────┘
┌───────────────▼────────────────────────────────────────────┐
│        OTel Collector → ClickHouse (metrics/logs/traces)     │
└────────────────────────────────────────────────────────────┘
```

The agent only ever talks to the MCP tool layer — it has no direct database access, no Docker
socket, and no visibility into fault-injection state. Anything it "knows" about an incident, it
had to go query for itself, the same way a human on-call engineer would.

## Tech stack

| Layer | Tech |
|---|---|
| Application under test (ShopGrid) | Rust, Axum, Tokio |
| Control plane / fault injection | Rust |
| Investigation agent | Python, LangGraph, LangChain, Anthropic Claude |
| Tool layer | MCP (Model Context Protocol), two servers: one read-only, one privileged |
| Evaluation | Python, LLM-as-judge scoring |
| Data & telemetry | PostgreSQL, Redis, Redpanda (Kafka-compatible), ClickHouse, OpenTelemetry |
| Web console | Next.js, TypeScript, Tailwind, Radix UI, Server-Sent Events |
| Infra | Docker Compose |

## Quick start

```bash
make up                              # start ShopGrid + telemetry stack
make run-scenario SCENARIO=db-pool-exhaustion SEED=42
make run-agent ALERT_NAME="..." ALERT_CONDITION="..."   # printed by the run above
make eval                            # score all six scenarios end to end
```

Or skip the manual wiring and use the web console — `cd apps/console && npm install && npm run
dev` (with `make up` already running) gives you a browser UI to launch scenarios, watch the
agent investigate live, and approve/deny any proposed fix.

Requires Docker, Rust, Python 3.11+, Node 20+, and an Anthropic API key for live agent runs.

## Safety model

Every write action the agent can take falls into one of four classes: reads are unrestricted,
low-risk changes (like a service restart) are policy-checked, consequential changes (like a
rollback) always require human approval, and destructive actions (deleting data, touching
infrastructure) are never exposed as callable tools at all — not blocked, just nonexistent. The
policy engine that enforces this lives in its own container with its own credentials, separate
from the agent, so the agent itself never holds the privileges it would need to bypass it. Full
detail in [`docs/safety-model.md`](docs/safety-model.md).

## Repository layout

| Path | Contents |
|---|---|
| `platform/` | ShopGrid services (Rust/Axum) and the control plane, with OTel instrumentation and fault-injection hooks |
| `scenarios/` | Scenario definitions — ground truth, symptoms, recovery conditions |
| `agent/` | The LangGraph investigation agent |
| `mcp/` | The two MCP servers (telemetry, guarded remediation) |
| `apps/console/` | The web console (Next.js) |
| `evaluation/` | The scoring harness, approval CLI, and generated reports |
| `observability/` | OTel Collector and ClickHouse setup |
| `runbooks/` | Runbooks the agent can retrieve as reference material |
| `docs/` | Architecture decisions and the safety model |

## Testing

```bash
make test
```

Runs the Rust workspace tests plus the Python test suites (telemetry tools, remediation policy
engine, and the agent's graph logic) against mocked infrastructure — fast, deterministic, and
independent of any running Docker stack or live model calls. Actual diagnosis quality and
end-to-end remediation are verified separately against the real running stack; see the
Makefile targets above.

## Status

The core system — fault injection, agent investigation, evaluation scoring, and guarded
remediation — is built and verified end-to-end against the live stack: the agent correctly
diagnoses real injected faults with cited evidence, `make eval` scores all six scenarios
automatically, and a proposed remediation genuinely blocks on human approval before anything
executes. The web console is built on top of that same system and is in final testing before
it's demo-ready.
