# Checkout Connection Pool Exhaustion

**Alert:** `checkout-pool-exhausted` -- `db.pool.active` pinned near `pool_max`.

## Symptom

Checkout's database connection pool saturates: `db.pool.active` climbs to (and stays
at) its configured maximum while `db.pool.idle` drops to zero. Requests that need a
connection start queuing or timing out. This is a resource-exhaustion pattern, not a
database-health pattern -- Postgres itself is typically fine.

## Common causes, roughly in order of likelihood

1. **Connection leak.** A recent code change stopped returning connections to the pool
   on some code path (often an early-return or error branch that skips the `defer`/
   `finally` release). Pool usage climbs monotonically and never drops, even when
   traffic is flat.
2. **Genuine load increase.** Traffic grew faster than the pool was sized for. Usage
   should track request volume and fluctuate with it, not climb monotonically at
   constant traffic.
3. **A slow downstream query.** Connections are being held longer than usual because
   the queries running on them got slower (lock contention, missing index, a bad
   migration). Usage rises with query latency, not necessarily with request volume.

## How to tell these apart

- Check `db.pool.active` as a *series* (`query_metrics` with `bucket_seconds` set),
  not just a point-in-time value. A leak looks like a ramp that never comes back down
  even during quiet periods; load-driven usage tracks traffic and can recede.
- Pull `get_recent_deployments` for `checkout`. A leak introduced by a bad release
  will correlate almost exactly with the pool starting to climb.
- Compare `db.pool.active` to actual request rate/volume for checkout over the same
  window. If the pool is climbing while request volume is flat or falling, that's a
  leak, not load.
- A slow-query cause should also show up as elevated span duration on checkout's
  database-touching spans in `find_traces` / `get_trace` -- a leak by itself does not
  necessarily make individual requests slower until the pool is nearly exhausted.

## Remediation

A rollback of the offending deployment is the correct fix if a recent release
introduced the leak -- it addresses the root cause, not just the symptom. A service
restart clears the immediate symptom (returns the pool to empty) but does **not** fix
a leak in the running code; if the leaking version is still deployed, the pool will
fill right back up. Restarting the database itself does not help -- the database is
not the resource under pressure, the application's connection pool is.
