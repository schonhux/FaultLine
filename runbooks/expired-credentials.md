# Internal Service-to-Service Auth Failures

**Alert:** `catalog-auth-failures` -- repeated "invalid internal token" rejections.

## Symptom

One service starts rejecting essentially all calls from another with 401/auth-failure
responses, starting at a specific, identifiable moment. Connectivity itself is fine --
the calling service can reach the callee, and the callee is otherwise healthy for
other callers -- only authentication fails, and only for this one caller.

## Common causes

1. **An internal credential/token expired or was rotated** on one side without the
   other side being updated to match (a coordination gap between a token rotation and
   the services that depend on it).
2. **A deployment shipped with a stale or wrong credential** baked into config.
3. **Clock skew** causing token expiry checks to fail even though the token itself is
   correct (uncommon in a single local Compose network, but a real-world possibility
   worth naming).

## How to tell these apart

- `search_logs` on the callee for the exact rejection message and timestamp of the
  *first* occurrence -- this pinpoints the activation moment precisely.
- `get_recent_deployments` on both the caller and callee -- does a deploy line up with
  the first rejection, suggesting cause (2), or does the failure start with no
  corresponding deploy at all, suggesting cause (1) (a token simply expired on its own
  schedule)?
- Confirm it's *specifically* an auth failure and not a connectivity problem: the
  request should be reaching the callee at all (visible in its traces/logs) and being
  actively rejected, rather than timing out or refusing the connection.
- Check whether *other* callers of the same callee are unaffected -- if only one
  caller's requests fail, that isolates the problem to that specific
  caller/credential pair rather than the callee's auth system broadly.

## Remediation

Restarting the calling service can pick up a freshly-issued or corrected credential if
one is now available, which resolves the immediate symptom. If the credential was
baked into a bad deployment, rolling that deployment back is the more direct fix.
Neither the database nor unrelated infrastructure needs to be touched for this class
of incident -- it is specifically an application-level credential problem between two
named services.
