#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${1:-http://localhost:8000}"
API_KEY="${API_SECRET_KEY:-}"

if ! command -v curl >/dev/null 2>&1; then
  echo "[smoke] curl not found" >&2
  exit 2
fi
if ! command -v jq >/dev/null 2>&1; then
  echo "[smoke] jq not found" >&2
  exit 2
fi

ok=0
fail=0

check() {
  local name="$1"
  local method="$2"
  local path="$3"
  local body="${4:-}"
  local expect_re='^(200|201|202|204|400|401|403|405|422|503)$'

  local url="${BASE_URL}${path}"
  local code
  if [[ -n "$body" ]]; then
    code=$(curl -sS -o /tmp/smoke_body.json -w "%{http_code}" -X "$method" "$url" \
      -H "Content-Type: application/json" \
      ${API_KEY:+-H "X-API-Key: ${API_KEY}"} \
      --data "$body" || true)
  else
    code=$(curl -sS -o /tmp/smoke_body.json -w "%{http_code}" -X "$method" "$url" \
      ${API_KEY:+-H "X-API-Key: ${API_KEY}"} || true)
  fi

  if [[ "$code" =~ $expect_re ]]; then
    echo "[PASS] $name $method $path -> $code"
    ok=$((ok + 1))
  else
    echo "[FAIL] $name $method $path -> $code"
    cat /tmp/smoke_body.json || true
    fail=$((fail + 1))
  fi
}

echo "[smoke] BASE_URL=${BASE_URL}"

check "health" GET "/health"
check "deriv_deep" GET "/health/deriv?timeout_seconds=6"
check "deriv_history" GET "/health/deriv/history?n=10"
check "status" GET "/status"
check "stats" GET "/stats"
check "balance" GET "/balance"
check "logs" GET "/logs?page=1&size=5"
check "audit_logs" GET "/audit/logs?page=1&size=5"
check "evolution_status" GET "/evolution/status"

# Validate key payloads quickly
curl -sS "${BASE_URL}/health/deriv?timeout_seconds=6" \
  | jq '{status, stage, token_present, broker_reachable, order_capable, latency_ms}' >/dev/null

echo "[smoke] ok=$ok fail=$fail"
if [[ $fail -gt 0 ]]; then
  exit 1
fi
