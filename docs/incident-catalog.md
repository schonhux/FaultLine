# Incident Catalog

v1 scenarios (Docker Compose, application-level injection). Each has exact ground truth,
expected symptoms, allowed/unsafe remediations, and recovery conditions in `scenarios/<id>/scenario.yaml`.

| # | Scenario | Root cause | Key distinguishing signals |
|---|---|---|---|
| 1 | db-pool-exhaustion | Leaked DB connections after bad deploy | Pool at max, normal DB CPU, traces stall before SQL span |
| 2 | redis-latency | Slow cache operations | Cache spans slow, DB load rises after cache timeouts |
| 3 | bad-deployment | Errors start immediately after version change | Only new version's requests fail; prior version healthy |
| 4 | kafka-lag | Consumer throughput collapse | Queue depth grows, producers healthy, notification delays |
| 5 | retry-storm | Request amplification on dependency failure | Traffic amplification with stable user traffic, large trace fan-out |
| 6 | expired-credentials | Auth failures at exact expiry time | Dependency reachable, repeated 401s starting at a precise timestamp |

v2 (deferred): memory leak, network partition (needs K8s overlay), DNS failure, inference
fallback failure, plus adversarial variants (misleading deployment, red-herring alert, missing
telemetry, conflicting evidence, multi-causal, stale runbook, unsafe easy fix).
