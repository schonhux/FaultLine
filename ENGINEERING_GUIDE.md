# FaultLine — Engineering Guide

> **Read this first.** This is the end-to-end onboarding document for any engineer or agent
> joining FaultLine. It explains what the project is, why it exists, how it is architected, the
> exact contents and boundaries of every layer, the data contracts between components, and the
> conventions you must follow. If you only read one file, read this one. Companion documents:
> `MASTER_PLAN.md` (sequencing + decisions), `docs/safety-model.md`, `docs/incident-catalog.md`,
> `docs/architecture/adr-001-fault-injection.md`, and `FaultLine Build Log.docx` (running log of
> what was actually built each layer).

---

## 0. TL;DR for a new agent

- **What it is:** a reproducible benchmark that measures how accurately, efficiently, and safely
  AI agents diagnose and remediate failures in a real distributed system.
- **The product is the benchmark, not the app.** The app (ShopGrid) is a fixture that produces
  honest telemetry. Do not over-invest in it.
- **Two systems:** System A = ShopGrid (app under test, Rust). System B = the arena (agent + MCP
  tool layer + evaluation harness + safety layer, Python/LangGraph).
- **Runtime:** Docker Compose, one command. Faults are deterministic code paths, not chaos
  tooling.
- **Stack:** Rust (axum/tokio) services · OTel → ClickHouse telemetry · Python + LangGraph agent ·
  MCP tool boundary · Postgres/Redis/Redpanda · Next.js 15 + shadcn console.
- **Work is sequenced in Layers 0–6.** Each layer has a hard exit criterion. Never start a layer
  before the previous one's exit criterion is met.
- **Golden rule of honesty:** no performance number goes on a resume or README until it's been
  measured by the Layer 4 harness.

---

## 1. Purpose and thesis

The industry is racing to ship AI agents that perform on-call / SRE work (Datadog, Microsoft, PagerDuty,
and a wave of startups). Every one faces the same unsolved problem: **how do you prove an agent is
correct, efficient, and safe before you let it touch production?** FaultLine is that proof machine.

What the finished project demonstrates to a hiring manager:

1. **Distributed systems + observability** — a real multi-service system with production-grade
   traces, metrics, and structured logs.
2. **Disciplined agent architecture** — a state machine with constrained tools, an explicit
   hypothesis ledger, a safety policy, and human approval — not a prompt in a `while` loop.
3. **Evaluation rigor** — ground truth, baselines, calibration, and measured claims. This is the
   rarest and most valuable signal; almost no student portfolio has it.

It also completes a career narrative: **Berkley (human SRE) → Lenovo (AI reliability signals) →
FaultLine (the benchmark for AI doing SRE work).**

### Non-goals
- Not a production incident tool. Not a chatbot over logs. Not a Kubernetes showcase (that's a v2
  overlay). Not a model-training project. We evaluate off-the-shelf models via API.

---

## 2. Mental model / glossary

| Term | Meaning |
|---|---|
| **ShopGrid** | The application under test (System A). A small e-commerce platform. |
| **Arena** | System B: the agent, MCP tools, evaluator, and safety layer that investigate ShopGrid. |
| **Scenario** | A named, seeded failure with exact ground truth (root cause, trigger, symptoms, recovery conditions). |
| **Fault injection** | Activating a dormant fault code path inside a service via its `/internal/fault` API. |
| **Ground truth** | The known-correct answer for a scenario, defined by construction. |
| **Hypothesis ledger** | The agent's live list of competing explanations, each with supporting/contradicting evidence, status, and confidence. |
| **Run** | One execution of one scenario by one agent config, fully recorded for replay and scoring. |
| **Record/replay** | Every run's messages/tool-calls/results are persisted so it can be re-scored or demoed with no API calls. |
| **Baseline** | A non-agent or simpler approach (runbook-mapper, one-shot LLM) used to prove the agent adds value. |
| **Policy engine** | Gate that classifies and validates every write action before execution. |
| **Recovery verification** | Post-remediation check that the incident is actually resolved and stays resolved for a stability window. |

