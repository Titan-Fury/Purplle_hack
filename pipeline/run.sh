#!/usr/bin/env bash
# One command to process all clips of a store into an event stream.
# Usage:  ./pipeline/run.sh [STORE_ID] [API_URL]
#   ./pipeline/run.sh                      -> batch, default store, JSONL only
#   ./pipeline/run.sh STORE_BLR_002 http://localhost:8000   -> live stream to API
set -euo pipefail

STORE="${1:-STORE_BLR_002}"
API_URL="${2:-}"
LAYOUT="${STORE_LAYOUT_PATH:-data/store_layout.json}"
OUT="pipeline/output/events_${STORE}.jsonl"

ARGS=(--store "$STORE" --layout "$LAYOUT" --out "$OUT")
if [[ -n "$API_URL" ]]; then
  ARGS+=(--api-url "$API_URL" --realtime)
fi

python -m pipeline.run "${ARGS[@]}"
echo "Events written to ${OUT}"
