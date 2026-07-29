# FaultLine Master Plan

Autonomous incident-response evaluation arena. This document is the single source of truth for
scope, sequencing, and design decisions. Original spec: full ShopGrid + K8s + Chaos Mesh + MLflow.
This plan revises it for a 3–4 week, high-intensity build that maximizes demo quality and
interview signal per hour invested.

## 0. Why this project exists

The industry is racing to ship AI agents that do on-call/SRE work, and all of them share one
unsolved problem: proving an agent is accurate, efficient, and safe *before* it touches
production. FaultLine is that proof machine. It demonstrates three things big tech hires for
that almost no student portfolio shows together:

1. Building a real distributed system with production-grade observability
2. Building an agent with disciplined architecture — state machine, constrained tools, safety
   policy, human approval — not a prompt in a while-loop
3. Evaluation rigor — ground truth, baselines, calibration, measured claims

It also completes the resume narrative: Berkley (human SRE) → Lenovo (AI reliability signals)
→ FaultLine (the benchmark for AI doing SRE work).

---

## 1. What we're actually selling

The resume/interview product is **the benchmark, not the app**. Priority order of what must be
excellent:

1. **Evaluation harness** — ground-truth scoring, baselines, measured numbers
2. **Agent** — state machine, explicit hypothesis tracking, evidence-cited diagnoses
3. **Safety layer** — tool classes, policy engine, approval gate, audit log
4. **Scenarios** — deterministic, reproducible, seeded
5. **ShopGrid app** — realistic enough to produce honest telemetry; otherwise minimal
6. **Console** — demo polish, built last

The original spec spends ~60% of its text on items 5–6. We invert that.

---

## 2. Improvements over the original spec

| # | Spec said | Plan says | Why |
|---|---|---|---|
| 1 | Kubernetes + Chaos Mesh + Terraform | Docker Compose + application-level fault injection | Determinism is a *requirement* for a benchmark; chaos tooling is timing-dependent and adds weeks of infra with no new resume signal (you already have K8s/Terraform bullets). Scenario schema stays injection-agnostic so a K8s overlay is a clean v2. (ADR-001) |
| 2 | 7 services (gateway, users, catalog, recommendations, checkout, inventory, notifications) | 4 services: gateway, checkout, catalog (absorbs users + inventory), notification worker | Every v1 scenario is expressible with 4. Each extra service costs instrumentation, Dockerfile, dashboards, and debugging time. Recommendations service returns in v2 for the inference-fallback scenario. |
| 3 | Kafka | Redpanda (Kafka-API-compatible, single binary) | Same wire protocol, same consumer-lag scenario, "Kafka" stays truthful on the resume, boots in seconds inside Compose. |
| 4 | gRPC between services | Plain HTTP + JSON internally | gRPC adds codegen and debugging friction with zero scenario value. Traces don't care. |
| 4b | Go backend, Prometheus+Jaeger+Grafana | **Rust (axum/tokio)** services; **OTel → ClickHouse** telemetry | Rust deepens the resume's strongest differentiator; ClickHouse gives the agent one SQL surface over logs/traces/metrics instead of three query APIs, and is the most current observability backend. |
| 5 | MLflow for evaluation | Postgres tables + own scorers, results rendered in console | The eval logic is the differentiator — owning it is better interview material than wiring MLflow. MLflow can be added as an exporter later. |
| 6 | Not mentioned | **Record/replay of every agent run** | Highest-leverage feature in the plan: re-score without API cost, demo without API keys, regression-test agent changes, and debug runs offline. Design it in from Layer 3 day one. |
| 7 | 10 scenarios up front | 6 in v1, chosen for *diagnostic separability* | db-pool, redis-latency, bad-deploy, kafka-lag, retry-storm, expired-creds. These six have mutually distinguishing telemetry signatures, which is what makes top-1 accuracy a meaningful metric. Memory leak, partition, DNS, inference-fallback → v2 (last three need real infra interference). |
| 8 | LangGraph agent described loosely | State machine is the *hard contract*: ALERT_INTAKE → CONTEXT → HYPOTHESES → INVESTIGATE → RANK → PROPOSE → APPROVAL(interrupt) → EXECUTE → VERIFY → REPORT | Forces the hypothesis ledger and evidence citation the eval depends on; approval is a LangGraph interrupt with persisted state. |
| 9 | Baselines listed as experiments A–F | Only three tiers in v1: (A) runbook-mapper (no LLM), (B) one-shot LLM, (C) full tool-using hypothesis agent | Three points establish the curve. Tiers D–F (info-gain, runbook retrieval, guarded remediation as separate arms) are v2 experiments once the harness exists. |
| 10 | Info-gain tool selection in core spec | Deferred to v2 | Research-grade polish. Measure duplicate-query rate first — you need that metric working to prove info-gain helps. |
| 11 | Time-limit / token budget implied | Explicit per-run budget caps (wall time, tool calls, tokens, $) enforced by the orchestrator | Cost control during dev and an honest efficiency metric. |
| 12 | — | Deterministic seeds everywhere: traffic gen, fault timing, scenario runner | Reproducibility claim must be literally true; also makes CI scenario tests possible. |

