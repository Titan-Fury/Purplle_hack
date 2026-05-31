#!/usr/bin/env sh
# Process all CCTV clips of a store through the detection pipeline -> structured events.
#
#   ./pipeline/run.sh                      # store ST1008 -> pipeline/output/events_ST1008.jsonl
#   ./pipeline/run.sh STORE_ID             # a different registered store
#   ./pipeline/run.sh STORE_ID http://localhost:8000   # also stream events live into the API
#
# Output JSONL location is printed at the end. Requires the pipeline deps
# (pip install -r requirements.txt ; plus torch/ultralytics/torchreid for detection).
set -e
STORE="${1:-ST1008}"
API="${2:-}"
OUT="pipeline/output/events_${STORE}.jsonl"
mkdir -p pipeline/output

CMD="python -m pipeline.run --store ${STORE} --layout data/store_layout.json --out ${OUT}"
if [ -n "${API}" ]; then
  CMD="${CMD} --api-url ${API} --realtime"
fi

echo "[run.sh] ${CMD}"
exec ${CMD}
