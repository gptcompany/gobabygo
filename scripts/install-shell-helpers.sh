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
# Installed only if "wss" is currently unused (no alias/function/command).
if ! alias wss >/dev/null 2>&1 && ! typeset -f wss >/dev/null 2>&1 && ! command -v wss >/dev/null 2>&1; then
  wss() {
    local ws_script ws_host repo_base repo mesh_home
    mesh_home="$(_mesh_resolve_home || true)"
    ws_script="${mesh_home}/scripts/ws"
    if [[ -x "$ws_script" ]]; then
      command "$ws_script" "$@"
      return $?
    fi

    ws_host="${MESH_WS_HOST:-sam@192.168.1.111}"
    repo_base="${MESH_WS_REPO_BASE:-/media/sam/1TB}"
    if [[ $# -eq 0 ]]; then
      command ssh "$ws_host"
      return $?
    fi

    repo="$1"
    command ssh -t "$ws_host" "cd '${repo_base}/${repo}' && exec \$SHELL -l"
  }
fi

# mesh: wrapper globale al launcher gobabygo/scripts/mesh (funziona da qualsiasi cartella).
if ! alias mesh >/dev/null 2>&1 && ! typeset -f mesh >/dev/null 2>&1 && ! command -v mesh >/dev/null 2>&1; then
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
fi

# Convenience aliases: keep native command names but use cd-aware wrappers.
# Installed only if alias name is currently unused.
if command -v yazi >/dev/null 2>&1 && ! alias yazi >/dev/null 2>&1; then
  alias yazi='yazicd'
fi
if command -v lf >/dev/null 2>&1 && ! alias lf >/dev/null 2>&1; then
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
