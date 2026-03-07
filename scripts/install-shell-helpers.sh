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

# Prefer explicit MESH_HOME, otherwise pin to 1TB workspace when available.
if [[ -z "${MESH_HOME:-}" && -d "/media/sam/1TB/gobabygo/scripts" ]]; then
  export MESH_HOME="/media/sam/1TB/gobabygo"
fi

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
  ws_host="${MESH_WS_HOST:-sam@192.168.1.111}"
  repo_base="${MESH_WS_REPO_BASE:-/media/sam/1TB}"
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
    command "$ws_script" "$@"
    return $?
  fi

  if [[ $# -eq 0 ]]; then
    command ssh "$ws_host"
    return $?
  fi

  repo="$1"
  if [[ "$repo" == /* ]]; then
    target_dir="$repo"
  else
    target_dir="${repo_base}/${repo}"
  fi
  command ssh -t "$ws_host" "if [[ -d '$target_dir' ]]; then cd '$target_dir'; else echo '[wss] missing repo: $target_dir'; cd '$repo_base'; fi; exec \$SHELL -l"
}

# wsattach: attach to tmux session on WS, auto-detecting effective service user.
unalias wsattach >/dev/null 2>&1 || true
wsattach() {
  local ws_host session
  ws_host="${MESH_WS_HOST:-sam@192.168.1.111}"
  session="${1:-}"
  if [[ -z "$session" ]]; then
    echo "Usage: wsattach <tmux-session>"
    return 1
  fi
  command ssh -t "$ws_host" "bash -lc '
if id mesh-worker >/dev/null 2>&1; then exec sudo -u mesh-worker tmux attach -t \"$session\"; fi
if id mesh >/dev/null 2>&1; then exec sudo -u mesh tmux attach -t \"$session\"; fi
exec tmux attach -t \"$session\"
'"
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

echo "Installed/updated lfcd + yazicd (+safe wss) in:"
echo "  - $TARGET_ZSHRC"
echo "  - $TARGET_BASHRC"
echo "Run one of:"
echo "  source \"$TARGET_ZSHRC\""
echo "  source \"$TARGET_BASHRC\""
