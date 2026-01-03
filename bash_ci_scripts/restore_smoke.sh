#!/usr/bin/env bash
set -euo pipefail
echo "[restore_smoke] running pytest"
python -m pytest -q
