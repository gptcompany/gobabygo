#!/usr/bin/env bash
# deploy-workers.sh — Deploy mesh workers locally on Workstation
# Usage: sudo ./deploy-workers.sh <auth_token> [--dry-run]
#
# Runs with sudo on Workstation (10.0.0.2 / 192.168.1.111)
set -euo pipefail

TOKEN="${1:?Usage: sudo $0 <auth_token> [--dry-run]}"
DRY_RUN="${2:-}"
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

BATCH_WORKERS=()
SESSION_WORKERS=()
REVIEW_WORKERS=()

for src_env in "${PROJECT_ROOT}"/deploy/mesh-worker-*.env; do
    [ -f "$src_env" ] || continue
    BATCH_WORKERS+=("$(basename "$src_env" .env | sed 's/^mesh-worker-//')")
done

for src_env in "${PROJECT_ROOT}"/deploy/mesh-session-*.env; do
    [ -f "$src_env" ] || continue
    SESSION_WORKERS+=("$(basename "$src_env" .env)")
done

for src_env in "${PROJECT_ROOT}"/deploy/mesh-review-*.env; do
    [ -f "$src_env" ] || continue
    REVIEW_WORKERS+=("$(basename "$src_env" .env)")
done

if [[ "$DRY_RUN" == "--dry-run" ]]; then
    echo "[DRY-RUN] Would deploy ${#BATCH_WORKERS[@]} batch, ${#SESSION_WORKERS[@]} session, ${#REVIEW_WORKERS[@]} review workers locally"
    echo "[DRY-RUN] Token: ${TOKEN:0:8}..."
    echo "[DRY-RUN] Batch workers: ${BATCH_WORKERS[*]:-(none)}"
    echo "[DRY-RUN] Session workers: ${SESSION_WORKERS[*]:-(none)}"
    echo "[DRY-RUN] Review workers: ${REVIEW_WORKERS[*]:-(none)}"
    echo "[DRY-RUN] Steps: mesh-worker user, copy code, venv, env files, systemd"
    exit 0
fi

if [[ $EUID -ne 0 ]]; then
    echo "ERROR: This script must be run as root (use sudo)"
    exit 1
fi

normalize_task_root() {
    local task_root="/tmp/mesh-tasks"

    mkdir -p "$task_root"
    if getent group sam >/dev/null 2>&1; then
        chown -R mesh-worker:sam "$task_root"
        echo "  task root owner: mesh-worker:sam"
    else
        chown -R mesh-worker:mesh-worker "$task_root"
        echo "  WARNING: group 'sam' missing; task root kept on mesh-worker:mesh-worker"
    fi
    chmod 2775 "$task_root"
    find "$task_root" -type d -exec chmod 2775 {} +
    find "$task_root" -type f -exec chmod ug+rw {} +
}

install_worker_tmpfiles_config() {
    local task_group="mesh-worker"

    if getent group sam >/dev/null 2>&1; then
        task_group="sam"
    fi

    cat > /etc/tmpfiles.d/mesh-worker.conf <<EOF
d /tmp/mesh-tasks 2775 mesh-worker ${task_group} -
EOF
    chmod 644 /etc/tmpfiles.d/mesh-worker.conf
    systemd-tmpfiles --create /etc/tmpfiles.d/mesh-worker.conf
}

install_common_worker_env() {
    local src="$1"
    local dst="/etc/mesh-worker/$(basename "$src")"

    sed "s/__REPLACE_WITH_TOKEN__/${TOKEN}/" "$src" > "$dst"
    if getent group sam >/dev/null 2>&1; then
        chown root:sam "$dst"
        chmod 640 "$dst"
    else
        chown root:root "$dst"
        chmod 600 "$dst"
    fi
}

prepare_worker_uv_env() {
    mkdir -p /opt/mesh-worker/.uv/cache /opt/mesh-worker/.uv/python
    chown -R mesh-worker:mesh-worker /opt/mesh-worker/.uv
}

