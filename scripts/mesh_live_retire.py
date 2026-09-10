#!/usr/bin/env python3
"""Explicit, fail-closed retirement for an already reconciled local tmux worker.

This helper intentionally owns the only mesh-live session termination primitive.
It is local-only, never selected by a supervisor tick, and records the operator's
handoff before it can terminate a session.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat as stat_module
import subprocess
import sys
import tempfile
import time
from typing import Sequence

from mesh_live_cli import LiveReadError, LiveSession, redact_capture, session_screen_state


DEFAULT_RETIRE_STATE_FILE = "~/.local/state/gobabygo/mesh-live-retire.json"
_FIELD_SEPARATOR = "\x1f"
_SAFE_SESSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
_SAFE_REASON = re.compile(r"^[^\x00-\x1f\x7f]{8,240}$")
_PROVIDER_COMMANDS = frozenset({"codex", "codex-cli", "agy", "antigravity"})
_MAX_HANDOFF_BYTES = 1024 * 1024


class RetirementRefused(LiveReadError):
    """A required lifecycle proof is absent or changed."""


@dataclass(frozen=True)
class RetirementPreflight:
    session: str
    pane_id: str
    pane_pid: int
    pane_path: str
    command: str
    attached: int
    panes: int
    git_clean: bool
    screen_state: str
    capture_fingerprint: str
    handoff_path: str
    handoff_sha256: str
    reason: str


def _run(args: list[str], *, timeout: float = 10.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=False, capture_output=True, text=True, timeout=timeout)


def _split_fields(value: str) -> list[str]:
    row = str(value or "").rstrip("\n")
    return row.split(_FIELD_SEPARATOR) if _FIELD_SEPARATOR in row else row.split(r"\037")


def _validate_session(value: str) -> str:
    session = str(value or "").strip()
    if not _SAFE_SESSION.fullmatch(session):
        raise RetirementRefused("retire requires one exact safe tmux session name")
    return session


def _validate_reason(value: str) -> str:
    reason = str(value or "").strip()
    if not _SAFE_REASON.fullmatch(reason):
        raise RetirementRefused("retire reason must be a single 8-240 character line")
    return reason


def _read_handoff(value: str) -> tuple[str, str]:
    path = Path(str(value or "")).expanduser()
    if not path.is_absolute():
        raise RetirementRefused("retire handoff must be an absolute existing file")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise RetirementRefused(f"unable to inspect retirement handoff: {exc}") from exc
    try:
        stat = os.fstat(fd)
        if not stat_module.S_ISREG(stat.st_mode) or stat.st_size <= 0 or stat.st_size > _MAX_HANDOFF_BYTES:
            raise RetirementRefused("retire handoff must be a non-empty regular file at most 1 MiB")
        data = os.read(fd, _MAX_HANDOFF_BYTES + 1)
    except OSError as exc:
        raise RetirementRefused(f"unable to read retirement handoff: {exc}") from exc
    finally:
        os.close(fd)
    if len(data) != stat.st_size:
        raise RetirementRefused("retire handoff changed while it was read")
    return str(path.resolve()), hashlib.sha256(data).hexdigest()


def _inspect_tmux(session: str) -> tuple[dict[str, str], str]:
    target = f"={session}:0.0"
    fields = _FIELD_SEPARATOR.join(
        (
            "#{session_name}", "#{pane_id}", "#{pane_pid}", "#{pane_current_path}",
            "#{pane_current_command}", "#{session_attached}", "#{window_panes}", "#{pane_dead}",
        )
    )
    proc = _run(["tmux", "display-message", "-p", "-t", target, fields])
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "tmux session is unavailable").strip()
        raise RetirementRefused(redact_capture(detail))
    parts = _split_fields(proc.stdout)
    if len(parts) != 8 or parts[0] != session:
        raise RetirementRefused("retire target identity changed during inspection")
    names = ("session", "pane_id", "pane_pid", "pane_path", "command", "attached", "panes", "dead")
    capture = _run(["tmux", "capture-pane", "-p", "-e", "-S", "-200", "-t", target])
    if capture.returncode != 0:
        detail = (capture.stderr or capture.stdout or "cannot capture target").strip()
        raise RetirementRefused(redact_capture(detail))
    return dict(zip(names, parts, strict=True)), redact_capture(capture.stdout)


def _require_no_children(pane_pid: str) -> None:
    if not str(pane_pid).isdigit():
        raise RetirementRefused("retire target has no valid pane process identity")
    proc = _run(["ps", "-axo", "pid=,ppid=,comm="])
    if proc.returncode != 0:
        raise RetirementRefused("cannot inspect worker child processes")
    children = []
    for line in proc.stdout.splitlines():
        fields = line.strip().split(None, 2)
        if len(fields) == 3 and fields[1] == pane_pid:
            children.append(fields[2])
    if children:
        raise RetirementRefused("retire target still has direct child processes")


def _require_clean_git(path: str) -> bool:
    if not os.path.isabs(path):
        raise RetirementRefused("retire target has no absolute repository path")
    root = _run(["git", "-C", path, "rev-parse", "--show-toplevel"])
    if root.returncode != 0 or os.path.normpath(root.stdout.strip()) != os.path.normpath(path):
        raise RetirementRefused("retire target is not at an exact Git repository root")
    status = _run(["git", "-C", path, "status", "--porcelain=v1", "--untracked-files=all"])
    if status.returncode != 0:
        raise RetirementRefused("cannot inspect target Git status")
    if status.stdout.strip():
        raise RetirementRefused("retire target Git worktree is not clean")
    return True


def inspect_retirement(session: str, handoff: str, reason: str) -> RetirementPreflight:
    name = _validate_session(session)
    safe_reason = _validate_reason(reason)
    handoff_path, handoff_sha256 = _read_handoff(handoff)
    fields, screen = _inspect_tmux(name)
    command = Path(fields["command"]).name.lower()
    if fields["dead"] != "0" or fields["attached"] != "0" or fields["panes"] != "1":
        raise RetirementRefused("retire target must be detached, live, and single-pane")
    if command not in _PROVIDER_COMMANDS:
        raise RetirementRefused("retire target must run a supported worker provider directly")
    _require_no_children(fields["pane_pid"])
    git_clean = _require_clean_git(fields["pane_path"])
    state = session_screen_state(
        LiveSession(
            owner=os.environ.get("USER", ""), name=name, pane_id=fields["pane_id"],
            pane_pid=int(fields["pane_pid"]), pane_path=fields["pane_path"], pane_command=command,
            output=screen,
        )
    )
    if state != "idle":
        raise RetirementRefused(f"retire target visible provider state is {state}, not idle")
    return RetirementPreflight(
        session=name, pane_id=fields["pane_id"], pane_pid=int(fields["pane_pid"]),
        pane_path=fields["pane_path"], command=command, attached=0, panes=1, git_clean=git_clean,
        screen_state=state,
        capture_fingerprint=hashlib.sha256(screen.encode("utf-8", errors="replace")).hexdigest(),
        handoff_path=handoff_path, handoff_sha256=handoff_sha256, reason=safe_reason,
    )


def _load_state(path: str) -> dict[str, object]:
    state_path = Path(path).expanduser()
    if not state_path.exists():
        return {"version": 1, "retirements": {}}
    if state_path.is_symlink():
        raise RetirementRefused("retire state file must not be a symlink")
    try:
        result = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RetirementRefused(f"unable to read retire state: {exc}") from exc
    if not isinstance(result, dict) or result.get("version") != 1 or not isinstance(result.get("retirements"), dict):
        raise RetirementRefused("unsupported retire state format")
    return result


def _save_state(path: str, state: dict[str, object]) -> None:
    state_path = Path(path).expanduser()
    if state_path.is_symlink():
        raise RetirementRefused("retire state file must not be a symlink")
    state_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{state_path.name}.", suffix=".tmp", dir=state_path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, state_path)
        state_path.chmod(0o600)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise RetirementRefused(f"unable to persist retirement record: {exc}") from exc


@contextmanager
def _retirement_lock(path: str):
    state_path = Path(path).expanduser()
    state_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    lock_path = state_path.with_name(f".{state_path.name}.lock")
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise RetirementRefused(f"unable to open retire lock: {exc}") from exc
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RetirementRefused("another mesh live retirement is already running") from exc
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def retire_worker(
    session: str, handoff: str, reason: str, *, apply: bool, confirm: str, state_file: str
) -> dict[str, object]:
    if not apply:
        first = inspect_retirement(session, handoff, reason)
        return {"status": "planned", "preflight": asdict(first)}
    with _retirement_lock(state_file):
        first = inspect_retirement(session, handoff, reason)
        if confirm != first.session:
            raise RetirementRefused("--apply requires --confirm with the exact session name")
        state = _load_state(state_file)
        retirements = state["retirements"]
        assert isinstance(retirements, dict)
        prior = retirements.get(first.session)
        if isinstance(prior, dict) and prior.get("status") in {"applied", "attempted"}:
            raise RetirementRefused("retirement was already attempted for this exact session")
        record = {"status": "attempted", "recorded_at": time.time(), "preflight": asdict(first)}
        retirements[first.session] = record
        _save_state(state_file, state)
        second = inspect_retirement(session, handoff, reason)
        if second != first:
            record["status"] = "refused_changed"
            record["refused_at"] = time.time()
            _save_state(state_file, state)
            raise RetirementRefused("retire target changed after durable record; no termination performed")
        terminated = _run(["tmux", "kill-session", "-t", f"={first.session}"])
        if terminated.returncode != 0:
            record["status"] = "failed"
            record["failed_at"] = time.time()
            _save_state(state_file, state)
            detail = (terminated.stderr or terminated.stdout or "tmux termination failed").strip()
            raise RetirementRefused(redact_capture(detail))
        record["status"] = "applied"
        record["applied_at"] = time.time()
        _save_state(state_file, state)
        return {"status": "applied", "preflight": asdict(first)}


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Retire one reconciled local tmux worker with fail-closed checks.")
    parser.add_argument("session", help="Exact tmux session name.")
    parser.add_argument("--handoff", required=True, help="Absolute preserved cancellation/supersession handoff file.")
    parser.add_argument("--reason", required=True, help="Single-line cancellation or supersession reason.")
    parser.add_argument("--apply", action="store_true", help="Terminate only after all preflights pass twice.")
    parser.add_argument("--confirm", default="", help="Required exact session name with --apply.")
    parser.add_argument("--state-file", default=os.environ.get("MESH_LIVE_RETIRE_STATE", DEFAULT_RETIRE_STATE_FILE))
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        result = retire_worker(args.session, args.handoff, args.reason, apply=args.apply, confirm=args.confirm, state_file=args.state_file)
    except (OSError, subprocess.SubprocessError, RetirementRefused) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"[mesh live retire] session={args.session} status={result['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
