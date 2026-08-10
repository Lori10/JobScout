#!/usr/bin/env bash
# Starts the dashboard backend (uvicorn --reload) and frontend (vite dev
# server) together for local development, and tears both down on exit.
# Backend: http://127.0.0.1:8000/api/*   Frontend (with hot reload): http://localhost:5173
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -s "$HOME/.nvm/nvm.sh" ]; then
  . "$HOME/.nvm/nvm.sh"
fi

pids=()
cleanup() {
  for pid in "${pids[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
}
trap cleanup EXIT INT TERM

.venv/bin/python -m uvicorn jobscout.web.app:app \
  --reload --reload-dir jobscout \
  --host 127.0.0.1 --port 8000 &
pids+=("$!")

(cd frontend && npm run dev) &
pids+=("$!")

wait