echo "=== Deploying Mesh Workers (local) ==="

# 1. Create service user
echo "[1/7] Creating mesh-worker user..."
# Ensure mesh group exists (shared with router service unit)
groupadd -f mesh
if ! id mesh-worker &>/dev/null; then
    useradd --system --create-home --shell /bin/bash mesh-worker
    usermod -aG mesh,sam mesh-worker 2>/dev/null || true
    echo "  Created user: mesh-worker (groups: mesh, sam)"
else
    usermod -aG mesh,sam mesh-worker 2>/dev/null || true
    echo "  User mesh-worker already exists (groups updated)"
fi

# 2. Create directories
echo "[2/7] Creating directories..."
mkdir -p /etc/mesh-worker /opt/mesh-worker /home/mesh-worker/.mesh/agents /tmp/mesh-tasks
chown -R mesh-worker:mesh-worker /opt/mesh-worker /home/mesh-worker/.mesh
install_worker_tmpfiles_config
normalize_task_root
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

# Find uv — may not be in root's PATH
UV_BIN=""
for uv_candidate in /home/sam/.local/bin/uv /usr/local/bin/uv /root/.local/bin/uv; do
    if [ -x "$uv_candidate" ]; then
        UV_BIN="$uv_candidate"
        break
    fi
done
if [ -z "$UV_BIN" ] && command -v uv &>/dev/null; then
    UV_BIN="$(command -v uv)"
fi

if [ -n "$UV_BIN" ]; then
    echo "  Using uv: $UV_BIN"
    rm -rf venv
    prepare_worker_uv_env
    # Keep uv-managed Python under /opt/mesh-worker so mesh-worker and runtime-user
    # overrides (for example User=sam) can execute the same interpreter target.
    env UV_CACHE_DIR=/opt/mesh-worker/.uv/cache \
        UV_PYTHON_INSTALL_DIR=/opt/mesh-worker/.uv/python \
        "$UV_BIN" venv venv --python 3.12
    env UV_CACHE_DIR=/opt/mesh-worker/.uv/cache \
        UV_PYTHON_INSTALL_DIR=/opt/mesh-worker/.uv/python \
        "$UV_BIN" pip install -e . --python venv/bin/python
    chown -R mesh-worker:mesh-worker /opt/mesh-worker/.uv
else
    echo "ERROR: uv not found. Install: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi
chown -R mesh-worker:mesh-worker /opt/mesh-worker
echo "  venv + deps: OK"

