#!/usr/bin/env bash
# set-mesh-token.sh
# Generate/update MESH_AUTH_TOKEN across router + workers and local operator env.
#
# Typical usage:
#   ./scripts/set-mesh-token.sh --generate
#   ./scripts/set-mesh-token.sh --token <TOKEN>
#
# Optional interactive secret store handoff:
#   ./scripts/set-mesh-token.sh --generate --secret-add MESH_AUTH_TOKEN
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  set-mesh-token.sh [options]

Options:
  --generate                     Generate a new token (openssl rand -hex 32)
  --token <value>                Use an existing token instead of generating
  --vps-host <ssh_host>          Router host (default: root@10.0.0.1)
  --ws-host <ssh_host>           Worker host (default: sam@10.0.0.2)
  --router-url <url>             Router URL for local env (default: http://10.0.0.1:8780)
  --router-env-path <path>       Router env path (default: /etc/mesh-router/mesh-router.env)
  --local-env-path <path>        Local env file path (default: ~/.mesh/router.env)
  --skip-router                  Do not update/restart router env
  --skip-worker                  Do not update/restart worker envs
  --no-local                     Do not write local ~/.mesh/router.env
  --secret-add <name>            Run interactive `secret-add <name>` at the end
  --dry-run                      Print planned actions only
  -h, --help                     Show this help

Notes:
  - The token is shared by router, workers, and meshctl clients.
  - This script masks token output; it does not print the full token.
USAGE
}

mask_token() {
  local token="$1"
  if [[ ${#token} -le 10 ]]; then
    printf '%s' '***'
    return
  fi
  printf '%s...%s' "${token:0:6}" "${token: -4}"
}

generate_token() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32
    return
  fi
  python3 - <<'PY'
import secrets
print(secrets.token_hex(32))
PY
}

TOKEN=""
GENERATE=0
VPS_HOST="root@10.0.0.1"
WS_HOST="sam@10.0.0.2"
ROUTER_URL="http://10.0.0.1:8780"
ROUTER_ENV_PATH="/etc/mesh-router/mesh-router.env"
LOCAL_ENV_PATH="${HOME}/.mesh/router.env"
SKIP_ROUTER=0
SKIP_WORKER=0
NO_LOCAL=0
DRY_RUN=0
SECRET_ADD_NAME=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --generate)
      GENERATE=1
      shift
      ;;
    --token)
      TOKEN="${2:-}"
      shift 2
      ;;
    --vps-host)
      VPS_HOST="${2:-}"
      shift 2
      ;;
    --ws-host)
      WS_HOST="${2:-}"
      shift 2
      ;;
    --router-url)
      ROUTER_URL="${2:-}"
      shift 2
      ;;
    --router-env-path)
      ROUTER_ENV_PATH="${2:-}"
      shift 2
      ;;
    --local-env-path)
      LOCAL_ENV_PATH="${2:-}"
      shift 2
      ;;
    --skip-router)
      SKIP_ROUTER=1
      shift
      ;;
    --skip-worker)
      SKIP_WORKER=1
      shift
      ;;
    --no-local)
      NO_LOCAL=1
      shift
      ;;
    --secret-add)
      SECRET_ADD_NAME="${2:-}"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ -n "$TOKEN" && "$GENERATE" -eq 1 ]]; then
  echo "Use either --token or --generate, not both." >&2
  exit 1
fi

if [[ -z "$TOKEN" ]]; then
  if [[ "$GENERATE" -eq 1 ]]; then
    TOKEN="$(generate_token)"
  else
    echo "Missing token. Use --generate or --token <value>." >&2
    exit 1
  fi
fi

# Restrict token charset to keep remote replacement safe/predictable.
if ! [[ "$TOKEN" =~ ^[A-Za-z0-9._-]{16,}$ ]]; then
  echo "Token format rejected. Use at least 16 chars from [A-Za-z0-9._-]." >&2
  exit 1
fi

TOKEN_MASKED="$(mask_token "$TOKEN")"
echo "Token: ${TOKEN_MASKED}"

