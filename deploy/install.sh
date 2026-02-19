#!/usr/bin/env bash
# install.sh — Mesh network provisioning
# Usage: ./install.sh router   (on VPS)
#        ./install.sh worker   (on Workstation)
set -euo pipefail

MODE="${1:?Usage: $0 router|worker}"
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

case "$MODE" in
  router)
    echo "=== Installing Mesh Router (VPS) ==="

    # 1. Create service user
    if ! id mesh &>/dev/null; then
      sudo useradd --system --no-create-home --shell /usr/sbin/nologin mesh
      echo "Created user: mesh"
    fi

    # 2. Create directories
    sudo mkdir -p /var/lib/mesh-router
    sudo mkdir -p /etc/mesh-router
    sudo mkdir -p /opt/mesh-router
    sudo chown mesh:mesh /var/lib/mesh-router
    sudo chown mesh:mesh /opt/mesh-router

    # 3. Install project (minimal: only production files)
    for item in src pyproject.toml schemas deploy; do
      [ -e "$PROJECT_ROOT/$item" ] && sudo cp -r "$PROJECT_ROOT/$item" /opt/mesh-router/
    done
    sudo chown -R mesh:mesh /opt/mesh-router

    # 4. Create venv with uv
    cd /opt/mesh-router
    if command -v uv &>/dev/null; then
      sudo -u mesh uv venv venv
      sudo -u mesh uv pip install -e . --python venv/bin/python
    else
      echo "ERROR: uv not found. Install: curl -LsSf https://astral.sh/uv/install.sh | sh"
      exit 1
    fi

    # 5. Copy env file template (owned by service user)
    sudo cp deploy/mesh-router.env /etc/mesh-router/mesh-router.env
    sudo chown mesh:mesh /etc/mesh-router/mesh-router.env
    sudo chmod 600 /etc/mesh-router/mesh-router.env
    echo "!! Edit /etc/mesh-router/mesh-router.env with real MESH_AUTH_TOKEN"

    # 6. Install systemd unit
    sudo cp deploy/mesh-router.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable mesh-router.service

    echo "=== Router installed. Start with: sudo systemctl start mesh-router ==="
    ;;

  worker)
    echo "=== Installing Mesh Workers (Workstation) ==="

    # 1. Create service user (with home for CCS profiles)
    if ! id mesh-worker &>/dev/null; then
      sudo useradd --system --create-home --shell /bin/bash mesh-worker
      sudo usermod -aG sam mesh-worker 2>/dev/null || true
      echo "Created user: mesh-worker"
    fi

    # 2. Create directories
    sudo mkdir -p /etc/mesh-worker
    sudo mkdir -p /opt/mesh-worker
    sudo mkdir -p /home/mesh-worker/.mesh/agents
    sudo chown -R mesh-worker:mesh /opt/mesh-worker 2>/dev/null || sudo chown -R mesh-worker /opt/mesh-worker
    sudo chown -R mesh-worker /home/mesh-worker/.mesh

    # 3. Install project (minimal: only production files)
    for item in src pyproject.toml schemas deploy; do
      [ -e "$PROJECT_ROOT/$item" ] && sudo cp -r "$PROJECT_ROOT/$item" /opt/mesh-worker/
    done
    sudo chown -R mesh-worker /opt/mesh-worker

    # 4. Create venv with uv
    cd /opt/mesh-worker
    if command -v uv &>/dev/null; then
      sudo -u mesh-worker uv venv venv
      sudo -u mesh-worker uv pip install -e . --python venv/bin/python
    else
      echo "ERROR: uv not found. Install: curl -LsSf https://astral.sh/uv/install.sh | sh"
      exit 1
    fi

    # 5. Copy env file templates (owned by service user)
    for env_file in deploy/mesh-worker-*.env; do
      [ -e "$env_file" ] || continue
      name=$(basename "$env_file" .env)
      instance="${name#mesh-worker-}"
      sudo cp "$env_file" "/etc/mesh-worker/${instance}.env"
      sudo chown mesh-worker "/etc/mesh-worker/${instance}.env"
      sudo chmod 600 "/etc/mesh-worker/${instance}.env"
    done
    echo "!! Edit /etc/mesh-worker/*.env with real MESH_AUTH_TOKEN and MESH_ROUTER_URL"

    # 6. Install systemd template unit
    sudo cp deploy/mesh-worker@.service /etc/systemd/system/
    sudo systemctl daemon-reload

    # Enable known worker instances
    sudo systemctl enable mesh-worker@claude-work.service
    sudo systemctl enable mesh-worker@codex-work.service
    sudo systemctl enable mesh-worker@gemini-work.service

    echo "=== Workers installed. Start with: sudo systemctl start mesh-worker@claude-work ==="
    ;;

  *)
    echo "Usage: $0 router|worker"
    exit 1
    ;;
esac