# 5. Copy env files with real token
echo "[5/7] Configuring env files..."
for common_env in "${PROJECT_ROOT}"/deploy/*.common.env; do
    [ -f "$common_env" ] || continue
    install_common_worker_env "$common_env"
    echo "  $(basename "$common_env"): OK"
done

for worker in "${BATCH_WORKERS[@]}"; do
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

# Optional interactive session worker env templates (claude/codex/gemini if present)
for SRC_ENV in "${PROJECT_ROOT}"/deploy/mesh-session-*.env; do
    [ -f "$SRC_ENV" ] || continue
    name="$(basename "$SRC_ENV" .env)"
    DST_ENV="/etc/mesh-worker/${name}.env"
    sed "s/__REPLACE_WITH_TOKEN__/${TOKEN}/" "$SRC_ENV" > "$DST_ENV"
    chown mesh-worker:mesh-worker "$DST_ENV"
    chmod 600 "$DST_ENV"
    echo "  ${name}.env: OK (session template)"
done

# Optional review worker env templates.
for SRC_ENV in "${PROJECT_ROOT}"/deploy/mesh-review-*.env; do
    [ -f "$SRC_ENV" ] || continue
    name="$(basename "$SRC_ENV" .env)"
    DST_ENV="/etc/mesh-worker/${name}.env"
    sed "s/__REPLACE_WITH_TOKEN__/${TOKEN}/" "$SRC_ENV" > "$DST_ENV"
    chown mesh-worker:mesh-worker "$DST_ENV"
    chmod 600 "$DST_ENV"
    echo "  ${name}.env: OK (review template)"
done

# 6. Install systemd template unit
echo "[6/7] Installing systemd service..."
cp "${PROJECT_ROOT}/deploy/mesh-worker@.service" /etc/systemd/system/mesh-worker@.service
if [ -f "${PROJECT_ROOT}/deploy/mesh-session-worker@.service" ]; then
    cp "${PROJECT_ROOT}/deploy/mesh-session-worker@.service" /etc/systemd/system/mesh-session-worker@.service
fi
if [ -f "${PROJECT_ROOT}/deploy/mesh-review-worker@.service" ]; then
    cp "${PROJECT_ROOT}/deploy/mesh-review-worker@.service" /etc/systemd/system/mesh-review-worker@.service
fi
systemctl daemon-reload

for worker in "${BATCH_WORKERS[@]}"; do
    systemctl enable "mesh-worker@${worker}.service"
    echo "  enabled: mesh-worker@${worker}"
done
for worker in "${SESSION_WORKERS[@]}"; do
    systemctl enable "mesh-session-worker@${worker}.service"
    echo "  enabled: mesh-session-worker@${worker}"
done
for worker in "${REVIEW_WORKERS[@]}"; do
    systemctl enable "mesh-review-worker@${worker}.service"
    echo "  enabled: mesh-review-worker@${worker}"
done
echo "  systemd: OK"

# 7. Start workers
echo "[7/7] Starting workers..."
for worker in "${BATCH_WORKERS[@]}"; do
    systemctl restart "mesh-worker@${worker}.service"
    echo "  started: mesh-worker@${worker}"
done
for worker in "${SESSION_WORKERS[@]}"; do
    systemctl restart "mesh-session-worker@${worker}.service"
    echo "  started: mesh-session-worker@${worker}"
done
for worker in "${REVIEW_WORKERS[@]}"; do
    systemctl restart "mesh-review-worker@${worker}.service"
    echo "  started: mesh-review-worker@${worker}"
done

sleep 3

echo ""
echo "=== Verifying ==="
ALL_OK=true
for worker in "${BATCH_WORKERS[@]}"; do
    if systemctl is-active --quiet "mesh-worker@${worker}.service"; then
        echo "  mesh-worker@${worker}: ACTIVE"
    else
        echo "  mesh-worker@${worker}: FAILED"
        echo "    Check: journalctl -u mesh-worker@${worker} -n 20"
        ALL_OK=false
    fi
done
for worker in "${SESSION_WORKERS[@]}"; do
    if systemctl is-active --quiet "mesh-session-worker@${worker}.service"; then
        echo "  mesh-session-worker@${worker}: ACTIVE"
    else
        echo "  mesh-session-worker@${worker}: FAILED"
        echo "    Check: journalctl -u mesh-session-worker@${worker} -n 20"
        ALL_OK=false
    fi
done
for worker in "${REVIEW_WORKERS[@]}"; do
    if systemctl is-active --quiet "mesh-review-worker@${worker}.service"; then
        echo "  mesh-review-worker@${worker}: ACTIVE"
    else
        echo "  mesh-review-worker@${worker}: FAILED"
        echo "    Check: journalctl -u mesh-review-worker@${worker} -n 20"
        ALL_OK=false
    fi
done

echo ""
if $ALL_OK; then
    total_workers=$((${#BATCH_WORKERS[@]} + ${#SESSION_WORKERS[@]} + ${#REVIEW_WORKERS[@]}))
    echo "=== All ${total_workers} workers deployed and running ==="
else
    echo "=== Some workers failed to start — check logs above ==="
fi
echo "  Logs: journalctl -u mesh-worker@claude-work -f"
echo "  Interactive session worker (manual start): systemctl start mesh-session-worker@mesh-session-claude-work"
