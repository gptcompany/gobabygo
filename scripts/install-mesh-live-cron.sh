#!/usr/bin/env bash
set -euo pipefail
umask 077

BEGIN_MARKER="# >>> gobabygo-mesh-live-tick >>>"
END_MARKER="# <<< gobabygo-mesh-live-tick <<<"
INTERVAL=30
MESH_SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/mesh"
STATE_FILE="${MESH_LIVE_TICK_STATE:-$HOME/.local/state/gobabygo/mesh-live-tick.json}"
LOG_FILE="${MESH_LIVE_TICK_LOG:-$HOME/.local/state/gobabygo/mesh-live-tick.log}"
DRY_RUN=0
REMOVE=0

usage() {
  cat <<'EOF'
Usage:
  install-mesh-live-cron.sh [--interval MINUTES] [--mesh-script PATH]
                            [--state-file PATH] [--log-file PATH] [--dry-run]
  install-mesh-live-cron.sh --remove [--dry-run]

Installs one idempotent user-crontab entry for `mesh live tick --apply`, including
exact WAIT selection and one wake after an explicitly declared session reset.
No daemon, router, database, iTerm2, or root access is required.
EOF
}

fail() {
  printf 'Error: %s\n' "$*" >&2
  exit 2
}

quote_cron_arg() {
  local value="$1"
  case "$value" in
    *$'\n'*|*$'\r'*|*%*) fail "cron paths must not contain newline, carriage return, or %" ;;
  esac
  value="${value//\'/\'\"\'\"\'}"
  printf "'%s'" "$value"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --interval)
      [[ $# -ge 2 ]] || fail "--interval requires a value"
      INTERVAL="$2"
      shift 2
      ;;
    --mesh-script)
      [[ $# -ge 2 ]] || fail "--mesh-script requires a value"
      MESH_SCRIPT="$2"
      shift 2
      ;;
    --state-file)
      [[ $# -ge 2 ]] || fail "--state-file requires a value"
      STATE_FILE="$2"
      shift 2
      ;;
    --log-file)
      [[ $# -ge 2 ]] || fail "--log-file requires a value"
      LOG_FILE="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --remove)
      REMOVE=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *) fail "unknown argument: $1" ;;
  esac
done

[[ "$INTERVAL" =~ ^[0-9]+$ ]] || fail "interval must be an integer from 1 to 59"
(( INTERVAL >= 1 && INTERVAL <= 59 )) || fail "interval must be an integer from 1 to 59"
command -v crontab >/dev/null 2>&1 || fail "crontab command not found"

if (( ! REMOVE )); then
  [[ "$MESH_SCRIPT" == /* ]] || fail "--mesh-script must be an absolute path"
  [[ "$STATE_FILE" == /* ]] || fail "--state-file must be an absolute path"
  [[ "$LOG_FILE" == /* ]] || fail "--log-file must be an absolute path"
  [[ -x "$MESH_SCRIPT" ]] || fail "mesh script is not executable: $MESH_SCRIPT"
fi

tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/mesh-live-cron.XXXXXX")"
trap 'rm -rf "$tmp_dir"' EXIT
current="$tmp_dir/current"
current_error="$tmp_dir/current.error"
clean="$tmp_dir/clean"
next="$tmp_dir/next"

if ! LC_ALL=C crontab -l >"$current" 2>"$current_error"; then
  if grep -qi "no crontab" "$current_error"; then
    : >"$current"
  else
    detail="$(tr '\n' ' ' <"$current_error")"
    fail "unable to read existing crontab${detail:+: $detail}"
  fi
fi
begin_count="$(grep -Fxc "$BEGIN_MARKER" "$current" || true)"
end_count="$(grep -Fxc "$END_MARKER" "$current" || true)"
if [[ "$begin_count" != "$end_count" || "$begin_count" -gt 1 ]]; then
  fail "existing mesh live tick marker block is malformed"
fi
awk -v begin="$BEGIN_MARKER" -v end="$END_MARKER" '
  $0 == begin { skip = 1; next }
  $0 == end { skip = 0; next }
  !skip { print }
' "$current" >"$clean"

{
  cat "$clean"
  if [[ -s "$clean" ]]; then
    printf '\n'
  fi
  if (( ! REMOVE )); then
    quoted_mesh="$(quote_cron_arg "$MESH_SCRIPT")"
    quoted_state="$(quote_cron_arg "$STATE_FILE")"
    quoted_log="$(quote_cron_arg "$LOG_FILE")"
    printf '%s\n' "$BEGIN_MARKER"
    printf '*/%s * * * * MESH_LIVE_LOCAL=1 %s live tick --apply --state-file %s >>%s 2>&1\n' \
      "$INTERVAL" "$quoted_mesh" "$quoted_state" "$quoted_log"
    printf '%s\n' "$END_MARKER"
  fi
} >"$next"

if (( DRY_RUN )); then
  cat "$next"
  exit 0
fi

if (( ! REMOVE )); then
  mkdir -p "$(dirname "$STATE_FILE")" "$(dirname "$LOG_FILE")"
  touch "$LOG_FILE"
  chmod 600 "$LOG_FILE"
fi

crontab - <"$next"
if (( REMOVE )); then
  echo "Removed mesh live tick from the user crontab."
else
  echo "Installed mesh live tick every ${INTERVAL} minutes."
  echo "State: $STATE_FILE"
  echo "Log:  $LOG_FILE"
fi