---

## 3. System architecture (end to end)

```
┌────────────────────────────────────────────────────────────────────┐
│  Web Console (Next.js 15 + shadcn)                                 │
│  Scenario picker · live investigation · approval UI · scorecards   │
└───────────────────────────────┬────────────────────────────────────┘
                                │ REST / SSE
┌───────────────────────────────▼────────────────────────────────────┐
│  Control Plane (Rust / axum)                                       │
│  Scenario lifecycle · run records · alert generation · reports API │
└───────┬───────────────────────────────────────────┬────────────────┘
        │ starts/records                            │ reads results
┌───────▼────────────────────┐          ┌───────────▼────────────────┐
│  Agent (Python / LangGraph)│          │  Evaluation Engine (Python) │
│  state machine + ledger    │          │  scorers + baselines        │
└───────┬────────────────────┘          └───────────┬────────────────┘
        │ MCP (stdio/HTTP)                          │ reads run records
┌───────▼────────────────────────────────────────────────────────────┐
│  Operational Tool Layer — MCP servers (Python)                     │
│  READ: query_metrics, search_logs, get_trace, compare_traces,      │
│        get_recent_deployments, get_service_topology, read_runbook, │
│        get_pod_status, get_queue_health, query_database_health     │
│  WRITE (Layer 5, policy-gated): rollback_deployment, restart_service│
│        scale_service, pause/resume_consumer, clear_cache_namespace │
└───────┬───────────────────────────────────┬────────────────────────┘
        │ SQL (reads)                        │ admin calls (writes)
┌───────▼──────────────────┐      ┌──────────▼──────────────────────────┐
│  ClickHouse              │      │  ShopGrid services (Rust/axum)      │
│  logs · traces · metrics │◄─────┤  gateway · checkout · catalog ·     │
│  (one SQL surface)       │ OTLP │  notifications  + trafficgen        │
└──────────────────────────┘  │   │  deps: Postgres · Redis · Redpanda  │
                              │   └─────────────────────────────────────┘
                    ┌─────────▼──────────┐
                    │ OTel Collector     │  receives OTLP, batches, writes ClickHouse
                    └────────────────────┘
```

### Why these boundaries
- **The agent only ever sees ShopGrid through the MCP tool layer.** It has no shell, no DB
  credentials, no cluster access. This is what makes safety measurable — the tool layer *is* the
  attack surface, and it's tiny and typed.
- **ClickHouse is one SQL surface** for logs, traces, and metrics. The MCP read tools are thin,
  well-shaped SQL queries. This is cleaner to build and to defend in an interview than juggling
  Prometheus + Jaeger + Loki APIs.
- **The control plane owns lifecycle and truth.** The agent never resets the environment or reads
  ground truth; only the control plane and evaluator do.

---

## 4. Repository layout

