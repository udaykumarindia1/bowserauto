#!/usr/bin/env bash
# Start the API (with the embedded worker) for local development.
#   ./scripts/dev_api.sh            -> http://0.0.0.0:8000  (logs to data/api.log as well as stdout)
#   PORT=9000 ./scripts/dev_api.sh
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/backend"
# shellcheck disable=SC1091
source "$ROOT/.venv/bin/activate"
mkdir -p data
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --log-level "${UVICORN_LOG_LEVEL:-warning}" 2>&1 | tee -a data/api.log
