#!/usr/bin/env bash
set -u
umask 077

review_hook="${HOME}/.claude/hooks/pre-push-review.py"
if [[ ! -f "$review_hook" ]]; then
  printf '[mesh-hook] missing global review: %s\n' "$review_hook" >&2
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
  printf '[mesh-hook] python3 is required for %s\n' "$review_hook" >&2
  exit 1
fi

input_file="$(mktemp "${TMPDIR:-/tmp}/mesh-pre-push.XXXXXX")" || exit 1
trap 'rm -f "$input_file"' EXIT HUP INT TERM
cat >"$input_file" || exit 1

python3 "$review_hook" "$@" <"$input_file" || exit $?

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[[ -n "$repo_root" ]] || exit 0
repo_hook="${repo_root}/.githooks/pre-push"
if [[ ! -e "$repo_hook" ]]; then
  exit 0
fi
if [[ ! -f "$repo_hook" || ! -x "$repo_hook" ]]; then
  printf '[mesh-hook] repository pre-push exists but is not executable: %s\n' "$repo_hook" >&2
  exit 1
fi
"$repo_hook" "$@" <"$input_file"