```
FaultLine/
├── Cargo.toml                  # Rust workspace (all services are members)
├── docker-compose.yml          # one-command local environment
├── Makefile                    # up / down / demo / eval / test entrypoints
├── MASTER_PLAN.md              # sequencing, decisions, resume framing
├── ENGINEERING_GUIDE.md        # ← this file
├── FaultLine Build Log.docx    # running log, updated per layer
│
├── platform/                   # System A — ShopGrid (Rust)
│   ├── shared/                 # telemetry init, HTTP middleware, fault API, instrumented client
│   ├── gateway/                # edge: auth, routing, request IDs, rate limiting
│   ├── checkout/               # orders, inventory check, PG txn, Kafka publish  (+ fault hooks)
│   ├── catalog/                # products, Redis read-through cache             (+ fault hooks)
│   ├── notifications/          # Kafka consumer, simulated confirmations        (+ fault hooks)
│   └── trafficgen/             # deterministic load generator
│
├── apps/
│   ├── control-plane/          # Rust: scenario lifecycle, run records, alerts, reports API
│   └── console/                # Next.js 15 + shadcn UI
│
├── agent/                      # System B — the investigating agent (Python)
│   ├── graph/                  # LangGraph state machine nodes + edges
│   ├── hypotheses/             # hypothesis ledger model + ranking
│   ├── policies/               # policy engine (Layer 5)
│   ├── prompts/                # versioned prompts (prompt_version is recorded per run)
│   └── run.py                  # entrypoint: `python -m agent.run --scenario ...`
│
├── mcp/                        # Operational tool layer (MCP servers, Python)
│   ├── telemetry-server/       # read tools backed by ClickHouse SQL
│   ├── deployment-server/      # deployment history + write tools (rollback/restart/scale)
│   └── runbook-server/         # runbook retrieval
│
├── scenarios/                  # one dir per scenario; scenario.yaml is the contract
│   └── db-pool-exhaustion/scenario.yaml   # canonical schema reference
│
├── evaluation/
│   ├── datasets/               # scenario→expected mappings (test cases)
│   ├── scorers/                # metric implementations + batch runner
│   ├── baselines/              # runbook-mapper, one-shot LLM
│   └── reports/                # generated comparison reports (gitignored)
│
├── observability/
│   ├── otel-collector/config.yaml
│   ├── clickhouse/             # schema DDL + init
│   └── grafana/ (optional dev-only dashboards)
│
├── runbooks/                   # markdown runbooks the agent can retrieve
├── infrastructure/docker/      # per-service Dockerfiles
├── tests/                      # unit / integration / scenario / policy / agent-regression
└── docs/                       # architecture, incident catalog, safety model, methodology, ADRs
```

**Project root = the `FaultLine/` folder.** Open that folder in VS Code; all commands run from it.

---

## 5. The layer system (how we build)

Each layer is a shippable increment with a **hard exit criterion**. Do not begin a layer until the
prior exit criterion is demonstrably met. This ordering exists so that the hardest, highest-value
work (agent + evaluation) sits on top of a foundation that is already proven to produce honest,
diagnosable telemetry.

### Layer 0 — Instrumented ShopGrid  *(in progress)*
**Goal:** a real, observable distributed system with dormant fault hooks. No agent.

**Build:**
- Rust workspace with `shared` crate: OTel init (traces+metrics+logs via OTLP), RED-metrics
  middleware, trace-context propagation, instrumented outbound client, and the `/internal/fault`
  admin API + `FaultConfig` state.
- Four services:
  - **gateway** (`:8080`) — external REST edge; auth, routing, request IDs, rate limiting; forwards
    to checkout/catalog.
  - **checkout** (`:8081`) — order validation, inventory check via catalog, Postgres transaction,
    Kafka publish. Exposes DB-pool gauges (`db.pool.active/idle/max`, `db.pool.acquire.duration_ms`).
    Fault hooks: `db_connection_leak`, `auth_expired`, `aggressive_retries`, `inject_error_rate`.
  - **catalog** (`:8082`) — product data, Redis read-through cache over Postgres; cache hit/miss
    counters. Fault hooks: `redis_latency_ms`, `inject_error_rate`.
  - **notifications** (`:8083`) — Kafka consumer of `orders.created`; simulated confirmations;
    consumer-lag gauge. Fault hooks: `pause_consumer`.
- **trafficgen** — deterministic, seeded load against the gateway.
- Dependencies: Postgres (app data), Redis (cache), Redpanda (Kafka API).
- Telemetry: OTel Collector → ClickHouse; schema for logs/traces/metrics.
- Deployment-event registry: a table of `{service, version, git_commit, deployed_at, config}`
  seeded with a plausible history (this is what `get_recent_deployments` reads).

**Exit criterion:** `make up` yields steady traffic; a single distributed trace is visible spanning
gateway→checkout→catalog→postgres and checkout→kafka; RED metrics plus pool/cache/queue gauges are
queryable in ClickHouse for every service; every fault hook exists but is dormant.

