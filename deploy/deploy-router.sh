#!/usr/bin/env bash
# deploy-router.sh — Deploy mesh router to VPS via SSH
# Usage: ./deploy-router.sh <auth_token> [--supervisor docker|systemd] [--dry-run]
#
# Prereqs: SSH access to root@10.0.0.1 (publickey)
set -euo pipefail

VPS_HOST="root@10.0.0.1"
MESH_PORT=8780
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TOKEN=""
ROUTER_SUPERVISOR="${MESH_ROUTER_SUPERVISOR:-docker}"
DRY_RUN=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)
            DRY_RUN=1
            ;;
        --supervisor)
            ROUTER_SUPERVISOR="${2:?missing value for --supervisor}"
            shift
            ;;
        --supervisor=*)
            ROUTER_SUPERVISOR="${1#*=}"
            ;;
        *)
            if [[ -z "$TOKEN" ]]; then
                TOKEN="$1"
            else
                echo "Usage: $0 <auth_token> [--supervisor docker|systemd] [--dry-run]" >&2
                exit 2
            fi
            ;;
    esac
    shift
done

TOKEN="${TOKEN:?Usage: $0 <auth_token> [--supervisor docker|systemd] [--dry-run]}"

if [[ "$ROUTER_SUPERVISOR" != "docker" && "$ROUTER_SUPERVISOR" != "systemd" ]]; then
    echo "ERROR: unsupported router supervisor '${ROUTER_SUPERVISOR}' (expected docker|systemd)" >&2
    exit 2
fi

if [[ "$DRY_RUN" == "1" ]]; then
    echo "[DRY-RUN] Would deploy router to ${VPS_HOST} using supervisor=${ROUTER_SUPERVISOR}"
    echo "[DRY-RUN] Token: ${TOKEN:0:8}..."
    if [[ "$ROUTER_SUPERVISOR" == "docker" ]]; then
        echo "[DRY-RUN] Steps: rsync, compose env, stop systemd router, docker compose up router"
    else
        echo "[DRY-RUN] Steps: uv install, mesh user, rsync, venv, env, systemd"
    fi
    exit 0
fi

echo "=== Deploying Mesh Router to VPS (${VPS_HOST}) with supervisor=${ROUTER_SUPERVISOR} ==="

if [[ "$ROUTER_SUPERVISOR" == "docker" ]]; then
    echo "[1/5] Creating directories..."
    ssh "$VPS_HOST" 'mkdir -p /var/lib/mesh-router /etc/mesh-router/config /opt/mesh-router'
    echo "  dirs: OK"

    echo "[2/5] Syncing source code..."
    rsync -az --delete \
        --exclude='__pycache__' \
        --exclude='.git' \
        --exclude='venv' \
        --exclude='tests' \
        --exclude='.claude*' \
        --exclude='.swarm' \
        --exclude='.planning' \
        --exclude='kiss_mesh' \
        "${PROJECT_ROOT}/src" \
        "${PROJECT_ROOT}/scripts" \
        "${PROJECT_ROOT}/deploy" \
        "${PROJECT_ROOT}/pyproject.toml" \
        "${PROJECT_ROOT}/uv.lock" \
        "${PROJECT_ROOT}/schemas" \
        "$VPS_HOST":/opt/mesh-router/
    echo "  rsync: OK"

    echo "[3/5] Configuring compose env..."
    ENV_CONTENT=$(sed "s/__REPLACE_WITH_TOKEN__/${TOKEN}/" "${PROJECT_ROOT}/deploy/compose.env.example")
    ssh "$VPS_HOST" bash -c "cat > /etc/mesh-router/compose.env << 'ENVEOF'
${ENV_CONTENT}
ENVEOF
chmod 640 /etc/mesh-router/compose.env"
    if ssh "$VPS_HOST" '[ ! -f /etc/mesh-router/mesh-matrix-bridge.docker.env ]'; then
        scp -q "${PROJECT_ROOT}/deploy/mesh-matrix-bridge.docker.env" \
            "$VPS_HOST":/etc/mesh-router/mesh-matrix-bridge.docker.env
        ssh "$VPS_HOST" 'chmod 640 /etc/mesh-router/mesh-matrix-bridge.docker.env'
    fi
    echo "  compose env: OK"

    echo "[4/5] Stopping legacy systemd router if present..."
    ssh "$VPS_HOST" 'systemctl stop mesh-router 2>/dev/null || true'
    echo "  systemd router stopped"

    echo "[5/5] Starting docker router..."
    ssh "$VPS_HOST" 'cd /opt/mesh-router && ./deploy/live-compose.sh up -d --build router'
    sleep 2

    echo ""
    echo "=== Verifying ==="
    if curl -sf "http://10.0.0.1:${MESH_PORT}/health" -o /dev/null --connect-timeout 5; then
        echo "  /health: OK"
        curl -s "http://10.0.0.1:${MESH_PORT}/health" | python3 -m json.tool
    else
        echo "  /health: FAILED (service may still be starting)"
        echo "  Check with: ssh ${VPS_HOST} 'cd /opt/mesh-router && ./deploy/live-compose.sh ps'"
    fi

    echo ""
    echo "=== Router docker deploy complete ==="
    echo "  URL: http://10.0.0.1:${MESH_PORT}"
    echo "  Logs: ssh ${VPS_HOST} 'cd /opt/mesh-router && ./deploy/live-compose.sh logs -f router'"
    exit 0
