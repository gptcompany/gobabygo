#!/usr/bin/env bash
# deploy-router.sh — Deploy mesh router to VPS via SSH
# Usage: ./deploy-router.sh <auth_token> [--dry-run]
#
# Prereqs: SSH access to root@10.0.0.1 (publickey)
set -euo pipefail

VPS_HOST="root@10.0.0.1"
MESH_PORT=8780
TOKEN="${1:?Usage: $0 <auth_token> [--dry-run]}"
DRY_RUN="${2:-}"
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if [[ "$DRY_RUN" == "--dry-run" ]]; then
    echo "[DRY-RUN] Would deploy router to ${VPS_HOST}"
    echo "[DRY-RUN] Token: ${TOKEN:0:8}..."
    echo "[DRY-RUN] Steps: uv install, mesh user, rsync, venv, env, systemd"
    exit 0
fi

echo "=== Deploying Mesh Router to VPS (${VPS_HOST}) ==="

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
export PATH="/root/.local/bin:$PATH"
cd /opt/mesh-router
# Create venv as mesh user (uv needs to be accessible)
if [ ! -d venv ]; then
    uv venv venv --python python3
fi
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
