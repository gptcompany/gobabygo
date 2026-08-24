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
  local target connect_timeout wall_timeout
  target="$1"
  [[ -n "$target" ]] || return 1
  connect_timeout="${MESH_WS_PROBE_TIMEOUT:-3}"
  case "$connect_timeout" in
    ''|*[!0-9]*|0) connect_timeout=3 ;;
  esac
  wall_timeout="${MESH_WS_PROBE_WALL_TIMEOUT:-$((connect_timeout + 2))}"
  case "$wall_timeout" in
    ''|*[!0-9]*|0) wall_timeout=$((connect_timeout + 2)) ;;
  esac
  command python3 -c '
import subprocess
import sys

try:
    result = subprocess.run(sys.argv[2:], check=False, timeout=int(sys.argv[1]))
except subprocess.TimeoutExpired:
    raise SystemExit(124)
raise SystemExit(result.returncode)
' "$wall_timeout" ssh \
    -o BatchMode=yes \
    -o ControlMaster=no \
    -o ControlPath=none \
    -o ConnectionAttempts=1 \
    -o ConnectTimeout="$connect_timeout" \
    "$target" true </dev/null >/dev/null 2>&1
}

_ws_mosh_host() {
  local vpn_host lan_host
  if [[ -n "${MESH_MOSH_HOST:-}" ]]; then
    printf '%s' "$MESH_MOSH_HOST"
    return 0
  fi
  vpn_host="${MESH_WS_VPN_HOST:-sam@10.0.0.2}"
  lan_host="${MESH_WS_LAN_HOST:-sam@172.23.0.42}"
  if _ws_host_reachable "$lan_host"; then
    printf '%s' "$lan_host"
    return 0
  fi
  if _ws_host_reachable "$vpn_host"; then
    printf '%s' "$vpn_host"
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

_ws_remote_resume_guard() {
  command cat <<'MESH_REMOTE_RESUME_GUARD'
_mesh_active_resume_id() {
  local candidate pane_pid current claude_pid child_pid child_comm previous arg marker proc_root
  candidate="$1"
  pane_pid="$(tmux display-message -p -t "$candidate" "#{pane_pid}" 2>/dev/null || true)"
  current="$(tmux display-message -p -t "$candidate" "#{pane_current_command}" 2>/dev/null || true)"
  current="${current##*/}"
  claude_pid=""
  case "$current" in
    claude|claude-code) claude_pid="$pane_pid" ;;
  esac
  if [[ -z "$claude_pid" && -n "$pane_pid" ]]; then
    while IFS= read -r child_pid; do
      child_pid="${child_pid//[[:space:]]/}"
      [[ -n "$child_pid" ]] || continue
      child_comm="$(ps -o comm= -p "$child_pid" 2>/dev/null || true)"
      child_comm="${child_comm##*/}"
      child_comm="${child_comm//[[:space:]]/}"
      case "$child_comm" in
        claude|claude-code) claude_pid="$child_pid"; break ;;
      esac
    done < <(ps -o pid= --ppid "$pane_pid" 2>/dev/null || true)
  fi
  [[ -n "$claude_pid" ]] || return 1

  proc_root="${MESH_LIVE_PROC_ROOT:-/proc}"
  if [[ -r "$proc_root/$claude_pid/cmdline" ]]; then
    previous=""
    while IFS= read -r -d "" arg; do
      if [[ "$previous" == "--resume" ]]; then
        printf "%s" "$arg"
        return 0
      fi
      case "$arg" in
        --resume=*) printf "%s" "${arg#--resume=}"; return 0 ;;
      esac
      previous="$arg"
    done < "$proc_root/$claude_pid/cmdline"
  fi

  marker="$(tmux show-environment -t "$candidate" MESH_LIVE_CLAUDE_RESUME_ID 2>/dev/null || true)"
  case "$marker" in
    MESH_LIVE_CLAUDE_RESUME_ID=*) printf "%s" "${marker#MESH_LIVE_CLAUDE_RESUME_ID=}"; return 0 ;;
  esac
  return 1
}

