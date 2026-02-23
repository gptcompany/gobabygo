#!/usr/bin/env bash
# deploy-workers.sh — Deploy mesh workers locally on Workstation
# Usage: sudo ./deploy-workers.sh <auth_token> [--dry-run]
#
# Runs with sudo on Workstation (10.0.0.2 / 192.168.1.111)
set -euo pipefail

TOKEN="${1:?Usage: sudo $0 <auth_token> [--dry-run]}"
DRY_RUN="${2:-}"
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

WORKERS=("claude-work" "codex-work" "gemini-work")

if [[ "$DRY_RUN" == "--dry-run" ]]; then
    echo "[DRY-RUN] Would deploy ${#WORKERS[@]} workers locally"
    echo "[DRY-RUN] Token: ${TOKEN:0:8}..."
    echo "[DRY-RUN] Workers: ${WORKERS[*]}"
    echo "[DRY-RUN] Steps: mesh-worker user, copy code, venv, env files, systemd"
    exit 0
fi

if [[ $EUID -ne 0 ]]; then
    echo "ERROR: This script must be run as root (use sudo)"
    exit 1
fi

echo "=== Deploying Mesh Workers (local) ==="

# 1. Create service user
echo "[1/7] Creating mesh-worker user..."
# Ensure mesh group exists (shared with router service unit)
groupadd -f mesh
if ! id mesh-worker &>/dev/null; then
    useradd --system --create-home --shell /bin/bash -g mesh-worker -G mesh,sam mesh-worker
    echo "  Created user: mesh-worker (groups: mesh-worker, mesh, sam)"
else
    usermod -aG mesh,sam mesh-worker 2>/dev/null || true
    echo "  User mesh-worker already exists (groups updated)"
fi

# 2. Create directories
echo "[2/7] Creating directories..."
mkdir -p /etc/mesh-worker /opt/mesh-worker /home/mesh-worker/.mesh/agents /tmp/mesh-tasks
chown -R mesh-worker:mesh-worker /opt/mesh-worker /home/mesh-worker/.mesh /tmp/mesh-tasks
echo "  dirs: OK"

# 3. Copy source code
echo "[3/7] Copying source code..."
# Clean destination first
rm -rf /opt/mesh-worker/src /opt/mesh-worker/pyproject.toml /opt/mesh-worker/schemas
for item in src pyproject.toml schemas; do
    if [ -e "${PROJECT_ROOT}/${item}" ]; then
        cp -r "${PROJECT_ROOT}/${item}" /opt/mesh-worker/
    fi
done
chown -R mesh-worker:mesh-worker /opt/mesh-worker
echo "  source: OK"

# 4. Create venv and install deps
echo "[4/7] Installing dependencies..."
cd /opt/mesh-worker

# Check Python version — pyproject.toml requires >=3.11
PYTHON_BIN=""
for candidate in python3.12 python3.11 python3; do
    if command -v "$candidate" &>/dev/null; then
        PY_VER=$("$candidate" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
        PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)
        if [ "$PY_MAJOR" -ge 3 ] && [ "$PY_MINOR" -ge 11 ]; then
            PYTHON_BIN="$candidate"
            echo "  Using $candidate ($PY_VER)"
            break
        fi
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    echo "WARNING: No Python >=3.11 found. Attempting uv with managed Python..."
    PYTHON_BIN="python3"
fi

if command -v uv &>/dev/null; then
    if [ ! -d venv ]; then
        sudo -u mesh-worker uv venv venv --python "$PYTHON_BIN"
    fi
    sudo -u mesh-worker uv pip install -e . --python venv/bin/python
else
    echo "WARNING: uv not found, falling back to pip..."
    if [ ! -d venv ]; then
        sudo -u mesh-worker "$PYTHON_BIN" -m venv venv
    fi
    sudo -u mesh-worker venv/bin/pip install -e .
fi
chown -R mesh-worker:mesh-worker /opt/mesh-worker
echo "  venv + deps: OK"

# 5. Copy env files with real token
echo "[5/7] Configuring env files..."
for worker in "${WORKERS[@]}"; do
    SRC_ENV="${PROJECT_ROOT}/deploy/mesh-worker-${worker}.env"
    DST_ENV="/etc/mesh-worker/${worker}.env"

    if [ ! -f "$SRC_ENV" ]; then
        echo "  WARNING: ${SRC_ENV} not found, skipping"
        continue
    fi

    sed "s/__REPLACE_WITH_TOKEN__/${TOKEN}/" "$SRC_ENV" > "$DST_ENV"
    chown mesh-worker:mesh-worker "$DST_ENV"
    chmod 600 "$DST_ENV"
    echo "  ${worker}.env: OK"
done

# 6. Install systemd template unit
echo "[6/7] Installing systemd service..."
cp "${PROJECT_ROOT}/deploy/mesh-worker@.service" /etc/systemd/system/mesh-worker@.service
systemctl daemon-reload

for worker in "${WORKERS[@]}"; do
    systemctl enable "mesh-worker@${worker}.service"
    echo "  enabled: mesh-worker@${worker}"
done
echo "  systemd: OK"

# 7. Start workers
echo "[7/7] Starting workers..."
for worker in "${WORKERS[@]}"; do
    systemctl restart "mesh-worker@${worker}.service"
    echo "  started: mesh-worker@${worker}"
done

sleep 3

echo ""
echo "=== Verifying ==="
ALL_OK=true
for worker in "${WORKERS[@]}"; do
    if systemctl is-active --quiet "mesh-worker@${worker}.service"; then
        echo "  mesh-worker@${worker}: ACTIVE"
    else
        echo "  mesh-worker@${worker}: FAILED"
        echo "    Check: journalctl -u mesh-worker@${worker} -n 20"
        ALL_OK=false
    fi
done

echo ""
if $ALL_OK; then
    echo "=== All ${#WORKERS[@]} workers deployed and running ==="
else
    echo "=== Some workers failed to start — check logs above ==="
fi
echo "  Logs: journalctl -u mesh-worker@claude-work -f"
