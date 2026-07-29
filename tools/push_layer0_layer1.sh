#!/bin/bash
set -e

rm -f .git/index.lock

echo "=== Layer 0: fixture fixes ==="
git checkout codex/layer-0-instrumented-shopgrid
git add docker-compose.yml observability/otel-collector/config.yaml \
  platform/catalog/Dockerfile platform/checkout/Dockerfile platform/checkout/src/main.rs \
  platform/gateway/Dockerfile platform/notifications/Dockerfile platform/trafficgen/Dockerfile \
  docs/architecture/system-connectivity.md "FaultLine Build Log.docx" tools/create_build_log.py
git commit -m "Layer 0: fix Rust MSRV pins, redpanda entrypoint, otel-collector version and config, ClickHouse auth. Add checkout DB and Kafka spans and system-connectivity doc. Layer 0 exit criterion verified and closed."
git push origin codex/layer-0-instrumented-shopgrid

echo "=== Layer 1: manual verification runbook ==="
git checkout codex/layer-1-manual-incidents
git merge codex/layer-0-instrumented-shopgrid --no-edit
git add docs/layer1-manual-verification.md tools/push_layer0_layer1.sh
git commit -m "Layer 1: add manual fault-trigger verification runbook for all 6 scenarios."
git push origin codex/layer-1-manual-incidents

echo "=== Done. Both branches committed and pushed. ==="
git log --oneline -5
