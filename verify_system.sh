#!/bin/bash

set -euo pipefail

echo "Running compile check..."
python -m compileall .

echo "Running DB migrations..."
alembic upgrade head

echo "Running API..."
uvicorn api_server:create_app --factory --host 127.0.0.1 --port 8000 >/tmp/bo_verify_system.log 2>&1 &
server_pid=$!

cleanup() {
	if kill -0 "$server_pid" 2>/dev/null; then
		kill "$server_pid" 2>/dev/null || true
		wait "$server_pid" 2>/dev/null || true
	fi
}

trap cleanup EXIT

echo "Health check..."
for _ in $(seq 1 30); do
	if curl --silent --fail http://127.0.0.1:8000/health; then
		echo
		echo "DONE"
		exit 0
	fi
	sleep 1
done

echo "Health check failed. Server log:"
cat /tmp/bo_verify_system.log
exit 1

