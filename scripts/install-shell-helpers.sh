#!/usr/bin/env bash
set -euo pipefail

TARGET_ZSHRC="${TARGET_ZSHRC:-$HOME/.zshrc}"
TARGET_BASHRC="${TARGET_BASHRC:-$HOME/.bashrc}"
BEGIN_MARKER="# >>> gobabygo-shell-helpers >>>"
END_MARKER="# <<< gobabygo-shell-helpers <<<"

install_block() {
  local target_rc="$1"
  mkdir -p "$(dirname "$target_rc")"
  touch "$target_rc"

  # Idempotent update: remove previous helper block (if present), then append fresh block.
  if grep -Fq "$BEGIN_MARKER" "$target_rc"; then
    local tmp_cleanup
    tmp_cleanup="$(mktemp "${TMPDIR:-/tmp}/shell.helpers.XXXXXX")"
    awk -v begin="$BEGIN_MARKER" -v end="$END_MARKER" '
      $0 == begin { skip = 1; next }
      $0 == end { skip = 0; next }
      !skip { print }
    ' "$target_rc" >"$tmp_cleanup"
    mv "$tmp_cleanup" "$target_rc"
  fi

  cat >>"$target_rc" <<'EOF'

# >>> gobabygo-shell-helpers >>>
_mesh_resolve_home() {
  if [[ -n "${MESH_HOME:-}" && -d "${MESH_HOME}/scripts" ]]; then
    printf '%s' "${MESH_HOME}"
    return 0
  fi
  if [[ -n "${MESH_WS_REPO_BASE:-}" && -d "${MESH_WS_REPO_BASE}/gobabygo-runtime/scripts" ]]; then
    printf '%s' "${MESH_WS_REPO_BASE}/gobabygo-runtime"
    return 0
  fi
  if [[ -n "${MESH_WS_REPO_BASE:-}" && -d "${MESH_WS_REPO_BASE}/gobabygo/scripts" ]]; then
    printf '%s' "${MESH_WS_REPO_BASE}/gobabygo"
    return 0
  fi
  if [[ -d "/data/sata/1TB/gobabygo-runtime/scripts" ]]; then
    printf '%s' "/data/sata/1TB/gobabygo-runtime"
    return 0
  fi
  if [[ -d "/media/sam/1TB/gobabygo/scripts" ]]; then
    printf '%s' "/media/sam/1TB/gobabygo"
    return 0
  fi
  if [[ -d "$HOME/gobabygo/scripts" ]]; then
    printf '%s' "$HOME/gobabygo"
    return 0
  fi
  return 1
}

# Prefer explicit MESH_HOME, otherwise pin to the canonical runtime when available.
if [[ -z "${MESH_HOME:-}" && -d "/data/sata/1TB/gobabygo-runtime/scripts" ]]; then
  export MESH_HOME="/data/sata/1TB/gobabygo-runtime"
elif [[ -z "${MESH_HOME:-}" && -d "/media/sam/1TB/gobabygo/scripts" ]]; then
  export MESH_HOME="/media/sam/1TB/gobabygo"
fi

_mesh_ssh_opts() {
  local interval count
  interval="${MESH_SSH_SERVER_ALIVE_INTERVAL:-10}"
  count="${MESH_SSH_SERVER_ALIVE_COUNT_MAX:-18}"
  printf '%s\n' \
    -o "ServerAliveInterval=${interval}" \
    -o "ServerAliveCountMax=${count}" \
    -o "TCPKeepAlive=yes" \
    -o "ConnectTimeout=10" \
    -o "ConnectionAttempts=3" \
    -o "ControlMaster=no" \
    -o "ControlPath=none" \
    -o "IPQoS=none"
}

_mesh_collect_ssh_opts() {
  local opt
  while IFS= read -r opt; do
    [[ -n "$opt" ]] && printf '%s\0' "$opt"
  done < <(_mesh_ssh_opts)
}

lfcd() {
  command -v lf >/dev/null 2>&1 || { echo "lf not found"; return 127; }
  local tmp rc dir
  tmp="$(mktemp "${TMPDIR:-/tmp}/lfcd.XXXXXX")"
  rc=0
  command lf -last-dir-path="$tmp" "$@" || rc=$?
  if [[ -f "$tmp" ]]; then
    dir="$(cat "$tmp")"
    [[ -d "$dir" ]] && builtin cd -- "$dir"
    rm -f "$tmp"
  fi
  return "$rc"
}

yazicd() {
  command -v yazi >/dev/null 2>&1 || { echo "yazi not found"; return 127; }
  local tmp rc dir
  tmp="$(mktemp "${TMPDIR:-/tmp}/yazicd.XXXXXX")"
  rc=0
  command yazi --cwd-file="$tmp" "$@" || rc=$?
  if [[ -s "$tmp" ]]; then
    dir="$(cat "$tmp")"
    [[ -d "$dir" ]] && builtin cd -- "$dir"
  fi
  rm -f "$tmp"
  return "$rc"
}

# wss: quick SSH to WS; if passed a repo name, jumps to that repo directory.
unalias wss >/dev/null 2>&1 || true
wss() {
  local ws_script ws_host repo_base repo mesh_home target_dir
  local -a ssh_opts=()
  _is_local_ws_host() {
    local h="$1"
    local t ip
    t="${h#*@}"
    t="${t%%:*}"
    case "$t" in
      localhost|127.0.0.1) return 0 ;;
    esac

    # If we are already connected via SSH to this host, avoid self-SSH loops.
    if [[ -n "${SSH_CONNECTION:-}" ]]; then
      local ssh_server_ip
      ssh_server_ip="$(awk '{print $3}' <<<"${SSH_CONNECTION}" 2>/dev/null || true)"
      if [[ -n "$ssh_server_ip" && "$t" == "$ssh_server_ip" ]]; then
        return 0
      fi
    fi

    [[ "$t" == "$(hostname 2>/dev/null || true)" ]] && return 0
    ip=""
    if command -v getent >/dev/null 2>&1; then
      ip="$(getent ahostsv4 "$t" 2>/dev/null | awk 'NR==1{print $1}')"
    fi
    [[ -z "$ip" ]] && ip="$t"
    if command -v ip >/dev/null 2>&1 && ip -4 addr show 2>/dev/null | grep -qw "$ip"; then
      return 0
    fi
    return 1
  }
  if command -v _ws_control_host >/dev/null 2>&1; then
    ws_host="$(_ws_control_host)" || return $?
  else
    ws_host="${MESH_WS_HOST:-sam@10.0.0.2}"
  fi
  repo_base="${MESH_WS_REPO_BASE:-/media/sam/1TB}"
  while IFS= read -r -d '' opt; do
    ssh_opts+=("$opt")
  done < <(_mesh_collect_ssh_opts)
  if _is_local_ws_host "$ws_host"; then
    if [[ $# -eq 0 ]]; then
      return 0
    fi
    repo="$1"
    if [[ "$repo" == /* ]]; then
      target_dir="$repo"
    else
      target_dir="${repo_base}/${repo}"
    fi
    if [[ -d "$target_dir" ]]; then
      builtin cd -- "$target_dir"
    else
      echo "[wss] missing repo: $target_dir"
      builtin cd -- "$repo_base"
    fi
    return $?
  fi

  mesh_home="$(_mesh_resolve_home || true)"
  ws_script="${mesh_home}/scripts/ws"
  if [[ -x "$ws_script" ]]; then
    MESH_WS_HOST="$ws_host" command "$ws_script" "$@"
    return $?
  fi

  if [[ $# -eq 0 ]]; then
    command ssh "${ssh_opts[@]}" "$ws_host"
    return $?
  fi

  repo="$1"
  if [[ "$repo" == /* ]]; then
    target_dir="$repo"
  else
    target_dir="${repo_base}/${repo}"
  fi
  command ssh "${ssh_opts[@]}" -t "$ws_host" "if [[ -d '$target_dir' ]]; then cd '$target_dir'; else echo '[wss] missing repo: $target_dir'; cd '$repo_base'; fi; exec \$SHELL -l"
}

# mesh: wrapper globale al launcher gobabygo/scripts/mesh (funziona da qualsiasi cartella).
unalias mesh >/dev/null 2>&1 || true
mesh() {
  local mesh_script mesh_home
  mesh_home="$(_mesh_resolve_home || true)"
  mesh_script="${mesh_home}/scripts/mesh"
  if [[ ! -x "$mesh_script" ]]; then
    echo "mesh script not found at $mesh_script"
    return 127
  fi
  command "$mesh_script" "$@"
}

mesh_live_helpers="$(_mesh_resolve_home 2>/dev/null || true)/scripts/mesh_live_shell_helpers.sh"
if [[ -r "$mesh_live_helpers" ]]; then
  source "$mesh_live_helpers"
else
  echo "Warning: mesh live shell helpers not found at $mesh_live_helpers" >&2
fi
unset mesh_live_helpers

# Convenience aliases: keep native command names but use cd-aware wrappers.
if command -v yazi >/dev/null 2>&1; then
  unalias yazi >/dev/null 2>&1 || true
  alias yazi='yazicd'
fi
if command -v lf >/dev/null 2>&1; then
  unalias lf >/dev/null 2>&1 || true
  alias lf='lfcd'
fi
# <<< gobabygo-shell-helpers <<<
EOF
}

install_block "$TARGET_ZSHRC"
install_block "$TARGET_BASHRC"

echo "Installed/updated mesh live + persistent tmux shell helpers in:"
echo "  - $TARGET_ZSHRC"
echo "  - $TARGET_BASHRC"
echo "Run one of:"
echo "  source \"$TARGET_ZSHRC\""
echo "  source \"$TARGET_BASHRC\""
