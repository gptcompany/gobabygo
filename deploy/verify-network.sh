#!/usr/bin/env bash
# verify-network.sh — Mesh network health checks
# Usage: ./verify-network.sh [router_url]
# Exit 0: all healthy, Exit 1: issues found
set -euo pipefail

ROUTER_URL="${1:-http://localhost:8780}"
PASS=0
FAIL=0
WARN=0

check() {
    local name="$1"
    shift
    if "$@" &>/dev/null; then
        echo "  [OK]   $name"
        ((PASS++)) || true
    else
        echo "  [FAIL] $name"
        ((FAIL++)) || true
    fi
}

warn_check() {
    local name="$1"
    shift
    if "$@" &>/dev/null; then
        echo "  [OK]   $name"
        ((PASS++)) || true
    else
        echo "  [WARN] $name"
        ((WARN++)) || true
    fi
}

echo "=== Mesh Network Health Check ==="
echo "Router URL: $ROUTER_URL"
echo ""

# 1. WireGuard tunnel
echo "--- WireGuard ---"
check "wg0 interface exists" ip link show wg0
warn_check "wg0 has peer" bash -c 'wg show wg0 2>/dev/null | grep -q peer'

# 2. Router reachability
echo ""
echo "--- Router ---"
check "Router reachable (GET /health)" curl -sf "${ROUTER_URL}/health" -o /dev/null
warn_check "Router has workers" bash -c "curl -sf ${ROUTER_URL}/health | python3 -c 'import sys,json; d=json.load(sys.stdin); sys.exit(0 if d[\"workers\"]>0 else 1)'"
warn_check "Queue depth < 100" bash -c "curl -sf ${ROUTER_URL}/health | python3 -c 'import sys,json; d=json.load(sys.stdin); sys.exit(0 if d[\"queue_depth\"]<100 else 1)'"

# 3. systemd services
echo ""
echo "--- Services ---"
router_systemd_active=0
router_docker_active=0

if systemctl list-unit-files mesh-router.service &>/dev/null 2>&1; then
    if systemctl is-active --quiet mesh-router.service; then
        router_systemd_active=1
        echo "  [OK]   mesh-router.service active"
        ((PASS++)) || true
    else
        echo "  [WARN] mesh-router.service inactive"
        ((WARN++)) || true
    fi
fi

if command -v docker &>/dev/null; then
    if docker inspect -f '{{.State.Running}}' mesh-router &>/dev/null; then
        router_docker_active=1
        echo "  [OK]   docker mesh-router active"
        ((PASS++)) || true
    fi
fi

if [ "$router_systemd_active" -eq 1 ] && [ "$router_docker_active" -eq 1 ]; then
    echo "  [FAIL] both router supervisors are active"
    ((FAIL++)) || true
elif [ "$router_systemd_active" -eq 0 ] && [ "$router_docker_active" -eq 0 ]; then
    echo "  [WARN] no local router supervisor detected"
    ((WARN++)) || true
fi

for svc in mesh-worker@claude-work mesh-worker@codex-work mesh-worker@gemini-work; do
    if systemctl list-unit-files "${svc}.service" &>/dev/null 2>&1; then
        warn_check "${svc} active" systemctl is-active --quiet "${svc}.service"
    fi
done

# 4. UFW (VPS only)
echo ""
echo "--- Firewall ---"
if command -v ufw &>/dev/null; then
    warn_check "UFW active" bash -c "ufw status | grep -q 'Status: active'"
    warn_check "Mesh port allowed on wg0" bash -c "ufw status | grep -q 8780"
else
    echo "  [SKIP] UFW not installed"
fi

# Summary
echo ""
echo "=== Results: ${PASS} passed, ${FAIL} failed, ${WARN} warnings ==="
[ "$FAIL" -eq 0 ]
