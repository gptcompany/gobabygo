#!/usr/bin/env bash
set -euo pipefail

ROLE="${1:?missing role}"
REPO_INPUT="${2:?missing repo}"
REPO_NAME="${3:?missing repo_name}"
REMOTE_INIT="${4:-}"

WS_HOST="${MESH_WS_HOST:-sam@192.168.1.111}"
WS_REPO_BASE="${MESH_WS_REPO_BASE:-/media/sam/1TB}"

mesh_ssh_opts() {
  local ctl_dir interval count persist
  ctl_dir="${MESH_SSH_CONTROL_DIR:-$HOME/.ssh/cm}"
  interval="${MESH_SSH_SERVER_ALIVE_INTERVAL:-15}"
  count="${MESH_SSH_SERVER_ALIVE_COUNT_MAX:-12}"
  persist="${MESH_SSH_CONTROL_PERSIST:-30m}"
  mkdir -p "$ctl_dir" 2>/dev/null || true
  printf '%s\n' \
    -o "ServerAliveInterval=${interval}" \
    -o "ServerAliveCountMax=${count}" \
    -o "TCPKeepAlive=yes" \
    -o "ConnectTimeout=10" \
    -o "ConnectionAttempts=3" \
    -o "ControlMaster=auto" \
    -o "ControlPersist=${persist}" \
    -o "ControlPath=${ctl_dir}/%C" \
    -o "IPQoS=none"
}

mesh_ssh_ui_opts() {
  local interval count
  interval="${MESH_SSH_SERVER_ALIVE_INTERVAL:-15}"
  count="${MESH_SSH_SERVER_ALIVE_COUNT_MAX:-12}"
  printf '%s\n' \
    -o "ServerAliveInterval=${interval}" \
    -o "ServerAliveCountMax=${count}" \
    -o "TCPKeepAlive=yes" \
    -o "ConnectTimeout=10" \
    -o "ConnectionAttempts=3" \
    -o "IPQoS=none"
}

is_local_ws_host() {
  local host="$1"
  local target target_ip
  target="${host#*@}"
  target="${target%%:*}"

  case "$target" in
    localhost|127.0.0.1)
      return 0
      ;;
  esac

  if [[ -n "${SSH_CONNECTION:-}" ]]; then
    local ssh_server_ip
    ssh_server_ip="$(awk '{print $3}' <<<"${SSH_CONNECTION}" 2>/dev/null || true)"
    if [[ -n "$ssh_server_ip" && "$target" == "$ssh_server_ip" ]]; then
      return 0
    fi
  fi

  [[ "$target" == "$(hostname 2>/dev/null || true)" ]] && return 0

  target_ip=""
  if command -v getent >/dev/null 2>&1; then
    target_ip="$(getent ahostsv4 "$target" 2>/dev/null | awk 'NR==1{print $1}')"
  fi
  [[ -z "$target_ip" ]] && target_ip="$target"
  if command -v ip >/dev/null 2>&1 && ip -4 addr show 2>/dev/null | grep -qw "$target_ip"; then
    return 0
  fi
  return 1
}

if [[ "$REPO_INPUT" == /* || "$REPO_INPUT" == .* || "$REPO_INPUT" == *"/"* ]]; then
  TARGET_DIR="$REPO_INPUT"
else
  TARGET_DIR="${WS_REPO_BASE}/${REPO_INPUT}"
fi

bootstrap_shell() {
  local target_dir="$1"
  local ws_repo_base="$2"
  local role="$3"
  local repo_name="$4"
  local remote_init="$5"

  if [[ -d "$target_dir" ]]; then
    cd "$target_dir"
  else
    echo "[mesh:${role}] missing repo: $target_dir"
    cd "$ws_repo_base"
  fi

  if [[ -n "${TERM:-}" ]]; then
    clear
  fi
  echo "[mesh:${role}] repo=${repo_name}"
  if [[ -n "$remote_init" ]]; then
    eval "$remote_init"
  fi
  exec "${SHELL:-/bin/bash}" -l
}

if is_local_ws_host "$WS_HOST"; then
  bootstrap_shell "$TARGET_DIR" "$WS_REPO_BASE" "$ROLE" "$REPO_NAME" "$REMOTE_INIT"
fi

mapfile -t SSH_OPTS < <(mesh_ssh_ui_opts)
exec ssh "${SSH_OPTS[@]}" -tt "$WS_HOST" "bash -s" -- "$TARGET_DIR" "$WS_REPO_BASE" "$ROLE" "$REPO_NAME" "$REMOTE_INIT" <<'EOF'
set -euo pipefail
target_dir="${1:?missing target_dir}"
ws_repo_base="${2:?missing ws_repo_base}"
role="${3:?missing role}"
repo_name="${4:?missing repo_name}"
remote_init="${5:-}"

if [[ -d "$target_dir" ]]; then
  cd "$target_dir"
else
  echo "[mesh:${role}] missing repo: $target_dir"
  cd "$ws_repo_base"
fi

if [[ -n "${TERM:-}" ]]; then
  clear
fi
echo "[mesh:${role}] repo=${repo_name}"
if [[ -n "$remote_init" ]]; then
  eval "$remote_init"
fi
exec "${SHELL:-/bin/bash}" -l
EOF