fi

# 1. Install uv on VPS (if not present)
echo "[1/8] Checking uv..."
ssh "$VPS_HOST" 'command -v uv &>/dev/null || (curl -LsSf https://astral.sh/uv/install.sh | sh && echo "export PATH=\$HOME/.local/bin:\$PATH" >> /root/.bashrc)'
echo "  uv: OK"

# 2. Create mesh user (system, no-login)
echo "[2/8] Creating mesh user..."
ssh "$VPS_HOST" 'id mesh &>/dev/null || useradd --system --no-create-home --shell /usr/sbin/nologin mesh'
echo "  user mesh: OK"

# 3. Create directories
echo "[3/8] Creating directories..."
ssh "$VPS_HOST" 'mkdir -p /var/lib/mesh-router /etc/mesh-router /opt/mesh-router && chown mesh:mesh /var/lib/mesh-router /opt/mesh-router'
echo "  dirs: OK"

# 4. Rsync source code
echo "[4/8] Syncing source code..."
rsync -az --delete \
    --exclude='__pycache__' \
    --exclude='.git' \
    --exclude='venv' \
    --exclude='tests' \
    --exclude='.claude*' \
    --exclude='.swarm' \
    --exclude='.planning' \
    --exclude='kiss_mesh' \
    "${PROJECT_ROOT}/src" \
    "${PROJECT_ROOT}/pyproject.toml" \
    "${PROJECT_ROOT}/schemas" \
    "$VPS_HOST":/opt/mesh-router/
ssh "$VPS_HOST" 'chown -R mesh:mesh /opt/mesh-router'
echo "  rsync: OK"

# 5. Create venv and install dependencies
echo "[5/8] Installing dependencies..."
ssh "$VPS_HOST" bash -s <<'REMOTE_INSTALL'
set -euo pipefail
export PATH="/root/.local/bin:$PATH"
cd /opt/mesh-router
# Remove stale venv to avoid cached build artifacts
rm -rf venv
uv venv venv --python python3
uv pip install -e . --python venv/bin/python
chown -R mesh:mesh /opt/mesh-router
REMOTE_INSTALL
echo "  venv + deps: OK"

# 6. Copy env file with real token
echo "[6/8] Configuring env..."
# Build env content with real token
ENV_CONTENT=$(sed "s/__REPLACE_WITH_TOKEN__/${TOKEN}/" "${PROJECT_ROOT}/deploy/mesh-router.env")
ssh "$VPS_HOST" bash -c "cat > /etc/mesh-router/mesh-router.env << 'ENVEOF'
${ENV_CONTENT}
ENVEOF
chown mesh:mesh /etc/mesh-router/mesh-router.env
chmod 600 /etc/mesh-router/mesh-router.env"
echo "  env: OK (token set)"

# 7. Install systemd unit
echo "[7/8] Installing systemd service..."
scp -q "${PROJECT_ROOT}/deploy/mesh-router.service" "$VPS_HOST":/etc/systemd/system/mesh-router.service
ssh "$VPS_HOST" 'systemctl daemon-reload && systemctl enable mesh-router.service'
echo "  systemd: OK"

# 8. Start and verify
echo "[8/8] Starting mesh-router..."
ssh "$VPS_HOST" 'systemctl restart mesh-router'
sleep 2

echo ""
echo "=== Verifying ==="
if curl -sf "http://10.0.0.1:${MESH_PORT}/health" -o /dev/null --connect-timeout 5; then
    echo "  /health: OK"
    curl -s "http://10.0.0.1:${MESH_PORT}/health" | python3 -m json.tool
else
    echo "  /health: FAILED (service may still be starting)"
    echo "  Check with: ssh ${VPS_HOST} journalctl -u mesh-router -n 20"
fi

echo ""
echo "=== Router deploy complete ==="
echo "  URL: http://10.0.0.1:${MESH_PORT}"
echo "  Logs: ssh ${VPS_HOST} journalctl -u mesh-router -f"
