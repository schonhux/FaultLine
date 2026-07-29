# FaultLine — System Connectivity Map (Layer 0, as actually built)

This document describes exactly what exists in the repository today and exactly how each piece
talks to every other piece. It reflects the real implementation, not the aspirational spec — if
something isn't listed here, it isn't built yet.

## 1. Everything that exists right now

| Component | What it is | Language | Where |
|---|---|---|---|
| `postgres` | App database: `products`, `orders` tables | — (Postgres 16) | `infrastructure/docker/postgres/001-shopgrid.sql` |
| `redis` | Cache used by `catalog` | — (Redis 7) | n/a |
| `redpanda` | Kafka-API message broker | — (Redpanda v24.1.7) | n/a |
| `clickhouse` | Telemetry store: traces, logs, metrics + `deployment_events` | — (ClickHouse 24.8) | `observability/clickhouse/init/001-deployment-events.sql` |
| `otel-collector` | Receives OTLP from all 5 Rust services, writes to ClickHouse | — (otelcol-contrib 0.104.0) | `observability/otel-collector/config.yaml` |
| `gateway` | Public HTTP edge: auth, rate limit, forwards to checkout/catalog | Rust (axum) | `platform/gateway` |
| `checkout` | Order validation, Postgres transaction, Kafka publish, fault hooks | Rust (axum) | `platform/checkout` |
| `catalog` | Product data, Redis read-through cache, fault hooks | Rust (axum) | `platform/catalog` |
| `notifications` | Kafka consumer, simulated confirmations, fault hook | Rust (axum + rdkafka) | `platform/notifications` |
| `trafficgen` | Deterministic seeded load generator | Rust | `platform/trafficgen` |
| `shared` | Not a running service — a Rust library crate used by all 5 services above (telemetry init, HTTP middleware, fault API) | Rust | `platform/shared` |

**Not built yet:** control plane, scenario runner, agent, MCP tool layer, evaluation harness,
console, remediation/approval. Those are Layers 2–6. Layer 0 is *only* the eight running
containers above.

## 2. Network map — who talks to whom, over what

```
                         ┌──────────────┐
                         │  trafficgen  │  (HTTP client only, no server)
                         └──────┬───────┘
                                │ HTTP POST /checkout
                                │ header: x-shopgrid-api-key: dev-shopgrid-key
                                ▼
┌───────────────────────────────────────────────────────────┐
│  gateway :8080                                             │
│  - checks x-shopgrid-api-key (public auth)                 │
│  - rate limiter (in-memory, per-second window)             │
│  - GET /products, GET /products/:id, POST /checkout        │
└──────┬───────────────────────────────────┬─────────────────┘
       │ HTTP GET, header:                 │ HTTP POST, header:
       │ x-internal-token: svc-gateway-token│ x-internal-token: svc-gateway-token
       ▼                                    ▼
┌────────────────┐                 ┌─────────────────────────────────────┐
│  catalog :8082 │◄────────────────┤  checkout :8081                     │
│  - reads/writes│  HTTP GET       │  - validates product via catalog    │
│    Redis cache │  x-internal-    │  - Postgres transaction (insert     │
│  - reads       │  token:         │    order, decrement stock)          │
│    Postgres    │  svc-checkout-  │  - publishes Kafka message          │
└───────┬────────┘  token          └──────┬───────────────────────┬──────┘
        │                                  │                       │
        ▼                                  ▼                       │
   ┌─────────┐                       ┌──────────┐                  │
   │  redis  │                       │ postgres │                  │
   └─────────┘                       └──────────┘                  │
                                                                    │ Kafka topic:
                                                                    │ orders.created
                                                                    ▼
                                                          ┌──────────────────┐
                                                          │  redpanda :9092  │
                                                          └────────┬─────────┘
                                                                   │ consumer group:
                                                                   │ shopgrid-notifications
                                                                   ▼
                                                          ┌──────────────────────┐
                                                          │ notifications :8083  │
                                                          │ logs a simulated     │
                                                          │ confirmation per msg │
                                                          └──────────────────────┘

All five Rust services also, independently, send telemetry:

┌─────────┬─────────┬─────────┬──────────────┬────────────┐
│ gateway │ checkout │ catalog │ notifications│ trafficgen*│
└────┬────┴────┬────┴────┬────┴──────┬───────┴─────┬──────┘
     │  OTLP/gRPC (traces + metrics + logs), all four → :4317
     │  *trafficgen does NOT export OTLP — it only logs to stdout
     ▼
┌────────────────────┐
│  otel-collector      │  receives OTLP, batches, exports via ClickHouse
│  :4317 (gRPC)        │  native protocol
│  :4318 (HTTP, unused)│
└──────────┬───────────┘
           │ TCP :9000 (ClickHouse native protocol)
           ▼
     ┌─────────────┐
     │ clickhouse  │  database `otel`: otel_traces, otel_logs,
     │ :8123 :9000 │  otel_metrics_gauge/sum/histogram/summary/exp_histogram,
     └─────────────┘  plus hand-seeded deployment_events
```

