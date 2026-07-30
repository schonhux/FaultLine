# Elevated Error Rate Following a Deployment

**Alert:** `catalog-error-rate-high` -- 5xx rate crosses threshold.

## Symptom

A service's error rate (5xx responses) jumps from near-zero to a sustained elevated
rate. The defining characteristic of *this* pattern specifically is a sharp step, not
a gradual climb -- error rate before some point in time is normal, and after that
point it is consistently high.

## Common causes

1. **A bad deployment.** New code shipped with a bug that fails a fraction of
   requests. The step in error rate lines up almost exactly with a deployment
   timestamp.
2. **A dependency degrading independently**, coincidentally close in time to an
   unrelated deploy. Rarer, but worth ruling out before assuming the deploy is at
   fault just because it's the most recent change.
3. **Config drift** shipped alongside a deploy (feature flag, rate limit, timeout
   value) rather than a code bug per se.

## How to tell these apart

- `get_recent_deployments` for the affected service, then compare the deploy
  timestamp against the error-rate step precisely. If requests *before* the
  deployment marker are clean and requests *after* it are failing, that is strong
  evidence for cause (1) -- do not just note that a deploy happened recently, confirm
  the timing lines up.
- `find_traces` filtered to `status=error` for the affected service to see the actual
  failure mode (which span fails, what status code, any error message in
  `StatusMessage`).
- `search_logs` on the affected service around the deployment timestamp for stack
  traces or explicit error messages that point at what changed.
- Check whether *upstream* dependencies of the affected service are also erroring
  (cause 2) versus only the deployed service itself (cause 1).

## Remediation

If the failures started at a specific deployment and no config or dependency
explanation fits better, rolling back that deployment is the direct fix -- it removes
the change that caused the regression rather than papering over the symptom. A
service restart alone will not help if the currently-running code is the bad version;
it will keep failing at the same rate immediately after restart.
