#!/usr/bin/env bash
# Shell helpers for direct tmux operations on the workstation.

unalias wboard wpeek wsend wbrief wsattach wsessions 2>/dev/null || true
unalias wtmux wclaude wcodex mtmux mclaude mcodex mcoordinator 2>/dev/null || true

_mesh_live_run() {
  if command -v mesh >/dev/null 2>&1; then
    mesh "$@"
    return $?
  fi
  local mesh_home mesh_script
  mesh_home="$(_mesh_resolve_home 2>/dev/null || true)"
  mesh_script="${mesh_home}/scripts/mesh"
  if [[ ! -x "$mesh_script" ]]; then
    echo "mesh script not found at $mesh_script" >&2
    return 127
  fi
  command "$mesh_script" "$@"
}

_ws_tmux_session_name() {
  local prefix label base
  prefix="${1:-ws}"
  label="${2:-main}"
  base="${label##*/}"
  base="${base//[^A-Za-z0-9_.-]/-}"
  [[ -z "$base" || "$base" == "." ]] && base="work"
  printf '%s-%s' "$prefix" "$base"
}

_ws_tmux_target_dir() {
  local repo_base repo
  repo_base="${MESH_WS_REPO_BASE:-/media/sam/1TB}"
  repo="${1:-}"
  if [[ -z "$repo" ]]; then
    printf '%s' "$repo_base"
  elif [[ "$repo" == /* ]]; then
    printf '%s' "$repo"
  else
    printf '%s/%s' "$repo_base" "$repo"
  fi
}

_ws_host_reachable() {
  local target host
  target="$1"
  host="${target#*@}"
  host="$(printf '%s' "$host" | sed 's/^\[//; s/\]$//')"
  if command -v nc >/dev/null 2>&1; then
    nc -z -w 1 "$host" 22 >/dev/null 2>&1
    return $?
  fi
  ping -c 1 "$host" >/dev/null 2>&1
}

_ws_mosh_host() {
  local vpn_host lan_host
  if [[ -n "${MESH_MOSH_HOST:-}" ]]; then
    printf '%s' "$MESH_MOSH_HOST"
    return 0
  fi
  vpn_host="${MESH_WS_VPN_HOST:-sam@10.0.0.2}"
  lan_host="${MESH_WS_LAN_HOST:-sam@172.23.0.42}"
  if _ws_host_reachable "$vpn_host"; then
    printf '%s' "$vpn_host"
    return 0
  fi
  if _ws_host_reachable "$lan_host"; then
    printf '%s' "$lan_host"
    return 0
  fi
  return 1
}

_ws_cloudflare_host() {
  local candidate config
  candidate="${MESH_WS_CLOUDFLARE_HOST:-dell7670}"
  [[ -n "$candidate" ]] || return 1
  config="$(command ssh -G "$candidate" 2>/dev/null || true)"
  if printf '%s\n' "$config" | grep -Eiq '^proxycommand .*(cloudflared|cloudflare)'; then
    printf '%s' "$candidate"
    return 0
  fi
  return 1
}

_ws_control_host() {
  local direct_host cloudflare_host
  if [[ -n "${MESH_WS_CONTROL_HOST:-}" ]]; then
    printf '%s' "$MESH_WS_CONTROL_HOST"
    return 0
  fi
  direct_host="$(_ws_mosh_host 2>/dev/null || true)"
  if [[ -n "$direct_host" ]]; then
    printf '%s' "$direct_host"
    return 0
  fi
  if [[ -n "${MESH_WS_HOST:-}" ]]; then
    printf '%s' "$MESH_WS_HOST"
    return 0
  fi
  cloudflare_host="$(_ws_cloudflare_host 2>/dev/null || true)"
  if [[ -n "$cloudflare_host" ]]; then
    printf '%s' "$cloudflare_host"
    return 0
  fi
  printf '%s' "sam@10.0.0.2"
}

_mesh_live_control_run() {
  local control_host
  control_host="$(_ws_control_host)" || return $?
  MESH_WS_HOST="$control_host" _mesh_live_run "$@"
}

_mesh_live_is_uint() {
  case "${1:-}" in
    ""|*[!0-9]*) return 1 ;;
    *) return 0 ;;
  esac
}

wboard() {
  local query lines
  query=""
  lines=""
  if _mesh_live_is_uint "${1:-}"; then
    lines="$1"
    shift
  elif [[ -n "${1:-}" ]]; then
    query="$1"
    shift
    if _mesh_live_is_uint "${1:-}"; then
      lines="$1"
      shift
    fi
  fi
  local -a args=(live board)
  [[ -n "$query" ]] && args+=("$query")
  [[ -n "$lines" ]] && args+=(--lines "$lines")
  _mesh_live_control_run "${args[@]}" "$@"
}

wpeek() {
  if [[ $# -lt 1 ]]; then
    echo "Usage: wpeek <session-name-or-prefix> [lines]" >&2
    return 2
  fi
  _mesh_live_control_run live peek "$@"
}

wsend() {
  if [[ $# -lt 1 ]]; then
    echo "Usage: wsend <session-name-or-prefix> [text] [--enter]" >&2
    return 2
  fi
  _mesh_live_control_run live send "$@"
}

wbrief() {
  _mesh_live_control_run live brief "$@"
}

wsattach() {
  local session direct_host
  session="${1:-}"
  if [[ -z "$session" ]]; then
    echo "Usage: wsattach <session-name-or-prefix> [mesh-live-attach-options]" >&2
    return 2
  fi
  shift
  direct_host="$(_ws_mosh_host 2>/dev/null || true)"
  if [[ -n "$direct_host" && -n "$(command -v mosh 2>/dev/null)" ]]; then
    MESH_WS_HOST="$direct_host" MESH_MOSH_HOST="$direct_host" \
      _mesh_live_run live attach "$session" "$@"
  else
    _mesh_live_control_run live attach "$session" "$@"
  fi
}

wsessions() {
  _mesh_live_control_run live board --lines 0 "$@"
}

_ws_ssh_attach_or_start_once() {
  local session target_dir startup ws_host
  local -a ssh_opts=()
  session="$1"
  target_dir="$2"
  startup="$3"
  ws_host="$(_ws_control_host)" || return $?
  if command -v _mesh_collect_ssh_opts >/dev/null 2>&1; then
    local opt
    while IFS= read -r -d '' opt; do
      ssh_opts+=("$opt")
    done < <(_mesh_collect_ssh_opts)
  fi
  command ssh "${ssh_opts[@]}" -t "$ws_host" \
    "SESSION=$(printf '%q' "$session") TARGET_DIR=$(printf '%q' "$target_dir") STARTUP=$(printf '%q' "$startup") bash -lc '
set -e
if [[ ! -d \"\$TARGET_DIR\" ]]; then
  echo \"[tmux] missing repo dir: \$TARGET_DIR\" >&2
  exit 3
fi
if tmux has-session -t \"\$SESSION\" 2>/dev/null; then
  exec tmux attach -t \"\$SESSION\"
fi
if [[ -n \"\$STARTUP\" ]]; then
  tmux new-session -d -s \"\$SESSION\" -c \"\$TARGET_DIR\" \"\$STARTUP; exec \\$SHELL -l\"
else
  tmux new-session -d -s \"\$SESSION\" -c \"\$TARGET_DIR\"
fi
exec tmux attach -t \"\$SESSION\"
'"
}

_ws_ssh_attach_or_start() {
  local session target_dir startup rc
  session="$1"
  target_dir="$2"
  startup="$3"
  while true; do
    _ws_ssh_attach_or_start_once "$session" "$target_dir" "$startup"
    rc=$?
    [[ "$rc" -eq 255 ]] || return "$rc"
    printf '\n[ws] SSH disconnected. Reconnecting in 3s. Press Ctrl-C to stop.\n' >&2
    sleep 3 || return "$rc"
  done
}

_ws_mosh_attach_or_start() {
  local session target_dir startup direct_host remote_command
  session="$1"
  target_dir="$2"
  startup="${3:-}"
  direct_host="$(_ws_mosh_host 2>/dev/null || true)"
  if [[ -z "$direct_host" || -z "$(command -v mosh 2>/dev/null)" ]]; then
    _ws_ssh_attach_or_start "$session" "$target_dir" "$startup"
    return $?
  fi
  remote_command="SESSION=$(printf '%q' "$session")
TARGET_DIR=$(printf '%q' "$target_dir")
STARTUP=$(printf '%q' "$startup")
set -e
if [[ ! -d \"\$TARGET_DIR\" ]]; then
  echo \"[tmux] missing repo dir: \$TARGET_DIR\" >&2
  exit 3
fi
if tmux has-session -t \"\$SESSION\" 2>/dev/null; then
  exec tmux attach -t \"\$SESSION\"
fi
if [[ -n \"\$STARTUP\" ]]; then
  tmux new-session -d -s \"\$SESSION\" -c \"\$TARGET_DIR\" \"\$STARTUP; exec \\$SHELL -l\"
else
  tmux new-session -d -s \"\$SESSION\" -c \"\$TARGET_DIR\"
fi
exec tmux attach -t \"\$SESSION\"
"
  LANG="${MESH_MOSH_LANG:-en_US.UTF-8}" LC_ALL="${MESH_MOSH_LOCALE:-en_US.UTF-8}" \
    command mosh \
      --ssh="ssh -o ControlMaster=no -o ControlPath=none -o ServerAliveInterval=10 -o ServerAliveCountMax=18" \
      "$direct_host" -- bash -lc "$remote_command"
}

wtmux() {
  local label session
  label="${1:-}"
  session="$(_ws_tmux_session_name ws "${label:-main}")"
  _ws_ssh_attach_or_start "$session" "$(_ws_tmux_target_dir "$label")" ""
}

wclaude() {
  local label session
  label="${1:-}"
  session="$(_ws_tmux_session_name claude "${label:-main}")"
  _ws_ssh_attach_or_start "$session" "$(_ws_tmux_target_dir "$label")" ""
}

wcodex() {
  local label session
  label="${1:-}"
  session="$(_ws_tmux_session_name codex "${label:-main}")"
  _ws_ssh_attach_or_start "$session" "$(_ws_tmux_target_dir "$label")" ""
}

mtmux() {
  local label session
  label="${1:-}"
  session="$(_ws_tmux_session_name ws "${label:-main}")"
  _ws_mosh_attach_or_start "$session" "$(_ws_tmux_target_dir "$label")"
}

mclaude() {
  local label session
  label="${1:-}"
  session="$(_ws_tmux_session_name claude "${label:-main}")"
  _ws_mosh_attach_or_start "$session" "$(_ws_tmux_target_dir "$label")"
}

mcodex() {
  local label session
  label="${1:-}"
  session="$(_ws_tmux_session_name codex "${label:-main}")"
  _ws_mosh_attach_or_start "$session" "$(_ws_tmux_target_dir "$label")"
}

mcoordinator() {
  local repo worker session_override resume_id continue_mode session target_dir repo_base remote_mesh
  local prompt claude_cmd startup usage
  local -a prompt_args=()
  usage="Usage: mcoordinator [<repo>|--all] [--worker <session>] [--session <name>] [--continue|--resume <id>]"
  repo=""
  worker=""
  session_override=""
  resume_id=""
  continue_mode=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --all)
        repo=""
        shift
        ;;
      --worker)
        if [[ $# -lt 2 || -z "$2" ]]; then
          echo "$usage" >&2
          return 2
        fi
        worker="$2"
        shift 2
        ;;
      --session)
        if [[ $# -lt 2 || -z "$2" || "$2" == *[^A-Za-z0-9_.-]* ]]; then
          echo "$usage" >&2
          return 2
        fi
        session_override="$2"
        shift 2
        ;;
      --continue)
        if [[ "$continue_mode" -eq 1 || -n "$resume_id" ]]; then
          echo "$usage" >&2
          return 2
        fi
        continue_mode=1
        shift
        ;;
      --resume)
        if [[
          $# -lt 2 || -z "$2" || "$continue_mode" -eq 1 || -n "$resume_id" ||
          "$2" != [A-Za-z0-9]* || "$2" == *[^A-Za-z0-9_.-]*
        ]]; then
          echo "$usage" >&2
          return 2
        fi
        resume_id="$2"
        shift 2
        ;;
      -h|--help)
        echo "$usage"
        return 0
        ;;
      -* )
        echo "Unknown mcoordinator option: $1" >&2
        return 2
        ;;
      *)
        if [[ -n "$repo" ]]; then
          echo "$usage" >&2
          return 2
        fi
        repo="$1"
        shift
        ;;
    esac
  done

  repo_base="${MESH_WS_REPO_BASE:-/media/sam/1TB}"
  remote_mesh="${MESH_COORDINATOR_MESH_SCRIPT:-${repo_base}/gobabygo/scripts/mesh}"
  if [[ -n "$repo" ]]; then
    session="${session_override:-$(_ws_tmux_session_name claude "${repo##*/}-coordinator")}"
    target_dir="$(_ws_tmux_target_dir "$repo")"
    prompt_args=(live coordinator-prompt --repo "$repo" --session "$session" --mesh-script "$remote_mesh")
  else
    session="${session_override:-claude-coordinator}"
    target_dir="$(_ws_tmux_target_dir)"
    prompt_args=(live coordinator-prompt --all --session "$session" --mesh-script "$remote_mesh")
  fi
  [[ -n "$worker" ]] && prompt_args+=(--worker "$worker")
  prompt="$(_mesh_live_run "${prompt_args[@]}")" || return $?
  claude_cmd="${MESH_COORDINATOR_CLAUDE_CMD:-claude}"
  if [[ "$continue_mode" -eq 1 ]]; then
    startup="${claude_cmd} --continue"
  elif [[ -n "$resume_id" ]]; then
    startup="${claude_cmd} --resume $(printf '%q' "$resume_id")"
  else
    startup="${claude_cmd}"
  fi
  startup="${startup} --name $(printf '%q' "$session") --append-system-prompt $(printf '%q' "$prompt")"
  _ws_mosh_attach_or_start "$session" "$target_dir" "$startup"
}
