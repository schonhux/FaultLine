# Layer 1 — Manual Incident Verification

**Goal (exit criterion):** for each of the 6 cataloged fault scenarios, prove that activating it
through the dormant `/internal/fault` API produces a telemetry signature in ClickHouse that is
*visibly distinct* from normal traffic, and that resetting the fault returns the system to
baseline. This is done by hand here, on purpose — Layer 2 is what automates this into a
repeatable scenario runner. Layer 1 exists to prove the signal is real before we automate around it.

General pattern for every scenario:

1. Confirm baseline (traffic flowing normally, no faults active).
2. `POST` a fault config to the owning service's `/internal/fault`. **This must include
   `-H 'Content-Type: application/json'`** — axum's JSON extractor rejects the request
   without it (`curl -d` alone sends `application/x-www-form-urlencoded`, which looks like it
   succeeded from curl's point of view but the server actually returns an error body and the
   fault is never applied).
3. Immediately `GET` the same endpoint and confirm the field you set is actually `true`/nonzero
   in the response, before waiting on anything. This is the cheap way to know the POST landed.
4. Wait the noted amount of time (trafficgen keeps generating load automatically at ~4 req/s).
5. Run the signature query in ClickHouse and confirm it looks like the expected symptom.
6. `POST /internal/fault/reset` on **all four services, unconditionally** — not just the ones
   you think you touched. `POST /internal/fault` replaces the entire config rather than merging
   fields, so a fault left active on a service you didn't touch this round stays silently active
   and contaminates every later scenario's results. Use the reset-all block below every time:

```
curl -s -X POST http://localhost:8080/internal/fault/reset
curl -s -X POST http://localhost:8081/internal/fault/reset
curl -s -X POST http://localhost:8082/internal/fault/reset
curl -s -X POST http://localhost:8083/internal/fault/reset
```

All fault activation calls are plain HTTP, no auth required (the fault API is intentionally
unauthenticated on the internal surface — see ADR-001). All ClickHouse queries use:

```
curl -s -u default:faultline_otel 'http://localhost:8123/' --data "<SQL>"
```

---

## 1. db-pool-exhaustion (checkout, port 8081)

**Ground truth:** every affected request leaks one Postgres connection; pool exhausts, then
every subsequent request stalls on `db.begin()` until the 3s acquire timeout fires.

Activate:
```
curl -s -X POST http://localhost:8081/internal/fault -H 'Content-Type: application/json' -d '{"db_connection_leak": true, "seed": 42}'
```

Wait ~15 seconds (pool_max is 20; at ~4 leaked connections/sec that pool is gone in 5s, give it
margin).

Signature — pool gauges should show `db.pool.active` pinned near 20 and `db.pool.idle` near 0:
```
curl -s -u default:faultline_otel 'http://localhost:8123/' --data "SELECT MetricName, Value, TimeUnix FROM otel.otel_metrics_gauge WHERE MetricName LIKE 'db.pool.%' ORDER BY TimeUnix DESC LIMIT 12 FORMAT PrettyCompact"
```

Corroborating signal — acquisition-timeout errors in the logs:
```
curl -s -u default:faultline_otel 'http://localhost:8123/' --data "SELECT Timestamp, Body FROM otel.otel_logs WHERE ServiceName='checkout' AND Body LIKE '%acquisition timeout%' ORDER BY Timestamp DESC LIMIT 10 FORMAT PrettyCompact"
```

Reset:
```
curl -s -X POST http://localhost:8081/internal/fault/reset
```

---

## 2. redis-latency (catalog, port 8082)

**Ground truth:** every cache GET/SET gets an artificial delay; catalog's own request duration
balloons even though Postgres and Redis themselves are healthy.

Activate:
```
curl -s -X POST http://localhost:8082/internal/fault -H 'Content-Type: application/json' -d '{"redis_latency_ms": 800}'
```

Wait ~15 seconds.

Signature — catalog's `http.request` span duration should roughly double (two cache ops per
request, ~800ms each):
```
curl -s -u default:faultline_otel 'http://localhost:8123/' --data "SELECT ServiceName, SpanName, avg(Duration)/1e6 AS avg_ms, count() FROM otel.otel_traces WHERE ServiceName='catalog' AND SpanName='http.request' AND Timestamp > now() - INTERVAL 2 MINUTE GROUP BY ServiceName, SpanName FORMAT PrettyCompact"
```

Reset:
```
curl -s -X POST http://localhost:8082/internal/fault/reset
```

---

## 3. bad-deployment (catalog, port 8082 + a deployment marker)

**Ground truth:** errors start at the exact moment of a version change, not gradually. We mark
the "deployment" with a `deployment_events` row stamped at the same moment we flip the fault, so
the ground truth (`triggering_change`) is a precise timestamp, not a guess.