if [[ "$SESSION_KIND" == "coordinator" && -n "$RESUME_ID" ]]; then
  while IFS= read -r candidate; do
    [[ -n "$candidate" && "$candidate" != "$SESSION" ]] || continue
    active_resume="$(_mesh_active_resume_id "$candidate" || true)"
    if [[ "$active_resume" == "$RESUME_ID" ]]; then
      echo "[tmux] Claude resume session is already active in tmux session: $candidate" >&2
      exit 6
    fi
  done < <(tmux list-sessions -F "#{session_name}" 2>/dev/null || true)

  lock_base="${XDG_RUNTIME_DIR:-$HOME/.local/state/gobabygo}"
  lock_file="$lock_base/mesh-live-resume-locks/$RESUME_ID.lock"
  if [[ -e "$lock_file" ]]; then
    if [[ -L "$lock_file" ]]; then
      echo "[tmux] refusing symlinked Claude resume lock: $lock_file" >&2
      exit 6
    fi
    if ! flock -n "$lock_file" true 2>/dev/null; then
      echo "[tmux] Claude resume session is already locked by another coordinator" >&2
      exit 6
    fi
  fi
fi
MESH_REMOTE_RESUME_GUARD
}

_ws_remote_locked_startup() {
  command cat <<'MESH_REMOTE_LOCKED_STARTUP'
session_command="$STARTUP"
session_handles_shell=0
if [[ "$SESSION_KIND" == "coordinator" && -n "$RESUME_ID" ]]; then
  if ! command -v flock >/dev/null 2>&1; then
    echo "[tmux] flock is required for deterministic Claude resume safety" >&2
    exit 6
  fi
  lock_base="${XDG_RUNTIME_DIR:-$HOME/.local/state/gobabygo}"
  lock_dir="$lock_base/mesh-live-resume-locks"
  mkdir -p "$lock_dir"
  chmod 700 "$lock_dir"
  lock_file="$lock_dir/$RESUME_ID.lock"
  if [[ -L "$lock_file" ]]; then
    echo "[tmux] refusing symlinked Claude resume lock: $lock_file" >&2
    exit 6
  fi
  (umask 077; : >> "$lock_file")
  chmod 600 "$lock_file"
  printf -v lock_file_q "%q" "$lock_file"
  printf -v startup_q "%q" "$STARTUP"
  session_command="exec 9>>$lock_file_q; if ! flock -n 9; then echo \"[tmux] Claude resume session is already locked by another coordinator\" >&2; sleep 2; exit 73; fi; eval $startup_q; flock -u 9; exec \$SHELL -l"
  session_handles_shell=1
fi
MESH_REMOTE_LOCKED_STARTUP
}

_ws_remote_coordinator_git_guard() {
  command cat <<'MESH_REMOTE_COORDINATOR_GIT_GUARD'
if [[ "$SESSION_KIND" == "coordinator" ]]; then
  target_real="$(cd "$TARGET_DIR" 2>/dev/null && pwd -P)"
  git_root="$(git -C "$TARGET_DIR" rev-parse --show-toplevel 2>/dev/null || true)"
  git_real="$(cd "$git_root" 2>/dev/null && pwd -P)"
  if [[ -z "$target_real" || -z "$git_real" || "$target_real" != "$git_real" ]]; then
    echo "[tmux] coordinator target must be an exact Git root: $TARGET_DIR" >&2
    exit 7
  fi
fi
MESH_REMOTE_COORDINATOR_GIT_GUARD
}

_ws_remote_tmux_create() {
  command cat <<'MESH_REMOTE_TMUX_CREATE'
if [[ -n "$STARTUP" ]]; then
  start_dir="$HOME/.local/state/gobabygo/mesh-live-session-start"
  mkdir -p "$start_dir"
  if [[ -L "$start_dir" ]]; then
    echo "[tmux] refusing symlinked startup directory: $start_dir" >&2
    exit 8
  fi
  chmod 700 "$start_dir"
  start_file="$(mktemp "$start_dir/start.XXXXXX")"
  chmod 600 "$start_file"
  if [[ "$session_handles_shell" -ne 1 ]]; then
    session_command="$session_command; exec \$SHELL -l"
  fi
  {
    printf "#!/usr/bin/env bash\n"
    printf "rm -f -- \\\"%s\\\"\n" "$start_file"
    printf "%s\n" "$session_command"
  } >"$start_file"
  printf -v start_file_q "%q" "$start_file"
  if ! tmux new-session -d -s "$SESSION" -c "$TARGET_DIR" "bash $start_file_q"; then
    rm -f -- "$start_file"
    exit 1
  fi
else
  tmux new-session -d -s "$SESSION" -c "$TARGET_DIR"
fi
MESH_REMOTE_TMUX_CREATE
}