**Kept from the spec unchanged:** safety classes 0–3, policy-engine checklist, recovery
verification with stability window, run metadata (scenario/seed/agent/model/prompt versions),
hypothesis ledger format, the scorecard metric families, repo layout (trimmed), adversarial
scenarios as the final layer.

---

## 3. Layer plan

Each layer has an **exit criterion** — do not start the next layer until it's met. Days assume
your "3–4 weeks, heavy daily hours" pace.

### Layer 0 — Instrumented ShopGrid (Days 1–5)
Build: 4 Rust services (axum/tokio) + Postgres/Redis/Redpanda; OTel traces/metrics/structured
logs through Collector → ClickHouse; dashboards; traffic generator; deployment-event registry
(versions, commits, timestamps — faked but consistent); health endpoints; `make up`.
Fault-injection hooks built into services from day one (dormant).
**Exit:** `make up` → steady traffic; one trace visible gateway→checkout→postgres→kafka;
RED metrics + pool/queue/cache gauges queryable in ClickHouse for every service.

### Layer 1 — Manual incidents (Days 6–7)
Trigger each of the 6 faults by hand (curl the fault API / swap image tag). Document observed
telemetry signature per incident; write the 6 runbooks. This validates that each scenario is
*diagnosable from telemetry alone* before any agent exists — the most common failure mode of
projects like this is scenarios whose ground truth isn't actually visible.
**Exit:** for each incident, a written signature table: which metrics/logs/traces distinguish it
from the other five.

### Layer 2 — Scenario runner (Days 8–10)
Rust control plane: scenario YAML loader, lifecycle engine (reset → load known-good → warm →
baseline-health gate → inject → symptom gate → session window → verify → score-ready → reset),
run records in Postgres with full metadata + seeds. Alert generator (threshold rules → alert
objects).
**Exit:** `faultline run db-pool-exhaustion --seed 42` twice produces near-identical telemetry
signatures with no manual steps.

### Layer 3 — Read-only agent + MCP tools (Days 10–15) ← the heart
MCP telemetry server backed by ClickHouse SQL (read-only tools: query_metrics, search_logs,
get_trace, compare_traces, get_recent_deployments, get_service_topology, read_runbook,
get_pod_status, get_queue_health, query_database_health). LangGraph state machine with hypothesis ledger (supporting/contradicting
evidence, status, confidence per hypothesis). Diagnosis report schema (root cause, trigger,
evidence, contradictions considered, confidence, recommendation). **Record/replay from the first
run.** Budget caps.
**Exit:** agent solves db-pool-exhaustion and redis-latency end-to-end from alert to structured
diagnosis, and a recorded run replays byte-identically without an API key.

