# ADR-001: Application-level fault injection over chaos tooling

**Status:** Accepted · 2026-07-28

## Context

FaultLine needs reproducible incidents with exact ground truth. Two approaches were considered:

1. **Infrastructure chaos tooling** (Chaos Mesh, Toxiproxy at the infra layer) — inject pod kills,
   network latency, and partitions from outside the application.
2. **Application-level fault hooks** — each ShopGrid service exposes an internal fault-config API;
   faults are deterministic code paths (leak N connections, add X ms to Redis calls, deploy a
   tagged "buggy" image) toggled by the scenario runner.

## Decision

Application-level fault injection for v1, with the scenario schema kept injection-mechanism-agnostic
so a Kubernetes + Chaos Mesh backend can be added later for network partitions and DNS faults.

## Rationale

- **Determinism.** A benchmark requires identical symptoms across runs. Chaos tooling is
  timing-dependent and environment-sensitive; a seeded code path is not.
- **Exact ground truth.** When the fault *is* the code path, the root cause is known by
  construction — no ambiguity about what the agent should have found.
- **Realism where it matters.** The agent observes the system only through telemetry. A connection
  leak introduced by `checkout-api:v1.8.3-buggy` produces the same metrics, logs, and traces
  whether the bug was written deliberately or shipped accidentally.
- **Runs anywhere.** Docker Compose on a laptop, one command, no cluster.

## Consequences

- Faults requiring true infrastructure interference (network partition, DNS failure) are deferred
  to the v2 Kubernetes overlay.
- Every service carries a small fault-injection module; it is disabled unless the scenario runner
  activates it, and it is documented as test scaffolding.
