#!/usr/bin/env bash
# iTerm2 launcher for GobabyGo operator shell on macOS.
# - Enters repo root
# - Loads mesh env from ~/.mesh/.env.mesh via dotenvx when available
# - Falls back to ~/.mesh/router.env (shell exports) if present
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DOTENV_FILE="${HOME}/.mesh/.env.mesh"
FALLBACK_EXPORTS="${HOME}/.mesh/router.env"

cd "${REPO_ROOT}"

if command -v dotenvx >/dev/null 2>&1 && [[ -f "${DOTENV_FILE}" ]]; then
  exec dotenvx run -f "${DOTENV_FILE}" -- zsh -l
fi

if [[ -f "${FALLBACK_EXPORTS}" ]]; then
  # shellcheck source=/dev/null
  source "${FALLBACK_EXPORTS}"
  exec zsh -l
fi

cat >&2 <<'EOF'
[mesh-shell] Missing env bootstrap.
Create one of:
  1) ~/.mesh/.env.mesh   (dotenv format, recommended)
       MESH_ROUTER_URL=http://10.0.0.1:8780
       MESH_AUTH_TOKEN=...
  2) ~/.mesh/router.env  (shell exports)
Then relaunch iTerm profile.
EOF
exec zsh -l
