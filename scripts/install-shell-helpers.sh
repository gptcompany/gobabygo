#!/usr/bin/env bash
set -euo pipefail

TARGET_ZSHRC="${TARGET_ZSHRC:-$HOME/.zshrc}"
BEGIN_MARKER="# >>> gobabygo-shell-helpers >>>"
END_MARKER="# <<< gobabygo-shell-helpers <<<"

mkdir -p "$(dirname "$TARGET_ZSHRC")"
touch "$TARGET_ZSHRC"

if grep -Fq "$BEGIN_MARKER" "$TARGET_ZSHRC"; then
  echo "Shell helpers already installed in $TARGET_ZSHRC"
  exit 0
fi

cat >>"$TARGET_ZSHRC" <<'EOF'

# >>> gobabygo-shell-helpers >>>
lfcd() {
  command -v lf >/dev/null 2>&1 || { echo "lf not found"; return 127; }
  local tmp rc dir
  tmp="$(mktemp "${TMPDIR:-/tmp}/lfcd.XXXXXX")"
  rc=0
  command lf -last-dir-path="$tmp" "$@" || rc=$?
  if [[ -f "$tmp" ]]; then
    dir="$(cat "$tmp")"
    [[ -d "$dir" ]] && builtin cd -- "$dir"
    rm -f "$tmp"
  fi
  return "$rc"
}

yazicd() {
  command -v yazi >/dev/null 2>&1 || { echo "yazi not found"; return 127; }
  local tmp rc dir
  tmp="$(mktemp "${TMPDIR:-/tmp}/yazicd.XXXXXX")"
  rc=0
  command yazi --cwd-file="$tmp" "$@" || rc=$?
  if [[ -s "$tmp" ]]; then
    dir="$(cat "$tmp")"
    [[ -d "$dir" ]] && builtin cd -- "$dir"
  fi
  rm -f "$tmp"
  return "$rc"
}
# <<< gobabygo-shell-helpers <<<
EOF

echo "Installed lfcd + yazicd in $TARGET_ZSHRC"
echo "Run: source \"$TARGET_ZSHRC\""

