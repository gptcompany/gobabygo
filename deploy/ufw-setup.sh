#!/usr/bin/env bash
# ufw-setup.sh — Configure UFW for mesh router (VPS only)
set -euo pipefail

MESH_PORT="${MESH_ROUTER_PORT:-8780}"

echo "Adding UFW rule: allow port ${MESH_PORT}/tcp on wg0"
sudo ufw allow in on wg0 to any port "${MESH_PORT}" proto tcp comment "mesh-router"
sudo ufw reload
echo "UFW rule added. Current status:"
sudo ufw status numbered | grep -i mesh || true
