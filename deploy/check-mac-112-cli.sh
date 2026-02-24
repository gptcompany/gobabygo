#!/usr/bin/env bash
# Verify macOS operator host (.112) has Claude/Codex/Gemini CLIs and Claude Agent Teams flag.
# VPN-first: tries 10.0.0.112 before LAN fallback 192.168.1.112.
set -euo pipefail

USER_NAME="${1:-sam}"
HOSTS=("10.0.0.112" "192.168.1.112")

TARGET=""
for host in "${HOSTS[@]}"; do
  if ssh -o ConnectTimeout=10 "${USER_NAME}@${host}" 'zsh -lic "echo ok"' >/dev/null 2>&1; then
    TARGET="${USER_NAME}@${host}"
    break
  fi
done

if [[ -z "$TARGET" ]]; then
  echo "ERROR: Could not reach ${USER_NAME}@10.0.0.112 or ${USER_NAME}@192.168.1.112"
  exit 1
fi

echo "Using target: $TARGET"
echo

ssh "$TARGET" 'zsh -lic '"'"'
echo "== Host =="
hostname
sw_vers 2>/dev/null || true
echo

echo "== CLI PATHS =="
for c in claude codex gemini; do
  printf "[%s] " "$c"
  command -v "$c" || echo NOT_FOUND
done
echo

echo "== Versions =="
claude --version || true
codex --version || codex version || true
gemini --version || gemini version || true
echo

echo "== npm globals (filtered) =="
npm -g ls --depth=0 2>/dev/null | egrep "(@anthropic-ai/claude-code|@openai/codex|@google/gemini-cli)" || true
echo

echo "== Claude Agent Teams Flag =="
python3 - <<\"PY\"
import json, os
p=os.path.expanduser(\"~/.claude/settings.json\")
try:
    with open(p) as f:
        data=json.load(f)
except Exception as e:
    print(f\"ERROR reading {p}: {e}\")
else:
    print(data.get(\"env\", {}).get(\"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS\"))
PY
'"'"''
