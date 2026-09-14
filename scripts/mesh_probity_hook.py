#!/usr/bin/env python3
"""Dispatch PreToolUse payloads to Probity only for opted-in Git roots."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Any, Sequence


CONFIG_NAMES = (
    "probity.config.ts",
    "probity.config.mts",
    "probity.config.js",
    "probity.config.mjs",
)
SUPPORTED_AGENTS = ("codex", "claude-code")
MAX_PAYLOAD_BYTES = 10 * 1024 * 1024
MAX_RESPONSE_BYTES = 1024 * 1024
# Leave headroom below Probity's 100 MiB session limit.
MAX_TRANSCRIPT_BYTES = 80 * 1024 * 1024
MAX_TRANSCRIPT_TAIL_BYTES = 8 * 1024 * 1024


def _open_transcript(path: str) -> int:
    """Open without following symlinks in any component or blocking on FIFOs."""
    parts = Path(path).parts
    if not parts or parts[0] != "/" or ".." in parts:
        raise ValueError("transcript_path must be an absolute regular non-symlink file")
    directory = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        return os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    finally:
        os.close(directory)


@contextmanager
def _forward_payload(raw: bytes, payload: dict[str, Any]):
    path = payload.get("transcript_path")
    if path is None:
        yield raw
        return
    if not isinstance(path, str) or not path:
        raise ValueError("transcript_path must be an absolute regular non-symlink file")
    with os.fdopen(_open_transcript(path), "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("transcript_path must be a regular non-symlink file")
        if info.st_size <= MAX_TRANSCRIPT_BYTES:
            yield raw
            return
        # Read one preceding byte to distinguish a line boundary from a partial line.
        start = max(0, info.st_size - MAX_TRANSCRIPT_TAIL_BYTES)
        source.seek(max(0, start - 1))
        tail = source.read(min(info.st_size, MAX_TRANSCRIPT_TAIL_BYTES + 1))
        if start:
            tail = tail[tail.find(b"\n") + 1:] if b"\n" in tail else b""
        tail = tail[:tail.rfind(b"\n") + 1]
        if not tail:
            raise ValueError("transcript tail has no complete JSONL records")
        for line in tail.splitlines():
            json.loads(line.decode("utf-8"))
    # Both the directory (0700) and file (0600) are private; cleanup also runs
    # on write errors, vendor failures, and timeouts.
    with tempfile.TemporaryDirectory(prefix="mesh-probity-") as directory:
        with tempfile.NamedTemporaryFile(dir=directory, suffix=".jsonl", delete=False) as target:
            target.write(tail)
        forwarded = {**payload, "transcript_path": target.name}
        yield json.dumps(forwarded, separators=(",", ":")).encode("utf-8")


def _allow() -> str:
    return "{}\n"


def _deny(agent: str, reason: str) -> str:
    bounded = " ".join(str(reason).split())[:1000]
    if agent == "codex":
        payload = {
            "decision": "block",
            "reason": f"Probity dispatcher: {bounded}",
        }
    else:
        payload = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": f"Probity dispatcher: {bounded}",
            }
        }
    return json.dumps(payload, separators=(",", ":")) + "\n"


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


def _is_vendor_response(agent: str, response: object) -> bool:
    if not isinstance(response, dict):
        return False
    if agent == "codex":
        return response.get("decision") == "block" and isinstance(response.get("reason"), str)
    output = response.get("hookSpecificOutput")
    return (
        isinstance(output, dict)
        and output.get("hookEventName") == "PreToolUse"
        and output.get("permissionDecision") == "deny"
        and isinstance(output.get("permissionDecisionReason"), str)
    )


def dispatch(raw: bytes, *, agent: str) -> str:
    if agent not in SUPPORTED_AGENTS:
        raise ValueError(f"unsupported Probity agent: {agent}")
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
        return _deny(agent, "probity.config must be one regular file at the Git root")
    configs = candidates
    if not configs:
        return _allow()
    if len(configs) != 1:
        return _deny(agent, "multiple probity.config files at the Git root")
    executable = _probity_executable()
    if executable is None:
        return _deny(agent, "repository opted in but the pinned Probity runtime is unavailable")
    expected = os.environ.get("MESH_PROBITY_EXPECTED_VERSION", "").strip()
    if not _runtime_matches_expected(executable, expected):
        return _deny(agent, "installed Probity package does not match the pinned version")
    try:
        with _forward_payload(raw, payload) as forwarded:
            proc = subprocess.run(
                [executable, "--agent", agent, "--config", str(configs[0])],
                cwd=root,
                input=forwarded,
                check=False,
                capture_output=True,
                timeout=120,
            )
    except subprocess.TimeoutExpired:
        return _deny(agent, "Probity timed out after 120 seconds")
    except (OSError, ValueError) as exc:
        return _deny(agent, f"cannot prepare transcript or execute Probity: {exc}")
    if len(proc.stdout) > MAX_RESPONSE_BYTES:
        return _deny(agent, "Probity response exceeds 1 MiB")
    if proc.returncode == 0 and not proc.stdout.strip():
        return _allow()
    try:
        response = json.loads(proc.stdout.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return _deny(agent, "Probity returned invalid JSON")
    if proc.returncode != 0:
        return _deny(agent, f"Probity failed with exit code {proc.returncode}")
    if not _is_vendor_response(agent, response):
        return _deny(agent, "Probity returned an invalid vendor response")
    return json.dumps(response, separators=(",", ":")) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    args = list(argv or [])
    if len(args) != 2 or args[0] != "--agent" or args[1] not in SUPPORTED_AGENTS:
        print("Usage: mesh_probity_hook.py --agent codex|claude-code < hook-payload.json", file=sys.stderr)
        return 2
    agent = args[1]
    try:
        raw = _read_payload(sys.stdin.buffer)
        sys.stdout.write(dispatch(raw, agent=agent))
    except ValueError as exc:
        sys.stdout.write(_deny(agent, str(exc)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
