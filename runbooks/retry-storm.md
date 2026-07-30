# Retry Amplification Against a Flaky Dependency

**Alert:** `checkout-retry-fanout-high` -- dependency calls per request far above
baseline.

## Symptom

The number of outbound dependency calls per inbound request rises well above normal
(baseline is one call out per request in), even though user-facing traffic volume is
unchanged. The effect is that a service's *effective* load on its dependencies can
multiply several times over without any real increase in end-user demand.

## Common causes

1. **A downstream dependency started failing intermittently, and the caller retries
   aggressively** (no backoff, or a retry budget that's too generous) on every
   failure. This is a feedback loop: retries add load to an already-struggling
   dependency, which fails more, which triggers more retries.
2. **A retry policy change shipped separately from any dependency issue** -- e.g. a
   deploy that increased max-retries or removed backoff, which would show elevated
   fan-out even against a healthy dependency.
3. **Genuine increased traffic** to the dependency from a source other than the
   retries (ruled out if user-facing request volume into the caller is flat).

## How to tell these apart

- Compare the caller's inbound request volume (should be flat) against its outbound
  dependency-call volume (elevated) -- the gap between them is the amplification.
- Check the dependency's own error rate over the same window. If it's elevated too,
  the retries are very likely reacting to real failures (cause 1) rather than being
  gratuitous (cause 2).
- `get_recent_deployments` on the calling service -- a retry-policy change would
  correlate with the fan-out starting, independent of the dependency's health.
- `get_trace` on a slow or fanned-out request to see the repeated dependency-call
  spans directly and confirm they're retries of the same logical call, not distinct
  legitimate calls.

## Remediation

If the dependency is genuinely unhealthy, restarting *it* may resolve the underlying
failures the retries are reacting to; restarting the caller can also help if it clears
a runaway retry loop or bad in-memory state. Scaling out either service is not an
appropriate response to a retry-amplification pattern -- it adds capacity to a
problem that's caused by call *behavior*, not by insufficient capacity, and can make
the load multiplication worse, not better.