if [[ "$SKIP_ROUTER" -eq 0 ]]; then
  echo "[router] ${VPS_HOST}:${ROUTER_ENV_PATH}"
  ssh "$VPS_HOST" bash -s -- "$ROUTER_ENV_PATH" "$TOKEN" "$DRY_RUN" <<'REMOTE_ROUTER'
set -euo pipefail
env_path="$1"
token="$2"
dry_run="$3"

if [[ ! -f "$env_path" ]]; then
  echo "Router env file not found: $env_path" >&2
  exit 1
fi

escaped="$(printf '%s' "$token" | sed 's/[\/&]/\\&/g')"
if [[ "$dry_run" == "1" ]]; then
  echo "DRY-RUN: would update $env_path and restart mesh-router"
  exit 0
fi

sudo sed -i "s|^MESH_AUTH_TOKEN=.*|MESH_AUTH_TOKEN=$escaped|" "$env_path"
sudo systemctl restart mesh-router
sudo systemctl is-active --quiet mesh-router
echo "Router updated and restarted."
REMOTE_ROUTER
fi

if [[ "$SKIP_WORKER" -eq 0 ]]; then
  echo "[worker] ${WS_HOST}:/etc/mesh-worker/*.env"
  ssh "$WS_HOST" bash -s -- "$TOKEN" "$DRY_RUN" <<'REMOTE_WORKER'
set -euo pipefail
token="$1"
dry_run="$2"

shopt -s nullglob
files=(/etc/mesh-worker/*.env)
if [[ ${#files[@]} -eq 0 ]]; then
  echo "No worker env files found under /etc/mesh-worker" >&2
  exit 1
fi

escaped="$(printf '%s' "$token" | sed 's/[\/&]/\\&/g')"
if [[ "$dry_run" == "1" ]]; then
  echo "DRY-RUN: would update ${#files[@]} worker env files and restart services"
  exit 0
fi

for f in "${files[@]}"; do
  sudo sed -i "s|^MESH_AUTH_TOKEN=.*|MESH_AUTH_TOKEN=$escaped|" "$f"
done

sudo systemctl daemon-reload

# Session workers (primary)
for inst in mesh-session-claude-work mesh-session-codex-work mesh-session-codex-review; do
  if [[ -f "/etc/mesh-worker/${inst}.env" ]]; then
    sudo systemctl restart "mesh-session-worker@${inst}" || true
  fi
done

# Review worker (if deployed)
if [[ -f "/etc/mesh-worker/mesh-review-codex.env" ]]; then
  sudo systemctl restart mesh-review-worker@mesh-review-codex || true
fi

echo "Worker env files updated and services restarted."
REMOTE_WORKER
fi

if [[ "$NO_LOCAL" -eq 0 ]]; then
  local_dir="$(dirname "$LOCAL_ENV_PATH")"
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "[local] DRY-RUN would write ${LOCAL_ENV_PATH}"
  else
    if [[ ! -d "$local_dir" ]]; then
      mkdir -p "$local_dir"
      chmod 700 "$local_dir"
    elif [[ "$local_dir" == "$HOME"* ]]; then
      chmod 700 "$local_dir" || true
    fi
    cat >"$LOCAL_ENV_PATH" <<EOF
export MESH_ROUTER_URL=${ROUTER_URL}
export MESH_AUTH_TOKEN=${TOKEN}
EOF
    chmod 600 "$LOCAL_ENV_PATH"
    echo "[local] wrote ${LOCAL_ENV_PATH}"
  fi
fi

if [[ -n "$SECRET_ADD_NAME" ]]; then
  if command -v secret-add >/dev/null 2>&1; then
    echo "[secret-add] launching interactive command: secret-add ${SECRET_ADD_NAME}"
    echo "Use the token stored in ${LOCAL_ENV_PATH} when prompted."
    secret-add "${SECRET_ADD_NAME}"
  else
    echo "[secret-add] command not found; skipping interactive secret store update." >&2
  fi
fi

echo "Done."
