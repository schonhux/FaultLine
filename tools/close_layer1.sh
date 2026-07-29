#!/bin/bash
set -e

rm -f .git/index.lock

echo "=== Committing Layer 1 closeout on codex/layer-1-manual-incidents ==="
git checkout codex/layer-1-manual-incidents
git add platform/notifications/src/main.rs docs/layer1-manual-verification.md "FaultLine Build Log.docx" tools/create_build_log.py tools/push_layer0_layer1.sh tools/close_layer1.sh
git commit -m "Layer 1: fix notifications consumer timeout bug (kafka-lag was unobservable), correct fault API content-type in runbook, verify all 6 scenarios live. Layer 1 exit criterion met and closed."
git push origin codex/layer-1-manual-incidents

echo "=== Merging Layer 1 into main ==="
git checkout main
git pull origin main
git merge codex/layer-1-manual-incidents --no-edit
git push origin main

echo "=== Done. main now has Layers 0 and 1. ==="
git log --oneline -8