### Layer 4 — Evaluation harness + baselines (Days 16–19)
Scorers: top-1 accuracy, top-3 recall, trigger/affected-service accuracy, unsupported-claim rate
(every evidence claim checked against actually-returned tool results), efficiency (time, tool
calls, duplicate queries, tokens, $), confidence calibration. Baselines A (runbook-mapper) and
B (one-shot LLM). Batch runner: N seeds × 6 scenarios × 3 tiers → results tables + markdown
report.
**Exit:** one command produces the A/B/C comparison table with real measured numbers. *This
table is the resume bullet.*

### Layer 5 — Guarded remediation (Days 20–24)
Write tools (rollback_deployment, restart_service, scale_service, pause/resume_consumer,
clear_cache_namespace, disable_feature_flag) behind the policy engine (class checks, target/scope
validation, evidence requirement, rate limit, audit log). Approval via LangGraph interrupt.
Recovery verification: alert clears + recovery conditions hold for stability window + no new
downstream failures. Policy test suite proving unsafe actions are refused.
**Exit:** full loop on db-pool-exhaustion — diagnose → propose rollback → human approves →
execute → verified recovery; and a red-team test showing `restart postgres` is denied and logged.

### Layer 6 — Console + docs + polish (Days 25–28)
Next.js console: scenario picker, live investigation view (state machine + hypothesis ledger
streaming), approval UI, scorecard + comparison views. Docs: architecture + diagrams, evaluation
methodology, limitations. CI (build, unit, policy tests, one smoke scenario). README with real
measured metrics, 2-minute demo path, and a recorded replay checked into the repo so the demo
works with zero setup.
**Exit:** a stranger with Docker can run `make demo` and see the whole story; you can demo it
cold in an interview in under 5 minutes.

### v2 backlog (post-recruiting version, only if desired)
K8s overlay + Chaos Mesh (network partition, DNS scenarios) · adversarial scenarios (§17 of spec)
· info-gain tool selection · multi-causal incidents · recommendations service + inference-fallback
scenario · MLflow export · cross-model comparison (OpenAI/local).

---

## 4. Stack (v1)

| Layer | Tech |
|---|---|
| App services + control plane + trafficgen | Rust (axum, tokio, tracing/OTel) |
| Agent + evaluation | Python 3.11 (uv), LangGraph, Anthropic API |
| Tool boundary | MCP (Python servers) |
| Console | Next.js 15 + TypeScript + shadcn/ui |
| Data | PostgreSQL (app + runs/scores), Redis, Redpanda |
| Telemetry | OpenTelemetry Collector → ClickHouse (logs, traces, metrics in one SQL surface) |
| Runtime | Docker Compose, Makefile entrypoints |
| CI | GitHub Actions |

## 5. Cost & risk controls

- **API spend:** dev iterations run on replay + Haiku-class models; scored headline runs on a
  stronger model. Budget caps per run. Expected total spend: low tens of dollars.
- **Biggest schedule risk:** Layer 0 scope creep. The app is a fixture — resist making it nice.
- **Biggest quality risk:** scenarios that aren't separable from telemetry (why Layer 1 exists).
- **Honesty rule (from spec §21):** target metrics (70%+ top-1, 90%+ top-3, <5% unsupported
  claims, 0 unsafe executions) are development targets — nothing goes on the resume until measured
  by Layer 4.

## 6. Resume outcome (draft shape, numbers TBD by Layer 4)

> **FaultLine — AI Incident-Response Evaluation Arena** | Rust, Python, LangGraph, MCP, OpenTelemetry, ClickHouse, Docker
> • Built a reproducible benchmark measuring LLM agents' root-cause accuracy, investigation efficiency, and safety across N seeded failure scenarios in an instrumented distributed system (4 Rust microservices, full traces/metrics/logs in ClickHouse).
> • Designed a policy-gated remediation layer (tool safety classes, human approval, automated recovery verification) achieving 0 unsafe action executions across all evaluated runs.
> • Measured X% top-1 root-cause accuracy for a hypothesis-tracking agent vs Y% for one-shot LLM and Z% for a no-LLM runbook baseline.
