#!/usr/bin/env bash
set -euo pipefail

ROLE="${1:?missing role}"
REPO_INPUT="${2:?missing repo}"
REPO_NAME="${3:?missing repo_name}"
ROLE_SET="${4:-}"
REMOTE_INIT="${5:-}"
LIVE_ATTACH_MODE="${6:-auto}"
if [[ -z "$REMOTE_INIT" && -n "$ROLE_SET" && "$ROLE_SET" != *","* ]]; then
  REMOTE_INIT="$ROLE_SET"
  ROLE_SET="$ROLE"
fi
[[ -n "$ROLE_SET" ]] || ROLE_SET="$ROLE"
REMOTE_INIT_B64=""

WS_HOST="${MESH_WS_HOST:-sam@192.168.1.111}"
WS_REPO_BASE="${MESH_WS_REPO_BASE:-/media/sam/1TB}"
MESH_CONTROL_REPO="${MESH_CONTROL_REPO:-/media/sam/1TB/gobabygo}"

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

resolve_target_dir() {
  local repo_input="$1"
  local ws_repo_base="$2"
  local candidate

  if [[ "$repo_input" == /* || "$repo_input" == .* || "$repo_input" == *"/"* ]]; then
    printf '%s\n' "$repo_input"
    return 0
  fi

  for candidate in \
    "${ws_repo_base}/${repo_input}" \
    "/media/sam/1TB/${repo_input}" \
    "/tmp/mesh-tasks/${repo_input}" \
    "/home/sam/${repo_input}"; do
    if [[ -d "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done

  printf '%s\n' "${ws_repo_base}/${repo_input}"
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

if [[ -n "$REMOTE_INIT" ]]; then
  REMOTE_INIT_B64="$(printf '%s' "$REMOTE_INIT" | base64 | tr -d '\n')"
fi

bootstrap_shell() {
  local target_dir="$1"
  local ws_repo_base="$2"
  local role="$3"
  local repo_name="$4"
  local remote_init="$5"
  local live_attach_mode="$6"
  local mesh_home mesh_script live_attach_helper live_attach

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
  if [[ -d "$target_dir/.git" || -f "$target_dir/.git" ]]; then
    git config --global --add safe.directory "$target_dir" >/dev/null 2>&1 || true
  fi
  mesh_home="$MESH_CONTROL_REPO"
  if [[ ! -x "$mesh_home/scripts/mesh" && -x "$ws_repo_base/gobabygo/scripts/mesh" ]]; then
    mesh_home="$ws_repo_base/gobabygo"
  fi
  mesh_script="$mesh_home/scripts/mesh"
  if [[ -x "$mesh_script" ]]; then
    mesh() { "$mesh_script" "$@"; }
    export MESH_HOME="$mesh_home"
  fi
  live_attach_helper="$mesh_home/scripts/mesh_ui_live_attach.py"
  if [[ "$live_attach_mode" != "pre_resolved" && "${MESH_UI_ATTACH_LIVE:-1}" != "0" && -f "$live_attach_helper" ]]; then
    live_attach="$("$(command -v python3 || command -v python)" "$live_attach_helper" "$role" "$target_dir" "$repo_name" "$ROLE_SET" 2>/dev/null || true)"
    if [[ -n "$live_attach" ]]; then
      eval "$live_attach"
    fi
  fi
  if [[ "$live_attach_mode" != "pre_resolved" && -z "$remote_init" && -z "${live_attach:-}" && ( "$role" == worker-* || "$role" == "verifier" ) ]]; then
    printf '[mesh:%s] WARNING: no active mesh session attached. This is a detached control shell on the WS, not the live worker runtime.\n' "$role"
  fi
  if [[ -n "$remote_init" ]]; then
    eval "$remote_init"
  fi
  exec "${SHELL:-/bin/bash}" -l
}

if is_local_ws_host "$WS_HOST"; then
  TARGET_DIR="$(resolve_target_dir "$REPO_INPUT" "$WS_REPO_BASE")"
  bootstrap_shell "$TARGET_DIR" "$WS_REPO_BASE" "$ROLE" "$REPO_NAME" "$REMOTE_INIT" "$LIVE_ATTACH_MODE"
fi

REMOTE_BOOTSTRAP_SCRIPT='
set -euo pipefail
target_dir="${TARGET_DIR:?missing target_dir}"
ws_repo_base="${WS_REPO_BASE:?missing ws_repo_base}"
role="${ROLE:?missing role}"
repo_name="${REPO_NAME:?missing repo_name}"
mesh_control_repo="${MESH_CONTROL_REPO:-/media/sam/1TB/gobabygo}"
remote_init=""
role_set="${ROLE_SET:-$role}"
live_attach_mode="${LIVE_ATTACH_MODE:-auto}"

if [[ -n "${REMOTE_INIT_B64:-}" ]]; then
  remote_init="$(printf "%s" "$REMOTE_INIT_B64" | base64 -d)"
fi

if [[ ! -d "$target_dir" ]]; then
  for candidate in \
    "$ws_repo_base/$repo_name" \
    "/media/sam/1TB/$repo_name" \
    "/tmp/mesh-tasks/$repo_name" \
    "/home/sam/$repo_name"; do
    if [[ -d "$candidate" ]]; then
      target_dir="$candidate"
      break
    fi
  done
fi

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
if [[ -d "$target_dir/.git" || -f "$target_dir/.git" ]]; then
  git config --global --add safe.directory "$target_dir" >/dev/null 2>&1 || true
fi
mesh_home="$mesh_control_repo"
if [[ ! -x "$mesh_home/scripts/mesh" && -x "$ws_repo_base/gobabygo/scripts/mesh" ]]; then
  mesh_home="$ws_repo_base/gobabygo"
fi
mesh_script="$mesh_home/scripts/mesh"
if [[ -x "$mesh_script" ]]; then
  mesh() { "$mesh_script" "$@"; }
  export MESH_HOME="$mesh_home"
fi
live_attach_helper="$mesh_home/scripts/mesh_ui_live_attach.py"
if [[ "$live_attach_mode" != "pre_resolved" && "${MESH_UI_ATTACH_LIVE:-1}" != "0" && -f "$live_attach_helper" ]]; then
  live_attach="$("$(command -v python3 || command -v python)" "$live_attach_helper" "$role" "$target_dir" "$repo_name" "$role_set" 2>/dev/null || true)"
  if [[ -n "$live_attach" ]]; then
    eval "$live_attach"
  fi
fi
if [[ "$live_attach_mode" != "pre_resolved" && -z "$remote_init" && -z "${live_attach:-}" && ( "$role" == worker-* || "$role" == "verifier" ) ]]; then
  printf "[mesh:%s] WARNING: no active mesh session attached. This is a detached control shell on the WS, not the live worker runtime.\n" "$role"
fi
if [[ -n "$remote_init" ]]; then
  eval "$remote_init"
fi
exec "${SHELL:-/bin/bash}" -l
'

mapfile -t SSH_OPTS < <(mesh_ssh_ui_opts)
REMOTE_COMMAND="$(printf 'TARGET_DIR=%q WS_REPO_BASE=%q ROLE=%q REPO_NAME=%q ROLE_SET=%q REMOTE_INIT_B64=%q LIVE_ATTACH_MODE=%q bash -lc %q' \
  "$TARGET_DIR" "$WS_REPO_BASE" "$ROLE" "$REPO_NAME" "$ROLE_SET" "$REMOTE_INIT_B64" "$LIVE_ATTACH_MODE" "$REMOTE_BOOTSTRAP_SCRIPT")"
exec ssh "${SSH_OPTS[@]}" -tt "$WS_HOST" "$REMOTE_COMMAND"