_ws_remote_tmux_history() {
  command cat <<'MESH_REMOTE_TMUX_HISTORY'
if ! tmux set-option -g history-limit 20000 >/dev/null 2>&1; then
  echo "[tmux] warning: could not set global history-limit" >&2
fi
if ! tmux set-option -w -t "$SESSION" history-limit 20000 >/dev/null 2>&1; then
  echo "[tmux] warning: could not set session history-limit: $SESSION" >&2
fi
MESH_REMOTE_TMUX_HISTORY
}

_ws_ssh_attach_or_start_once() {
  local session target_dir startup resume_id session_kind ws_host resume_guard locked_startup git_guard tmux_create tmux_history
  local -a ssh_opts=()
  session="$1"
  target_dir="$2"
  startup="$3"
  resume_id="${4:-}"
  session_kind="${5:-}"
  resume_guard="$(_ws_remote_resume_guard)"
  locked_startup="$(_ws_remote_locked_startup)"
  git_guard="$(_ws_remote_coordinator_git_guard)"
  tmux_create="$(_ws_remote_tmux_create)"
  tmux_history="$(_ws_remote_tmux_history)"
  ws_host="$(_ws_control_host)" || return $?
  if command -v _mesh_collect_ssh_opts >/dev/null 2>&1; then
    local opt
    while IFS= read -r -d '' opt; do
      ssh_opts+=("$opt")
    done < <(_mesh_collect_ssh_opts)
  fi
  command ssh "${ssh_opts[@]}" -t "$ws_host" \
    "SESSION=$(printf '%q' "$session") TARGET_DIR=$(printf '%q' "$target_dir") STARTUP=$(printf '%q' "$startup") RESUME_ID=$(printf '%q' "$resume_id") SESSION_KIND=$(printf '%q' "$session_kind") bash -lc '
set -e
if [[ ! -d \"\$TARGET_DIR\" ]]; then
  echo \"[tmux] missing repo dir: \$TARGET_DIR\" >&2
  exit 3
fi
$git_guard
$resume_guard
if tmux has-session -t \"\$SESSION\" 2>/dev/null; then
  if [[ \"\$SESSION_KIND\" == \"coordinator\" ]]; then
    pane_path=\"\$(tmux display-message -p -t \"\$SESSION\" \"#{pane_current_path}\" 2>/dev/null || true)\"
    pane_real=\"\$(cd \"\$pane_path\" 2>/dev/null && pwd -P)\"
    if [[ -z \"\$pane_real\" || \"\$pane_real\" != \"\$target_real\" ]]; then
      echo \"[tmux] existing coordinator session \$SESSION targets a different Git root\" >&2
      exit 5
    fi
    marker=\"\$(tmux show-environment -t \"\$SESSION\" MESH_LIVE_COORDINATOR 2>/dev/null || true)\"
    current=\"\$(tmux display-message -p -t \"\$SESSION\" \"#{pane_current_command}\" 2>/dev/null || true)\"
    current=\"\${current##*/}\"
    pane_pid=\"\$(tmux display-message -p -t \"\$SESSION\" \"#{pane_pid}\" 2>/dev/null || true)\"
    claude_child=\"\"
    if [[ -n \"\$pane_pid\" ]]; then
      claude_child=\"\$(ps -o comm= --ppid \"\$pane_pid\" 2>/dev/null | grep -E -m 1 \"^[[:space:]]*(claude|claude-code)[[:space:]]*\$\" || true)\"
    fi
    if [[ \"\$current\" == \"claude\" || \"\$current\" == \"claude-code\" || -n \"\$claude_child\" ]]; then
      :
    else
      case \"\$current\" in
        bash|zsh|sh|fish)
          if [[ \"\$marker\" == \"MESH_LIVE_COORDINATOR=1\" ]]; then
            echo \"[tmux] existing coordinator session \$SESSION no longer has a running Claude process\" >&2
          else
            echo \"[tmux] existing session \$SESSION is a shell, not a Claude coordinator\" >&2
          fi
          echo \"[tmux] use mcoordinator --session <fresh-name> to bootstrap, or wsattach \$SESSION to inspect it\" >&2
          exit 5
          ;;
      esac
    fi
  fi
  $tmux_history
  exec tmux attach -t \"\$SESSION\"
fi
if [[ -n \"\$RESUME_ID\" ]]; then
  case \"\$RESUME_ID\" in
    ????????-????-????-????-????????????) ;;
    *) echo \"[tmux] invalid Claude resume session ID\" >&2; exit 4 ;;
  esac
  encoded_dir=\"\${TARGET_DIR//\//-}\"
  claude_config=\"\${CLAUDE_CONFIG_DIR:-\$HOME/.claude}\"
  resume_file=\"\$claude_config/projects/\$encoded_dir/\$RESUME_ID.jsonl\"
  if [[ ! -f \"\$resume_file\" ]]; then
    echo \"[tmux] Claude resume session not found in target directory: \$TARGET_DIR\" >&2
    exit 4
  fi
fi
$locked_startup
$tmux_create
$tmux_history
if [[ \"\$SESSION_KIND\" == \"coordinator\" ]]; then
  tmux set-environment -t \"\$SESSION\" MESH_LIVE_COORDINATOR 1
  if [[ -n \"\$RESUME_ID\" ]]; then
    tmux set-environment -t \"\$SESSION\" MESH_LIVE_CLAUDE_RESUME_ID \"\$RESUME_ID\"
  fi
fi
exec tmux attach -t \"\$SESSION\"
'"
}

_ws_ssh_attach_or_start() {
  local session target_dir startup resume_id session_kind rc retries max_retries retry_delay
  session="$1"
  target_dir="$2"
  startup="$3"
  resume_id="${4:-}"
  session_kind="${5:-}"
  max_retries="${MESH_WS_SSH_RECONNECT_ATTEMPTS:-3}"
  case "$max_retries" in
    ''|*[!0-9]*) max_retries=3 ;;
  esac
  retry_delay="${MESH_WS_SSH_RECONNECT_DELAY:-3}"
  case "$retry_delay" in
    ''|*[!0-9]*) retry_delay=3 ;;
  esac
  retries=0
  while true; do
    _ws_ssh_attach_or_start_once "$session" "$target_dir" "$startup" "$resume_id" "$session_kind"
    rc=$?
    [[ "$rc" -eq 255 ]] || return "$rc"
    if [[ "$retries" -ge "$max_retries" ]]; then
      printf '\n[ws] SSH unavailable after %s reconnect attempt(s); tmux was not stopped.\n' "$retries" >&2
      return "$rc"
    fi
    retries=$((retries + 1))
    printf '\n[ws] SSH disconnected. Reconnecting in %ss (%s/%s). Press Ctrl-C to stop.\n' \
      "$retry_delay" "$retries" "$max_retries" >&2
    sleep "$retry_delay" || return "$rc"
  done
}

_ws_mosh_preflight_attach_or_start() {
  local session target_dir resume_id session_kind direct_host resume_guard git_guard
  session="$1"
  target_dir="$2"
  resume_id="${3:-}"
  session_kind="${4:-}"
  direct_host="$5"
  resume_guard="$(_ws_remote_resume_guard)"
  git_guard="$(_ws_remote_coordinator_git_guard)"
  command ssh \
    -o ControlMaster=no -o ControlPath=none -o ConnectTimeout=10 \
    "$direct_host" \
    "SESSION=$(printf '%q' "$session") TARGET_DIR=$(printf '%q' "$target_dir") RESUME_ID=$(printf '%q' "$resume_id") SESSION_KIND=$(printf '%q' "$session_kind") bash -lc '
if [[ ! -d \"\$TARGET_DIR\" ]]; then
  echo \"[tmux] missing repo dir: \$TARGET_DIR\" >&2
  exit 3
fi
$git_guard
$resume_guard
if tmux has-session -t \"\$SESSION\" 2>/dev/null; then
  if [[ \"\$SESSION_KIND\" == \"coordinator\" ]]; then
    pane_path=\"\$(tmux display-message -p -t \"\$SESSION\" \"#{pane_current_path}\" 2>/dev/null || true)\"
    pane_real=\"\$(cd \"\$pane_path\" 2>/dev/null && pwd -P)\"
    if [[ -z \"\$pane_real\" || \"\$pane_real\" != \"\$target_real\" ]]; then
      echo \"[tmux] existing coordinator session \$SESSION targets a different Git root\" >&2
      exit 5
    fi
    marker=\"\$(tmux show-environment -t \"\$SESSION\" MESH_LIVE_COORDINATOR 2>/dev/null || true)\"
    current=\"\$(tmux display-message -p -t \"\$SESSION\" \"#{pane_current_command}\" 2>/dev/null || true)\"
    current=\"\${current##*/}\"
    pane_pid=\"\$(tmux display-message -p -t \"\$SESSION\" \"#{pane_pid}\" 2>/dev/null || true)\"
    claude_child=\"\"
    if [[ -n \"\$pane_pid\" ]]; then
      claude_child=\"\$(ps -o comm= --ppid \"\$pane_pid\" 2>/dev/null | grep -E -m 1 \"^[[:space:]]*(claude|claude-code)[[:space:]]*\$\" || true)\"
    fi
    if [[ \"\$current\" == \"claude\" || \"\$current\" == \"claude-code\" || -n \"\$claude_child\" ]]; then
      :
    else
      case \"\$current\" in
        bash|zsh|sh|fish)
          if [[ \"\$marker\" == \"MESH_LIVE_COORDINATOR=1\" ]]; then
            echo \"[tmux] existing coordinator session \$SESSION no longer has a running Claude process\" >&2
          else
            echo \"[tmux] existing session \$SESSION is a shell, not a Claude coordinator\" >&2
          fi
          echo \"[tmux] use mcoordinator --session <fresh-name> to bootstrap, or wsattach \$SESSION to inspect it\" >&2
          exit 5
          ;;
      esac
    fi
  fi
  exit 0
fi
if [[ -n \"\$RESUME_ID\" ]]; then
  case \"\$RESUME_ID\" in
    ????????-????-????-????-????????????) ;;
    *) echo \"[tmux] invalid Claude resume session ID\" >&2; exit 4 ;;
  esac
  encoded_dir=\"\${TARGET_DIR//\//-}\"
  claude_config=\"\${CLAUDE_CONFIG_DIR:-\$HOME/.claude}\"
  resume_file=\"\$claude_config/projects/\$encoded_dir/\$RESUME_ID.jsonl\"
  if [[ ! -f \"\$resume_file\" ]]; then
    echo \"[tmux] Claude resume session not found in target directory: \$TARGET_DIR\" >&2
    exit 4
  fi
fi
'"
}

_ws_stage_mosh_command() {
  local direct_host remote_command stage_command
  local -a ssh_opts=()
  direct_host="$1"
  remote_command="$2"
  if command -v _mesh_collect_ssh_opts >/dev/null 2>&1; then
    local opt
    while IFS= read -r -d '' opt; do
      ssh_opts+=("$opt")
    done < <(_mesh_collect_ssh_opts)
  fi
  stage_command='set -e
launch_dir="$HOME/.local/state/gobabygo/mesh-live-launch"
mkdir -p "$launch_dir"
if [ -L "$launch_dir" ]; then
  echo "[mosh] refusing symlinked launch directory: $launch_dir" >&2
  exit 1
fi
chmod 700 "$launch_dir"
launch_file="$(mktemp "$launch_dir/launch.XXXXXX")"
chmod 600 "$launch_file"
printf "rm -f -- \\\"%s\\\"\\n" "$launch_file" >"$launch_file"
if ! cat >>"$launch_file"; then
  rm -f -- "$launch_file"
  exit 1
fi
printf "%s" "$launch_file"'
  {
    printf '%s\n' "$remote_command"
  } | command ssh "${ssh_opts[@]}" -o BatchMode=yes -o ConnectTimeout=10 \
    "$direct_host" "$stage_command"
}

_ws_remove_staged_mosh_command() {
  local direct_host launch_file
  local -a ssh_opts=()
  direct_host="$1"
  launch_file="$2"
  [[ -n "$launch_file" ]] || return 0
  if command -v _mesh_collect_ssh_opts >/dev/null 2>&1; then
    local opt
    while IFS= read -r -d '' opt; do
      ssh_opts+=("$opt")
    done < <(_mesh_collect_ssh_opts)
  fi
  command ssh "${ssh_opts[@]}" -o BatchMode=yes -o ConnectTimeout=10 \
    "$direct_host" "rm -f -- $(printf '%q' "$launch_file")" </dev/null >/dev/null 2>&1
}

_ws_mosh_attach_or_start() {
  local session target_dir startup resume_id session_kind direct_host remote_command remote_script rc
  local resume_guard locked_startup git_guard tmux_create tmux_history
  session="$1"
  target_dir="$2"
  startup="${3:-}"
  resume_id="${4:-}"
  session_kind="${5:-}"
  resume_guard="$(_ws_remote_resume_guard)"
  locked_startup="$(_ws_remote_locked_startup)"
  git_guard="$(_ws_remote_coordinator_git_guard)"
  tmux_create="$(_ws_remote_tmux_create)"
  tmux_history="$(_ws_remote_tmux_history)"
  direct_host="$(_ws_mosh_host 2>/dev/null || true)"
  if [[ -z "$direct_host" || -z "$(command -v mosh 2>/dev/null)" ]]; then
    _ws_ssh_attach_or_start "$session" "$target_dir" "$startup" "$resume_id" "$session_kind"
    return $?
  fi
  if _ws_mosh_preflight_attach_or_start \
    "$session" "$target_dir" "$resume_id" "$session_kind" "$direct_host"; then
    :
  else
    rc=$?
    case "$rc" in
      3|4|5|6|7|130|143) return "$rc" ;;
    esac
    printf '\n[ws] mosh preflight failed (exit %s); falling back to SSH.\n' "$rc" >&2
    _ws_ssh_attach_or_start "$session" "$target_dir" "$startup" "$resume_id" "$session_kind"
    return $?
  fi
  remote_command="SESSION=$(printf '%q' "$session")
TARGET_DIR=$(printf '%q' "$target_dir")
STARTUP=$(printf '%q' "$startup")
RESUME_ID=$(printf '%q' "$resume_id")
SESSION_KIND=$(printf '%q' "$session_kind")
set -e
if [[ ! -d \"\$TARGET_DIR\" ]]; then
  echo \"[tmux] missing repo dir: \$TARGET_DIR\" >&2
  exit 3
fi
$git_guard
$resume_guard
if tmux has-session -t \"\$SESSION\" 2>/dev/null; then
  if [[ \"\$SESSION_KIND\" == \"coordinator\" ]]; then
    pane_path=\"\$(tmux display-message -p -t \"\$SESSION\" \"#{pane_current_path}\" 2>/dev/null || true)\"
    pane_real=\"\$(cd \"\$pane_path\" 2>/dev/null && pwd -P)\"
    if [[ -z \"\$pane_real\" || \"\$pane_real\" != \"\$target_real\" ]]; then
      echo \"[tmux] existing coordinator session \$SESSION targets a different Git root\" >&2
      exit 5
    fi
    marker=\"\$(tmux show-environment -t \"\$SESSION\" MESH_LIVE_COORDINATOR 2>/dev/null || true)\"
    current=\"\$(tmux display-message -p -t \"\$SESSION\" \"#{pane_current_command}\" 2>/dev/null || true)\"
    current=\"\${current##*/}\"
    pane_pid=\"\$(tmux display-message -p -t \"\$SESSION\" \"#{pane_pid}\" 2>/dev/null || true)\"
    claude_child=\"\"
    if [[ -n \"\$pane_pid\" ]]; then
      claude_child=\"\$(ps -o comm= --ppid \"\$pane_pid\" 2>/dev/null | grep -E -m 1 \"^[[:space:]]*(claude|claude-code)[[:space:]]*\$\" || true)\"
    fi
    if [[ \"\$current\" == \"claude\" || \"\$current\" == \"claude-code\" || -n \"\$claude_child\" ]]; then
      :
    else
      case \"\$current\" in
        bash|zsh|sh|fish)
          if [[ \"\$marker\" == \"MESH_LIVE_COORDINATOR=1\" ]]; then
            echo \"[tmux] existing coordinator session \$SESSION no longer has a running Claude process\" >&2
          else
            echo \"[tmux] existing session \$SESSION is a shell, not a Claude coordinator\" >&2
          fi
          echo \"[tmux] use mcoordinator --session <fresh-name> to bootstrap, or wsattach \$SESSION to inspect it\" >&2
          exit 5
          ;;
      esac
    fi
  fi
  $tmux_history
  exec tmux attach -t \"\$SESSION\"
fi
if [[ -n \"\$RESUME_ID\" ]]; then
  case \"\$RESUME_ID\" in
    ????????-????-????-????-????????????) ;;
    *) echo \"[tmux] invalid Claude resume session ID\" >&2; exit 4 ;;
  esac
  encoded_dir=\"\${TARGET_DIR//\//-}\"
  claude_config=\"\${CLAUDE_CONFIG_DIR:-\$HOME/.claude}\"
  resume_file=\"\$claude_config/projects/\$encoded_dir/\$RESUME_ID.jsonl\"
  if [[ ! -f \"\$resume_file\" ]]; then
    echo \"[tmux] Claude resume session not found in target directory: \$TARGET_DIR\" >&2
    exit 4
  fi
fi
$locked_startup
$tmux_create
$tmux_history
if [[ \"\$SESSION_KIND\" == \"coordinator\" ]]; then
  tmux set-environment -t \"\$SESSION\" MESH_LIVE_COORDINATOR 1
  if [[ -n \"\$RESUME_ID\" ]]; then
    tmux set-environment -t \"\$SESSION\" MESH_LIVE_CLAUDE_RESUME_ID \"\$RESUME_ID\"
  fi
fi
exec tmux attach -t \"\$SESSION\"
"
  if remote_script="$(_ws_stage_mosh_command "$direct_host" "$remote_command")"; then
    :
  else
    rc=$?
    printf '\n[ws] mosh command staging failed (exit %s); falling back to SSH.\n' "$rc" >&2
    _ws_ssh_attach_or_start "$session" "$target_dir" "$startup" "$resume_id" "$session_kind"
    return $?
  fi
  if LANG="${MESH_MOSH_LANG:-en_US.UTF-8}" LC_ALL="${MESH_MOSH_LOCALE:-en_US.UTF-8}" \
    command mosh \
      --ssh="ssh -o ControlMaster=no -o ControlPath=none -o ServerAliveInterval=10 -o ServerAliveCountMax=18" \
      "$direct_host" -- bash "$remote_script"; then
    _ws_remove_staged_mosh_command "$direct_host" "$remote_script" || true
    return 0
  else
    rc=$?
  fi
  _ws_remove_staged_mosh_command "$direct_host" "$remote_script" || true
  case "$rc" in
    130|143) return "$rc" ;;
  esac
  printf '\n[ws] mosh attach failed (exit %s); falling back to SSH.\n' "$rc" >&2
  _ws_ssh_attach_or_start "$session" "$target_dir" "$startup" "$resume_id" "$session_kind"
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

_ws_remote_speckit_status() {
  local mesh_script target_dir control_host remote_script remote_command opt
  local status_timeout transport_timeout
  local -a ssh_opts=()
  mesh_script="$1"
  target_dir="$2"
  status_timeout="${MESH_COORDINATOR_STATUS_TIMEOUT:-20}"
  if ! _mesh_live_is_uint "$status_timeout" || [[ "$status_timeout" -eq 0 ]]; then
    status_timeout=20
  fi
  transport_timeout=$((status_timeout + 10))
  control_host="$(_ws_control_host)" || return $?
  if command -v _mesh_collect_ssh_opts >/dev/null 2>&1; then
    while IFS= read -r -d '' opt; do
      ssh_opts+=("$opt")
    done < <(_mesh_collect_ssh_opts)
  fi
  remote_script='if [[ ! -x "$MESH_SCRIPT" || ! -d "$TARGET_DIR" ]]; then exit 3; fi
command -v timeout >/dev/null 2>&1 || exit 4
output="$(timeout --signal=TERM --kill-after=2s "${STATUS_TIMEOUT}s" "$MESH_SCRIPT" speckit status "$TARGET_DIR" --json)"
rc=$?
if [[ $rc -ne 0 && $rc -ne 1 ]]; then exit $rc; fi
printf "%s" "$output"'
  printf -v remote_command 'MESH_SCRIPT=%q TARGET_DIR=%q STATUS_TIMEOUT=%q bash -lc %q' \
    "$mesh_script" "$target_dir" "$status_timeout" "$remote_script"
  command python3 -c '
import subprocess
import sys

try:
    result = subprocess.run(sys.argv[2:], check=False, timeout=int(sys.argv[1]))
except subprocess.TimeoutExpired:
    raise SystemExit(124)
raise SystemExit(result.returncode)
' "$transport_timeout" ssh "${ssh_opts[@]}" -o BatchMode=yes \
    -o ConnectTimeout=10 -o ServerAliveInterval=5 -o ServerAliveCountMax=2 \
    "$control_host" "$remote_command"
}

mcoordinator() {
  local repo worker workflow session_override resume_id continue_mode session target_dir repo_base remote_mesh state_repo
  local prompt claude_cmd startup usage speckit_status_json
  local -a prompt_args=()
  usage="Usage: mcoordinator [<repo>|--all] [--workflow direct|speckit|adaptive] [--worker <session>] [--session <name>] [--continue|--resume <id>]"
  repo=""
  worker=""
  workflow="${MESH_COORDINATOR_WORKFLOW:-adaptive}"
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
      --workflow)
        if [[ $# -lt 2 ]]; then
          echo "$usage" >&2
          return 2
        fi
        case "$2" in
          direct|speckit|adaptive) workflow="$2" ;;
          *)
            echo "$usage" >&2
            return 2
            ;;
        esac
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
          ! "$2" =~ ^[A-Fa-f0-9]{8}-[A-Fa-f0-9]{4}-[A-Fa-f0-9]{4}-[A-Fa-f0-9]{4}-[A-Fa-f0-9]{12}$
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
    prompt_args=(live coordinator-prompt --repo "$repo" --repo-root "$target_dir" --session "$session" --mesh-script "$remote_mesh" --workflow "$workflow")
  else
    session="${session_override:-claude-coordinator}"
    state_repo="${MESH_COORDINATOR_STATE_REPO:-${repo_base}/coordination}"
    case "$state_repo" in
      /*) ;;
      *) echo "MESH_COORDINATOR_STATE_REPO must be an absolute path" >&2; return 2 ;;
    esac
    target_dir="$state_repo"
    prompt_args=(live coordinator-prompt --all --session "$session" --mesh-script "$remote_mesh" --workflow "$workflow")
  fi
  [[ -n "$worker" ]] && prompt_args+=(--worker "$worker")
  if [[ -n "${MESH_COORDINATOR_SPECKIT_STATUS_JSON+x}" ]]; then
    speckit_status_json="$MESH_COORDINATOR_SPECKIT_STATUS_JSON"
  else
    speckit_status_json="$(_ws_remote_speckit_status "$remote_mesh" "$target_dir" 2>/dev/null || true)"
  fi
  if [[ ${#speckit_status_json} -gt 16384 ]]; then
    speckit_status_json=""
  fi
  [[ -n "$speckit_status_json" ]] && prompt_args+=(--speckit-status-json "$speckit_status_json")
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
  startup="CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN=1 ${startup}"
  _ws_mosh_attach_or_start "$session" "$target_dir" "$startup" "$resume_id" coordinator
}