Insert the deployment marker (stamped "now"):
```
curl -s -u default:faultline_otel 'http://localhost:8123/' --data "INSERT INTO otel.deployment_events (service, version, git_commit, deployed_at, config) VALUES ('catalog', 'v1.9.0-buggy', 'manual-layer1-catalog', now64(3), '{\"note\":\"manual bad-deployment trigger\"}')"
```

Immediately activate:
```
curl -s -X POST http://localhost:8082/internal/fault -H 'Content-Type: application/json' -d '{"inject_error_rate": 0.6, "seed": 7}'
```

Wait ~30 seconds.

Signature — error rate should jump from ~0 to ~60% starting exactly at the deployment timestamp,
not before:
```
curl -s -u default:faultline_otel 'http://localhost:8123/' --data "SELECT toStartOfMinute(Timestamp) AS minute, countIf(SpanAttributes['http.status_code']='500') AS errors, count() AS total FROM otel.otel_traces WHERE ServiceName='catalog' AND SpanName='http.request' AND Timestamp > now() - INTERVAL 5 MINUTE GROUP BY minute ORDER BY minute FORMAT PrettyCompact"
```

Reset (the deployment_events row is a permanent historical record — do not delete it):
```
curl -s -X POST http://localhost:8082/internal/fault/reset
```

---

## 4. kafka-lag (notifications, port 8083)

**Ground truth:** the consumer stops pulling from `orders.created`; checkout keeps publishing
normally, so lag climbs while the producer side stays healthy.

Activate:
```
curl -s -X POST http://localhost:8083/internal/fault -H 'Content-Type: application/json' -d '{"pause_consumer": true}'
```

Wait ~30 seconds.

Signature — `queue.consumer_lag` should climb steadily:
```
curl -s -u default:faultline_otel 'http://localhost:8123/' --data "SELECT TimeUnix, Value FROM otel.otel_metrics_gauge WHERE MetricName='queue.consumer_lag' ORDER BY TimeUnix DESC LIMIT 20 FORMAT PrettyCompact"
```

Reset, then re-run the same query a minute later to confirm lag drains back down:
```
curl -s -X POST http://localhost:8083/internal/fault/reset
```

---

## 5. retry-storm (checkout, port 8081 + catalog, port 8082)

**Ground truth:** checkout retries catalog 5x with no backoff on failure. Combined with a flaky
catalog, this amplifies request volume against catalog far beyond what trafficgen's steady RPS
would otherwise cause — with user-facing traffic volume unchanged.

Activate both:
```
curl -s -X POST http://localhost:8082/internal/fault -H 'Content-Type: application/json' -d '{"inject_error_rate": 0.5, "seed": 3}'
curl -s -X POST http://localhost:8081/internal/fault -H 'Content-Type: application/json' -d '{"aggressive_retries": true}'
```

Wait ~20 seconds.

Signature — count `dependency.call` spans per parent `http.request` span; should jump from 1 to
up to 5 per checkout:
```
curl -s -u default:faultline_otel 'http://localhost:8123/' --data "SELECT ParentSpanId, count() AS dependency_calls FROM otel.otel_traces WHERE ServiceName='checkout' AND SpanName='dependency.call' AND Timestamp > now() - INTERVAL 2 MINUTE GROUP BY ParentSpanId ORDER BY dependency_calls DESC LIMIT 10 FORMAT PrettyCompact"
```

Reset both:
```
curl -s -X POST http://localhost:8082/internal/fault/reset
curl -s -X POST http://localhost:8081/internal/fault/reset
```

---

## 6. expired-credentials (checkout, port 8081)

**Ground truth:** checkout's internal token to catalog is treated as expired; catalog rejects it
with 401 on every call, starting at the exact moment the fault is flipped.

Activate:
```
curl -s -X POST http://localhost:8081/internal/fault -H 'Content-Type: application/json' -d '{"auth_expired": true}'
```

Wait ~15 seconds.

Signature — catalog logs a 401 warning on every call from checkout:
```
curl -s -u default:faultline_otel 'http://localhost:8123/' --data "SELECT Timestamp, Body FROM otel.otel_logs WHERE ServiceName='catalog' AND Body LIKE '%invalid internal token%' ORDER BY Timestamp DESC LIMIT 10 FORMAT PrettyCompact"
```

Reset:
```
curl -s -X POST http://localhost:8081/internal/fault/reset
```

---

## Exit checklist

- [ ] All 6 scenarios activated individually (not simultaneously).
- [ ] Each produced a signature query result that is visibly different from baseline.
- [ ] Each was reset and confirmed back to baseline (e.g., pool gauges back to near-idle,
      consumer lag draining, error rate back to ~0) before moving to the next.
- [ ] `docker compose ps` still shows every service `Up` after cycling through all 6 — a fault
      should degrade the *simulated* system, not crash the *real* container.
