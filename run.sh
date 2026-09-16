#!/usr/bin/env bash
# Hardware Comps Dashboard — local boot helper.
#
# The FastAPI app object lives in app/main.py, so the correct module path is
# `app.main:app` (NOT `app:app`). This wraps the canonical boot command and makes
# it discoverable from the repo root.
#
# Usage:
#   ./run.sh                          # http://127.0.0.1:5000 (default)
#   PORT=8131 ./run.sh                # custom port
#   HOST=0.0.0.0 ./run.sh             # bind all interfaces
#
# Required env (see README): DATABASE_URL (Neon/PostgreSQL).
# Optional env: EBAY_CLIENT_ID, EBAY_CLIENT_SECRET, SITE_URL, and the admin
# email/password used to seed the login (see README).
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-5000}"
HOST="${HOST:-127.0.0.1}"

if [ ! -x ".venv/bin/python" ]; then
  echo "No .venv found — create it first with:  uv sync   (or: uv venv && uv pip install -e .)" >&2
  exit 1
fi

echo "Booting Hardware Comps Dashboard on http://$HOST:$PORT"
echo "  (uvicorn app.main:app — app object lives in app/main.py)"
exec .venv/bin/python -m uvicorn app.main:app --host "$HOST" --port "$PORT" "$@"