### Layer 1 — Manual incidents
**Goal:** prove each scenario is diagnosable **from telemetry alone** before any agent exists.

**Build:** trigger each of the 6 faults by hand (POST `/internal/fault` or swap the deployment
version). For each, write down the observed telemetry **signature** — which metrics/logs/traces
distinguish it from the other five. Author the 6 runbooks in `runbooks/`.

**Exit criterion:** a signature table for all 6 incidents; each is distinguishable from the others
using only what the MCP read tools will expose. (If a scenario isn't separable here, fix the app or
the scenario now — this is the cheapest place to catch it.)

### Layer 2 — Scenario runner (control plane)
**Goal:** a repeatable incident laboratory.

**Build (Rust control plane):** scenario YAML loader; lifecycle engine
(`reset → load known-good → warm traffic → baseline-health gate → inject → symptom gate → session
window → verify → score-ready → reset`); alert generator (threshold rules → alert objects); run
records persisted to Postgres with **full metadata**: scenario id, seed, agent version, model
version, prompt version, tool version, environment commit, start/end time.

**Exit criterion:** `faultline run db-pool-exhaustion --seed 42` executed twice yields near-identical
telemetry signatures with zero manual steps.

### Layer 3 — Read-only diagnostic agent + MCP tools  *(the heart of the project)*
**Goal:** an agent that investigates and submits an evidence-backed diagnosis.

**Build:**
- **MCP telemetry server** backed by ClickHouse SQL, exposing the read tools listed in §3. Each
  tool is narrow and schema-validated (see §7 for the schema contract).
- **LangGraph state machine** (see §6) with an explicit **hypothesis ledger**.
- **Diagnosis report schema:** root cause, triggering event, affected component, supporting
  evidence, contradicting evidence considered, confidence, recommended action, risks, recovery
  criteria.
- **Record/replay from the very first run** — persist every model message, tool call, tool result,
  and state transition. Replays must reproduce without any API key.
- **Budget caps** per run: wall-clock, tool-call count, tokens, dollar estimate.

**Exit criterion:** agent solves `db-pool-exhaustion` and `redis-latency` end-to-end from alert to
structured diagnosis; a recorded run replays identically offline.

### Layer 4 — Evaluation harness + baselines
**Goal:** compare approaches scientifically. **This layer produces the resume numbers.**

**Build:**
- **Scorers:** top-1 root-cause accuracy, top-3 recall, trigger accuracy, affected-service
  accuracy, unsupported-claim rate (every evidence claim checked against tool results actually
  returned), efficiency (time, tool calls, duplicate-query rate, tokens, $), confidence calibration.
- **Baselines:** A = runbook-mapper (alert→static runbook→fixed queries, no LLM); B = one-shot LLM
  (alert + telemetry snapshot, no tools).
- **Batch runner:** N seeds × 6 scenarios × {A, B, agent} → results tables + a markdown/HTML report.

**Exit criterion:** one command produces the A/B/agent comparison table with real measured numbers.

### Layer 5 — Guarded remediation + recovery verification
**Goal:** a safe incident-resolution workflow.

**Build:**
- **Write tools** (`rollback_deployment`, `restart_service`, `scale_service`,
  `pause/resume_consumer`, `clear_cache_namespace`, `disable_feature_flag`) behind the **policy
  engine** (class checks, target/scope validation, evidence requirement, rate limit, audit log —
  see `docs/safety-model.md`).
- **Human approval** via a LangGraph interrupt: state is persisted and the graph waits for an
  approve/deny decision from the console.
- **Recovery verification:** alert clears + recovery conditions hold for the stability window + no
  new downstream failures appear.
- **Policy test suite** proving prohibited actions are refused and logged.

**Exit criterion:** full loop on `db-pool-exhaustion` (diagnose → propose rollback → approve →
execute → verified recovery); red-team test shows `restart postgres` is denied and audited.

### Layer 6 — Console + docs + demo polish
**Goal:** demo-ready, comprehensible by a stranger with Docker.

