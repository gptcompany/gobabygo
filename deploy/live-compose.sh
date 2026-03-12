#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${MESH_COMPOSE_ENV_FILE:-/etc/mesh-router/compose.env}"

if [[ -f "$ENV_FILE" ]]; then
    if [[ ! -r "$ENV_FILE" ]]; then
        echo "[error] compose env file is not readable: $ENV_FILE" >&2
        exit 1
    fi
    exec docker compose --env-file "$ENV_FILE" -f "$SCRIPT_DIR/compose.yml" "$@"
fi

echo "[warn] compose env file not found: $ENV_FILE" >&2
echo "[warn] falling back to current shell environment" >&2
exec docker compose -f "$SCRIPT_DIR/compose.yml" "$@"
