#!/usr/bin/env bash
# SMS-2 IDOS — intranet server launcher
# Usage: ./deploy/run.sh [port]   (default 8020)
set -e
cd "$(dirname "$0")/../backend"
PORT="${1:-8020}"
echo "SMS-2 IDOS starting on http://0.0.0.0:${PORT}  (Ctrl+C to stop)"
exec python3 -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT}" --workers 1
