#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${1:-http://localhost:8000}"
API_FILE="${2:-frontend/lib/api.ts}"
API_KEY="${API_SECRET_KEY:-}"

if ! command -v curl >/dev/null 2>&1; then
  echo "[conn] curl not found" >&2
  exit 2
fi
if ! command -v jq >/dev/null 2>&1; then
  echo "[conn] jq not found" >&2
  exit 2
fi
if [[ ! -f "$API_FILE" ]]; then
  echo "[conn] cannot find $API_FILE" >&2
  exit 2
fi

tmp_map="$(mktemp)"

# Map frontend api calls -> method + path by parsing frontend/lib/api.ts
awk '
  /=>[[:space:]]*request/ {
    inb=1; method="GET"; path="";
  }
  inb {
    if (match($0, /request(<[^>]+>)?\((`|")([^`"]+)/, m)) {
      path=m[3];
    }
    if ($0 ~ /method:[[:space:]]*"POST"/) method="POST";
    if ($0 ~ /\),?[[:space:]]*$/ || $0 ~ /},?[[:space:]]*$/) {
      if (path != "") print method "\t" path;
      inb=0;
    }
  }
' "$API_FILE" \
  | sed -E 's/\$\{[^}]+\}/1/g' \
  | awk -F'\t' '{split($2, a, "?"); print $1"\t"a[1]}' \
  | awk -F'\t' '!seen[$1 FS $2]++' \
  > "$tmp_map"

if [[ ! -s "$tmp_map" ]]; then
  echo "[conn] no endpoints parsed from $API_FILE" >&2
  exit 2
fi

# Optional: compare with backend OpenAPI route list
backend_routes="$(mktemp)"
openapi_code=$(curl -sS -o /tmp/openapi.json -w "%{http_code}" "${BASE_URL}/openapi.json" || true)
if [[ "$openapi_code" == "200" ]] && jq -e . /tmp/openapi.json >/dev/null 2>&1; then
  jq -r '.paths | to_entries[] | .key' /tmp/openapi.json > "$backend_routes" 2>/dev/null || true
else
  : > "$backend_routes"
fi

ok=0
fail=0

echo "[conn] BASE_URL=${BASE_URL}"

while IFS=$'\t' read -r method path; do
  [[ -z "$path" ]] && continue
  url="${BASE_URL}${path}"

  if [[ "$method" == "POST" ]]; then
    code=$(curl -sS -o /tmp/conn_body.json -w "%{http_code}" -X POST "$url" \
      -H "Content-Type: application/json" \
      ${API_KEY:+-H "X-API-Key: ${API_KEY}"} \
      --data '{}' || true)
  else
    code=$(curl -sS -o /tmp/conn_body.json -w "%{http_code}" -X GET "$url" \
      ${API_KEY:+-H "X-API-Key: ${API_KEY}"} || true)
  fi

  if [[ "$code" =~ ^(200|201|202|204|400|401|403|405|422|503)$ ]]; then
    echo "[PASS] $method $path -> $code"
    ok=$((ok + 1))
  else
    echo "[FAIL] $method $path -> $code"
    fail=$((fail + 1))
  fi

  if [[ -s "$backend_routes" ]] && ! grep -Fxq "$path" "$backend_routes"; then
    echo "[WARN] frontend path not in openapi: $path"
  fi
done < "$tmp_map"

echo "[conn] ok=$ok fail=$fail"
if [[ $fail -gt 0 ]]; then
  exit 1
fi