**Build:** Next.js console (scenario picker, live investigation with streaming state machine +
hypothesis ledger, approval UI, scorecards + comparison views); architecture docs with diagrams;
evaluation methodology; limitations; CI (build + unit + policy tests + one smoke scenario); README
with measured metrics; a committed recorded replay so the demo runs with zero setup.

**Exit criterion:** `make demo` tells the whole story; a cold 5-minute interview demo is possible.

### v2 backlog (not part of the recruiting version)
K8s + Chaos Mesh overlay (enables network-partition and DNS scenarios) · adversarial scenarios
(misleading deployment, red-herring alert, missing telemetry, conflicting evidence, multi-causal,
stale runbook, unsafe easy fix) · information-gain-based tool selection · recommendations service +
inference-fallback scenario · MLflow export · cross-model comparison.

---

## 6. Agent design contract (Layer 3+)

The agent is a **state machine**, never an unconstrained loop. Nodes:

```
ALERT_INTAKE → CONTEXT_COLLECTION → HYPOTHESIS_GENERATION → INVESTIGATION
   → ROOT_CAUSE_RANKING → REMEDIATION_PROPOSAL → HUMAN_APPROVAL (interrupt)
   → ACTION_EXECUTION → RECOVERY_VERIFICATION → INCIDENT_REPORT
```

- **INVESTIGATION** loops with **ROOT_CAUSE_RANKING** until confidence threshold or budget is hit.
- **HUMAN_APPROVAL** is a LangGraph `interrupt`: the graph persists state and resumes only on an
  external approve/deny. Read-only runs (Layers 3–4) stop at REMEDIATION_PROPOSAL.

**Agent state object (persisted, versioned):**
```json
{
  "incident_id": "inc_018",
  "alert": {},
  "topology": {},
  "recent_changes": [],
  "observations": [],
  "hypotheses": [],
  "tool_history": [],
  "current_root_cause": null,
  "confidence": 0.0,
  "proposed_action": null,
  "approval_status": null
}
```

**Hypothesis ledger entry:**
```json
{
  "hypothesis": "connection pool exhaustion",
  "supporting_evidence": ["pool active equals configured maximum", "acquisition wait increased",
                          "traces pause before SQL span begins", "began after checkout deployment"],
  "contradicting_evidence": [],
  "status": "leading",          // proposed | investigating | leading | rejected | confirmed
  "confidence": 0.91
}
```

Every claim in `supporting_evidence` must trace back to an actual tool result — the unsupported-claim
scorer enforces this. This is the single most important discipline in the agent.

---

## 7. Contracts you must not break

These are the seams multiple agents will build against. Change them only with a note in the Build
Log and a version bump.

### 7.1 Fault-injection API (every ShopGrid service)
```
GET  /internal/fault         → current FaultConfig
POST /internal/fault         → replace FaultConfig (scenario runner only)
POST /internal/fault/reset   → dormant
```
`FaultConfig` fields (see `platform/shared/src/fault.rs`): `db_connection_leak`, `redis_latency_ms`,
`inject_error_rate`, `pause_consumer`, `auth_expired`, `aggressive_retries`, `seed`. Faults are
**dormant by default**; only the scenario runner activates them.

### 7.2 Scenario YAML (the ground-truth contract)
`scenarios/<id>/scenario.yaml` (canonical example: `db-pool-exhaustion`). Required keys: `id`,
`title`, `difficulty`, `inject{type,target,version,fault_config}`,
`ground_truth{root_cause,triggering_change,affected_service}`, `expected_symptoms[]`,
`alert{name,condition}`, `allowed_remediations[]`, `unsafe_actions[]`,
`recovery_conditions{p95_latency_ms,error_rate_percent,stability_window_seconds}`,
`lifecycle{warm_traffic_seconds,baseline_health_required,symptom_wait_seconds,time_limit_seconds}`.

