#!/usr/bin/env bash
# One-shot local development setup (idempotent, safe to re-run after a fresh clone or a
# rebuilt sandbox):
#   1. Python virtualenv  (.venv)  + backend dependencies
#   2. backend/.env from backend/.env.example (never overwritten)
#   3. a headless Chromium build when Playwright's managed browser cannot be installed
#   4. database migrations
#   5. frontend dependencies (npm) when node is available and SKIP_FRONTEND is not set
#
# Usage:  ./scripts/dev_setup.sh            # full setup
#         SKIP_CHROMIUM=1 ./scripts/dev_setup.sh
#         SKIP_FRONTEND=1 ./scripts/dev_setup.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

log() { printf '\033[1;34m[dev-setup]\033[0m %s\n' "$*"; }

# 1. virtualenv + dependencies ----------------------------------------------------------
if [ ! -x .venv/bin/python ]; then
  log "creating virtualenv .venv"
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --quiet --upgrade pip
log "installing backend dependencies"
pip install --quiet -r backend/requirements-dev.txt

# 2. environment file -------------------------------------------------------------------
if [ ! -f backend/.env ]; then
  log "creating backend/.env from backend/.env.example"
  cp backend/.env.example backend/.env
fi

# 3. browser ----------------------------------------------------------------------------
if [ "${SKIP_CHROMIUM:-0}" != "1" ]; then
  if ! (cd backend && python -c "from app.browser.launcher import browser_available as b; import sys; sys.exit(0 if b() else 1)" 2>/dev/null); then
    log "no Chromium found - trying 'playwright install chromium' first"
    if ! python -m playwright install chromium >/dev/null 2>&1; then
      log "Playwright CDN unreachable - downloading the self-contained @sparticuz/chromium build"
      pip install --quiet brotli
      BIN="$(python scripts/fetch_chromium.py | tail -n 1)"
      if ! grep -q '^CHROMIUM_EXECUTABLE_PATH=' backend/.env; then
        printf '\nCHROMIUM_EXECUTABLE_PATH=%s\n' "$BIN" >> backend/.env
        log "added CHROMIUM_EXECUTABLE_PATH to backend/.env"
      fi
    fi
  else
    log "Chromium already available"
  fi
fi

# 4. database ---------------------------------------------------------------------------
log "running database migrations"
(cd backend && mkdir -p data && alembic upgrade head >/dev/null)

# 5. frontend ---------------------------------------------------------------------------
if [ "${SKIP_FRONTEND:-0}" != "1" ] && command -v npm >/dev/null 2>&1 && [ -f frontend/package.json ]; then
  if [ ! -d frontend/node_modules ]; then
    log "installing frontend dependencies"
    (cd frontend && npm install --no-audit --no-fund --loglevel=error)
  else
    log "frontend dependencies already installed"
  fi
fi

log "done. Start the API with:  ./scripts/dev_api.sh   (http://localhost:8000/api/docs)"
