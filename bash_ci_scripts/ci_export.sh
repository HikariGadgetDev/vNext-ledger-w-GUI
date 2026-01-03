#!/usr/bin/env bash
set -euo pipefail
BASE="${BASE:-http://127.0.0.1:8000}"
REPORT_DATE="${REPORT_DATE:-$(date +%F)}"
OUT="${OUT:-reports/${REPORT_DATE}}"
mkdir -p "$OUT"
log(){ echo "[ci_export] $*"; }
curl -fsS "$BASE/scan?full=0" -H "Content-Type: application/json" -d '{}' -o "$OUT/scan.json"
curl -fsS "$BASE/export/summary" -o "$OUT/summary.json"
curl -fsS "$BASE/export/notes" -o "$OUT/notes.json"
curl -fsS "$BASE/export/metrics?limit=50" -o "$OUT/metrics.json"