### 7.3 MCP tool schema (operational tool layer)
Every tool is named, described, and has a JSON input schema. Example:
```json
{
  "name": "query_metrics",
  "description": "Query a predefined operational metric over a time window",
  "input": {
    "metric": "checkout_db_pool_active",
    "service": "checkout",
    "start_time": "2026-07-27T19:00:00Z",
    "end_time": "2026-07-27T19:10:00Z",
    "aggregation": "max"
  }
}
```
Tools are classified by safety class (0=read, 1=low-risk, 2=consequential, 3=prohibited/never
exposed). See `docs/safety-model.md`. Read tools return data only from ClickHouse; they never touch
ground truth.

### 7.4 Run record (record/replay + evaluation)
Every run persists: metadata (scenario, seed, agent/model/prompt/tool versions, env commit, times),
full message trace, tool calls + results, state transitions, hypotheses over time, final diagnosis,
human decision, remediation, verification result. The evaluator and the replayer both read this
schema — treat it as an API.

### 7.5 Telemetry semantics
Follow OpenTelemetry semantic conventions. Key custom metrics: `http.server.request.{count,duration_ms}`,
`http.server.error.count`, `http.client.dependency.duration_ms`, `db.pool.{active,idle,max}`,
`db.pool.acquire.duration_ms`, `cache.{hits,misses}`, consumer-lag gauge on notifications. Logs are
structured JSON and trace-correlated (trace_id present).

---

## 8. Conventions

- **Determinism everywhere.** Traffic gen, fault timing, and scenario runner all take a seed. Same
  seed ⇒ same run. Reproducibility is a *claim we make*, so it must be literally true.
- **Ports:** gateway 8080, checkout 8081, catalog 8082, notifications 8083, OTLP 4317/4318,
  ClickHouse 8123/9000, Postgres 5432, Redis 6379, Redpanda 9092, console 3000, control plane TBD.
- **One command to run.** `make up`, `make demo`, `make eval`, `make down`. Keep it that way.
- **Cost discipline.** Dev iterations use replay + cheap models; scored headline runs use a stronger
  model. Enforce per-run budget caps.
- **Honesty rule.** Target metrics in the plan (70%+ top-1, 90%+ top-3, <5% unsupported claims, 0
  unsafe executions) are *development targets*. Nothing goes public until Layer 4 measures it.
- **Document as you go.** Update `FaultLine Build Log.docx` at the end of each layer (and for major
  mid-layer decisions): what was built, why, the architecture, rejected alternatives, problems +
  fixes, and how the exit criterion was verified.

---

## 9. How to parallelize across agents

Once **Layer 0** and the contracts in §7 are stable, independent agents can work concurrently:

- **Agent P (Platform/Rust):** owns `platform/*` and the control plane; keeps §7.1, §7.2, §7.5
  stable.
- **Agent T (Tools/MCP):** owns `mcp/*`; builds read tools against the ClickHouse schema and the
  §7.3 contract. Can start as soon as Layer 0 telemetry lands.
- **Agent A (Agent/LangGraph):** owns `agent/*`; builds the state machine and ledger against the MCP
  contract. Can develop against recorded/mock tool results before real tools are done.
- **Agent E (Evaluation):** owns `evaluation/*`; builds scorers against the §7.4 run-record schema.
  Can start with synthetic run records.
- **Agent U (UI):** owns `apps/console`; builds against the control plane's REST/SSE API.

**Coordination rules:** contracts in §7 are the interface; changing one requires a Build Log entry
and a ping to dependent agents. The layer exit criteria are the integration gates.

---

## 10. Current status

- Layer 0 in progress. Done so far: Rust workspace + `shared` crate (telemetry, HTTP middleware,
  fault API), `catalog` and `checkout` services with fault hooks and pool/cache instrumentation.
- Next in Layer 0: `gateway`, `notifications`, `trafficgen`, Dockerfiles, ClickHouse schema +
  Collector wiring, deployment-event registry, and the DB/product seed data — then verify the exit
  criterion.

*(Keep this section current as layers complete; the Build Log holds the detailed history.)*
