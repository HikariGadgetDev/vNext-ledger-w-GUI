#!/usr/bin/env bash
set -euo pipefail
echo "[ci_smoke] running pytest"
python -m pytest -q
