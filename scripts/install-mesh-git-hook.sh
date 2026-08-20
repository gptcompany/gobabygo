#!/usr/bin/env bash
set -euo pipefail
umask 077

SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/mesh_global_pre_push.sh"
TARGET="${MESH_GLOBAL_PRE_PUSH_TARGET:-$HOME/.claude/hooks/pre-push}"
APPLY=0
REPLACE=0

usage() {
  cat <<'EOF'
Usage: install-mesh-git-hook.sh [--apply] [--replace] [--target PATH]

Without --apply, prints the installation plan. --replace is required for an
existing unknown hook. The known legacy pre-push-review.py shim is migrated
without --replace.
EOF
}

fail() {
  printf 'Error: %s\n' "$*" >&2
  exit 2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply) APPLY=1; shift ;;
    --replace) REPLACE=1; shift ;;
    --target)
      [[ $# -ge 2 ]] || fail "--target requires a path"
      TARGET="$2"
      shift 2
      ;;
    -h|--help) usage; exit 0 ;;
    *) fail "unknown argument: $1" ;;
  esac
done

[[ "$TARGET" == /* ]] || fail "target must be an absolute path"
[[ -f "$SOURCE" ]] || fail "dispatcher source not found: $SOURCE"

known_legacy=0
if [[ -f "$TARGET" ]]; then
  executable_lines="$(sed -e '/^[[:space:]]*#/d' -e '/^[[:space:]]*$/d' "$TARGET")"
  if [[ "$executable_lines" == 'python3 ~/.claude/hooks/pre-push-review.py' ]]; then
    known_legacy=1
  fi
fi
if [[ -e "$TARGET" && ! -f "$TARGET" ]]; then
  fail "target exists and is not a regular file: $TARGET"
fi
if [[ -f "$TARGET" ]] && cmp -s "$SOURCE" "$TARGET"; then
  echo "Mesh global pre-push dispatcher is already installed."
  exit 0
fi
if [[ -f "$TARGET" && "$known_legacy" -ne 1 && "$REPLACE" -ne 1 ]]; then
  fail "refusing to replace unknown hook without --replace: $TARGET"
fi

echo "Source: $SOURCE"
echo "Target: $TARGET"
echo "Global core.hooksPath: $(dirname "$TARGET")"
if [[ "$APPLY" -ne 1 ]]; then
  echo "Plan only; rerun with --apply."
  exit 0
fi

mkdir -p "$(dirname "$TARGET")"
chmod 700 "$(dirname "$TARGET")"
temp_target="$(mktemp "$(dirname "$TARGET")/.pre-push.XXXXXX")"
trap 'rm -f "$temp_target"' EXIT
install -m 755 "$SOURCE" "$temp_target"
mv -f "$temp_target" "$TARGET"
trap - EXIT
git config --global core.hooksPath "$(dirname "$TARGET")"
echo "Installed Mesh global pre-push dispatcher."
