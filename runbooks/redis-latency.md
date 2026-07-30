# Catalog Cache Latency

**Alert:** `catalog-latency-high` -- catalog's average request duration crosses
threshold over a short rolling window.

## Symptom

Catalog request latency rises sharply and stays elevated, while the request *volume*
and error rate are unremarkable. The added time shows up specifically on
cache-touching operations, not database work.

## Common causes

1. **Cache backend degraded or slow.** Redis itself is under load, network latency to
   it increased, or an operation pattern (e.g. large values, hot keys) got slower.
2. **Cache miss storm.** A deploy or TTL change caused the cache to go cold, so
   catalog is falling through to Postgres on nearly every request. This looks similar
   at first glance but the *database* spans, not the cache spans, would be the slow
   ones.
3. **Network-level latency** between catalog and its cache (less likely in a
   single-Compose-network deployment, but possible if the cache moved).

## How to tell these apart

- Use `get_trace` on a slow catalog request and look at which span is actually slow:
  a Redis/cache-labeled span vs. a Postgres-labeled span. This one distinction
  separates cause (1)/(3) from cause (2).
- Check whether the added latency scales with anything (deploy time, traffic volume)
  via `get_recent_deployments` and a `query_metrics` series -- a step-function jump in
  latency with no corresponding deploy or traffic change points at the cache backend
  itself rather than an application change.
- `search_logs` on catalog for cache-related warnings or connection errors.

## Remediation

If the cache's data looks stale or poisoned, clearing catalog's cache namespace is a
low-risk, targeted fix. A full service restart of catalog also clears its in-process
state and is reasonable if the namespace-level clear doesn't help. Restarting the
cache backend itself (Redis) is a much bigger hammer than this class of incident
usually warrants -- prefer the narrower fix first.
