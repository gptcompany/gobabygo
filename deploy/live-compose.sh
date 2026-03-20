#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${MESH_COMPOSE_ENV_FILE:-/etc/mesh-router/compose.env}"
ALLOW_HYBRID_ROUTER="${MESH_ALLOW_HYBRID_ROUTER:-0}"

if command -v systemctl >/dev/null 2>&1; then
    if systemctl list-unit-files mesh-router.service >/dev/null 2>&1; then
        if systemctl is-active --quiet mesh-router.service; then
            if [[ "$ALLOW_HYBRID_ROUTER" != "1" ]]; then
                echo "[error] mesh-router.service is active on this host" >&2
                echo "[error] choose one router supervisor: Docker Compose or systemd, not both" >&2
                echo "[hint] stop the systemd router first: sudo systemctl stop mesh-router" >&2
                exit 1
            fi
            echo "[warn] mesh-router.service is active; continuing because MESH_ALLOW_HYBRID_ROUTER=1" >&2
        fi
    fi
fi

if [[ -f "$ENV_FILE" ]]; then
    if [[ ! -r "$ENV_FILE" ]]; then
        echo "[error] compose env file is not readable: $ENV_FILE" >&2
        exit 1
    fi
    exec docker compose --env-file "$ENV_FILE" -f "$SCRIPT_DIR/compose.yml" "$@"
fi

echo "[warn] compose env file not found: $ENV_FILE" >&2
echo "[warn] falling back to current shell environment" >&2
exec env COMPOSE_DISABLE_ENV_FILE=1 docker compose -f "$SCRIPT_DIR/compose.yml" "$@"