## 3. Protocol / port reference

| From | To | Protocol | Port | Purpose |
|---|---|---|---|---|
| trafficgen | gateway | HTTP/1.1 + JSON | 8080 | synthetic checkout load |
| gateway | catalog | HTTP/1.1 + JSON | 8082 | product lookups (internal token) |
| gateway | checkout | HTTP/1.1 + JSON | 8081 | checkout requests (internal token) |
| checkout | catalog | HTTP/1.1 + JSON | 8082 | stock/price validation (internal token) |
| checkout | postgres | Postgres wire protocol | 5432 | orders/products table writes |
| catalog | postgres | Postgres wire protocol | 5432 | products table reads |
| catalog | redis | RESP | 6379 | product cache GET/SET |
| checkout | redpanda | Kafka wire protocol | 9092 | publish `orders.created` |
| notifications | redpanda | Kafka wire protocol | 9092 | consume `orders.created` |
| gateway/checkout/catalog/notifications | otel-collector | OTLP/gRPC | 4317 | traces, metrics, logs |
| otel-collector | clickhouse | ClickHouse native protocol | 9000 | writes telemetry tables |
| you (curl/browser) | clickhouse | HTTP | 8123 | ad-hoc SQL queries |
| scenario runner (Layer 2, not built yet) | every service | HTTP/1.1 + JSON | each service's own port, path `/internal/fault` | activate/reset dormant faults |

## 4. How a single request actually flows (concrete example)

1. `trafficgen` picks a random `product_id`/`quantity`, `POST`s to `gateway:8080/checkout` with
   the public API key header.
2. `gateway`'s `edge_middleware` checks the API key and rate limit, then forwards to
   `checkout:8081/checkout` with an internal service token and the current trace context injected
   into the request headers (`traceparent`).
3. `checkout` calls `catalog:8082/products/{id}` (same trace context propagated) to validate stock
   and price.
4. `checkout` opens a Postgres transaction (`db.transaction` span), inserts the order, decrements
   stock, commits.
5. `checkout` publishes an `orders.created` message to Redpanda (`kafka.publish` span) — this leg
   is *not* trace-linked into a consumer span, since Kafka message headers don't carry trace
   context yet (that would be a Layer 0 enhancement, not currently implemented).
6. `notifications` consumes the message from its `shopgrid-notifications` consumer group and logs a
   simulated confirmation.
7. Independently of all this, each of the four HTTP-serving Rust services streams its own traces,
   RED metrics, and structured logs to `otel-collector` over OTLP/gRPC every few seconds.
   `otel-collector` batches and writes them into ClickHouse. `trafficgen` only logs to its own
   stdout — it does not export telemetry.

## 5. The fault-injection control surface (dormant in Layer 0)

Every one of the four HTTP services (not trafficgen) mounts three endpoints, unauthenticated,
under its own port:

```
GET  /internal/fault         → current fault configuration (JSON)
POST /internal/fault         → replace fault configuration
POST /internal/fault/reset   → back to all-dormant
```

Nothing calls these yet — the scenario runner that will (Layer 2) doesn't exist. You can exercise
them manually right now, e.g. `curl -X POST http://localhost:8081/internal/fault -d '{"db_connection_leak": true}'`.

## 6. Known gap

Kafka messages are not trace-context-carrying, so a trace never shows a `notifications` span —
only `gateway`, `checkout`, and `catalog` appear in a given `TraceId`. This is a real limitation,
not a bug; closing it (via message headers) is a candidate Layer 0 follow-up, not required for the
Layer 0 exit criterion.
