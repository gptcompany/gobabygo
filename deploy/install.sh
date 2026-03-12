#!/usr/bin/env bash
# install.sh — Mesh network provisioning
# Usage: ./install.sh router   (on VPS)
#        ./install.sh worker   (on Workstation)
set -euo pipefail

MODE="${1:?Usage: $0 router|worker}"
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

normalize_task_root() {
  local task_root="/tmp/mesh-tasks"

  sudo mkdir -p "$task_root"
  if getent group sam >/dev/null 2>&1; then
    sudo chown -R mesh-worker:sam "$task_root"
  else
    sudo chown -R mesh-worker:mesh-worker "$task_root"
    echo "WARNING: group 'sam' missing; task root kept on mesh-worker:mesh-worker"
  fi
  sudo chmod 2775 "$task_root"
  sudo find "$task_root" -type d -exec chmod 2775 {} +
  sudo find "$task_root" -type f -exec chmod ug+rw {} +
}

install_worker_common_env() {
    local src="$1"
    local dst="/etc/mesh-worker/$(basename "$src")"

    sudo cp "$src" "$dst"
    if getent group sam >/dev/null 2>&1; then
      sudo chown root:sam "$dst"
      sudo chmod 640 "$dst"
    else
      sudo chown root:root "$dst"
      sudo chmod 600 "$dst"
    fi
}

install_worker_instance_env() {
    local src="$1"
    local name

    name="$(basename "$src" .env)"
    case "$name" in
      mesh-worker-*)
        name="${name#mesh-worker-}"
        ;;
    esac

    sudo cp "$src" "/etc/mesh-worker/${name}.env"
    sudo chown mesh-worker "/etc/mesh-worker/${name}.env"
    sudo chmod 600 "/etc/mesh-worker/${name}.env"
}

enable_worker_instances() {
    local env_path
    local name
    local unit

    for env_path in /etc/mesh-worker/*.env; do
      [ -e "$env_path" ] || continue
      name="$(basename "$env_path" .env)"
      case "$name" in
        common|*.common)
          continue
          ;;
        mesh-session-*)
          unit="mesh-session-worker@${name}.service"
          ;;
        mesh-review-*)
          unit="mesh-review-worker@${name}.service"
          ;;
        *)
          unit="mesh-worker@${name}.service"
          ;;
      esac
      sudo systemctl enable "$unit"
    done
}

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
    sudo chown -R mesh-worker:mesh-worker /opt/mesh-worker
    sudo chown -R mesh-worker /home/mesh-worker/.mesh
    normalize_task_root

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
    for common_env in deploy/*.common.env; do
      [ -e "$common_env" ] || continue
      install_worker_common_env "$common_env"
    done

    # Includes batch (`mesh-worker-*`), interactive session (`mesh-session-*`),
    # and review (`mesh-review-*`) templates.
    for env_file in deploy/mesh-worker-*.env deploy/mesh-session-*.env deploy/mesh-review-*.env; do
      [ -e "$env_file" ] || continue
      install_worker_instance_env "$env_file"
    done
    echo "!! Edit /etc/mesh-worker/common.env with real MESH_AUTH_TOKEN and MESH_ROUTER_URL"

    # 6. Install systemd template unit
    sudo cp deploy/mesh-worker@.service /etc/systemd/system/
    if [ -f deploy/mesh-session-worker@.service ]; then
      sudo cp deploy/mesh-session-worker@.service /etc/systemd/system/
    fi
    if [ -f deploy/mesh-review-worker@.service ]; then
      sudo cp deploy/mesh-review-worker@.service /etc/systemd/system/
    fi
    sudo systemctl daemon-reload

    # Enable known worker instances
    enable_worker_instances

    echo "=== Workers installed. Start batch with: sudo systemctl start mesh-worker@claude-work ==="
    echo "=== Interactive session workers available: mesh-session-worker@mesh-session-claude-work ==="
    ;;

  *)
    echo "Usage: $0 router|worker"
    exit 1
    ;;
esac
