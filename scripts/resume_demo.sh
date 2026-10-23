#!/usr/bin/env bash
# Recorded interruption/resume demonstration on the FULL corpus (real model calls).
set -euo pipefail
cd "$(dirname "$0")/.."
FULL=data/spotify_reviews_18months.csv
echo "== 1. start the full run (phase=initial, 4 workers) and interrupt it with SIGINT after saved progress"
python3 -m pipeline run --input $FULL --state state/main --stages ingest,enrich --workers 4 --phase initial --run-id full-initial-sync &
PID=$!
sleep ${INTERRUPT_AFTER:-150}
echo "== sending SIGINT to $PID"; kill -INT $PID; wait $PID || true
echo "== 2. snapshot completed IDs before resume"
python3 -m pipeline checkpoint --state state/main --input $FULL --out grading/checkpoint_before.json
echo "== 3. resume (phase=resume): only pending texts are dispatched"
python3 -m pipeline run --input $FULL --state state/main --stages enrich --workers 4 --phase resume --max-batches ${RESUME_BATCHES:-24} --run-id full-resume-sync
echo "== 4. snapshot after resume and check no completed ID was re-sent"
python3 -m pipeline checkpoint --state state/main --input $FULL --out grading/checkpoint_after.json
python3 evals/run_evals.py resume-check --state state/main
