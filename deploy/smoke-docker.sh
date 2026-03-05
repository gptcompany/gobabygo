#!/usr/bin/env bash
# smoke-docker.sh — Smoke test for Docker router
# Usage: ./smoke-docker.sh [router_url]
set -euo pipefail

ROUTER_URL="${1:-http://localhost:8780}"
PASS=0
FAIL=0

check() {
    local name="$1"; shift
    if "$@" &>/dev/null; then
        echo "  [OK]   $name"
        ((PASS++))
    else
        echo "  [FAIL] $name"
        ((FAIL++))
    fi
}

echo "=== Docker Router Smoke Test ==="
echo "URL: $ROUTER_URL"
echo ""

# 1. Container running
echo "--- Container ---"
check "mesh-router container running" docker inspect -f '{{.State.Running}}' mesh-router

# 2. Health endpoint
echo ""
echo "--- Health ---"
check "GET /health returns 200" curl -sf "${ROUTER_URL}/health" -o /dev/null
check "/health has valid JSON" bash -c "curl -sf ${ROUTER_URL}/health | python3 -c 'import sys,json; json.load(sys.stdin)'"

# 3. Metrics endpoint
echo ""
echo "--- Metrics ---"
check "GET /metrics returns 200" curl -sf "${ROUTER_URL}/metrics" -o /dev/null

# 4. Docker healthcheck status
echo ""
echo "--- Docker Healthcheck ---"
check "healthcheck passing" bash -c "docker inspect mesh-router | python3 -c \"import sys,json; d=json.load(sys.stdin)[0]; sys.exit(0 if d['State']['Health']['Status']=='healthy' else 1)\""

# 5. Workers connected (warning only)
echo ""
echo "--- Workers ---"
WORKERS=$(curl -sf "${ROUTER_URL}/health" 2>/dev/null | python3 -c 'import sys,json; print(json.load(sys.stdin).get("workers",0))' 2>/dev/null || echo "0")
if [ "$WORKERS" -gt 0 ]; then
    echo "  [OK]   $WORKERS worker(s) connected"
    ((PASS++))
else
    echo "  [WARN] No workers connected (start systemd workers)"
fi

# Summary
echo ""
echo "=== Results: ${PASS} passed, ${FAIL} failed ==="
[ "$FAIL" -eq 0 ]
