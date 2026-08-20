#!/usr/bin/env python3
"""Dispatch Codex PreToolUse payloads to Probity only for opted-in Git roots."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Sequence


CONFIG_NAMES = (
    "probity.config.ts",
    "probity.config.mts",
    "probity.config.js",
    "probity.config.mjs",
)
MAX_PAYLOAD_BYTES = 10 * 1024 * 1024
MAX_RESPONSE_BYTES = 1024 * 1024


def _allow() -> str:
    return "{}\n"


def _deny(reason: str) -> str:
    bounded = " ".join(str(reason).split())[:1000]
    return json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": f"Probity dispatcher: {bounded}",
            }
        },
        separators=(",", ":"),
    ) + "\n"


def _read_payload(stream: Any) -> bytes:
    raw = stream.read(MAX_PAYLOAD_BYTES + 1)
    if len(raw) > MAX_PAYLOAD_BYTES:
        raise ValueError("hook payload exceeds 10 MiB")
    return raw


def _decode_payload(raw: bytes) -> dict[str, Any] | None:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _git_root(cwd: str) -> Path | None:
    candidate = Path(cwd).expanduser()
    if not candidate.is_absolute() or not candidate.is_dir():
        return None
    try:
        proc = subprocess.run(
            ["git", "-C", str(candidate), "rev-parse", "--show-toplevel"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    root = Path(proc.stdout.strip()).resolve()
    try:
        candidate.resolve().relative_to(root)
    except ValueError:
        return None
    return root


def _probity_executable() -> str | None:
    override = os.environ.get("MESH_PROBITY_BIN", "").strip()
    candidates = [override, shutil.which("probity") or "", "~/.npm-global/bin/probity"]
    for raw in candidates:
        if not raw:
            continue
        candidate = Path(raw).expanduser()
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate.resolve())
    return None


def _runtime_matches_expected(executable: str, expected: str) -> bool:
    if not expected:
        return True
    package_json = Path(executable).resolve().parent.parent / "package.json"
    try:
        if package_json.stat().st_size > 1024 * 1024:
            return False
        payload = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return (
        isinstance(payload, dict)
        and payload.get("name") == "@nizos/probity"
        and payload.get("version") == expected
    )


def dispatch(raw: bytes) -> str:
    payload = _decode_payload(raw)
    if payload is None or payload.get("hook_event_name") != "PreToolUse":
        return _allow()
    cwd = payload.get("cwd")
    if not isinstance(cwd, str):
        return _allow()
    root = _git_root(cwd)
    if root is None:
        return _allow()
    candidates = [root / name for name in CONFIG_NAMES if (root / name).exists() or (root / name).is_symlink()]
    if any(path.is_symlink() or not path.is_file() for path in candidates):
        return _deny("probity.config must be one regular file at the Git root")
    configs = candidates
    if not configs:
        return _allow()
    if len(configs) != 1:
        return _deny("multiple probity.config files at the Git root")
    executable = _probity_executable()
    if executable is None:
        return _deny("repository opted in but the pinned Probity runtime is unavailable")
    expected = os.environ.get("MESH_PROBITY_EXPECTED_VERSION", "").strip()
    if not _runtime_matches_expected(executable, expected):
        return _deny("installed Probity package does not match the pinned version")
    try:
        proc = subprocess.run(
            [executable, "--agent", "codex", "--config", str(configs[0])],
            cwd=root,
            input=raw,
            check=False,
            capture_output=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return _deny("Probity timed out after 120 seconds")
    except OSError as exc:
        return _deny(f"cannot execute Probity: {exc}")
    if len(proc.stdout) > MAX_RESPONSE_BYTES:
        return _deny("Probity response exceeds 1 MiB")
    if proc.returncode == 0 and not proc.stdout.strip():
        return _allow()
    try:
        response = json.loads(proc.stdout.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return _deny("Probity returned invalid JSON")
    if proc.returncode != 0 or not isinstance(response, dict):
        return _deny(f"Probity failed with exit code {proc.returncode}")
    return json.dumps(response, separators=(",", ":")) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    if argv:
        print("Usage: mesh_probity_hook.py < hook-payload.json", file=sys.stderr)
        return 2
    try:
        raw = _read_payload(sys.stdin.buffer)
        sys.stdout.write(dispatch(raw))
    except ValueError as exc:
        sys.stdout.write(_deny(str(exc)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
