#!/usr/bin/env bash
set -euo pipefail

cd /app 2>/dev/null || cd /workspaces/bo

echo "[startup] Running Alembic migrations..."
alembic upgrade head

echo "[startup] Starting API server..."
exec uvicorn api_server:create_app --factory --host 0.0.0.0 --port 8000
