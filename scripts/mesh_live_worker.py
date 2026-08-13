#!/usr/bin/env python3
"""Create or reuse one constrained local Codex tmux worker."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import time
from typing import Sequence


_FIELD_SEPARATOR = "\x1f"
_SAFE_SESSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
_DEFAULT_REPO_ROOTS = ("/data/sata/1TB", "/media/sam/1TB", "/media/sam/1TB1")
_CODEX_CANDIDATES = ("/usr/local/bin/codex", "/usr/bin/codex")


class WorkerEnsureError(RuntimeError):
    pass


def _run_command(args: list[str], *, timeout: float = 10.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _configured_repo_roots() -> tuple[Path, ...]:
    raw_roots: list[str] = []
    configured_base = os.environ.get("MESH_WS_REPO_BASE", "").strip()
    if configured_base:
        raw_roots.append(configured_base)
    configured_roots = os.environ.get("MESH_LIVE_REPO_ROOTS", "").strip()
    if configured_roots:
        raw_roots.extend(configured_roots.split(os.pathsep))
    else:
        raw_roots.extend(_DEFAULT_REPO_ROOTS)

    roots: list[Path] = []
    for raw in raw_roots:
        if not raw.strip():
            continue
        root = Path(raw).expanduser().resolve()
        if root not in roots:
            roots.append(root)
    return tuple(roots)


def _is_below(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _git_root(path: Path) -> Path:
    proc = _run_command(["git", "-C", str(path), "rev-parse", "--show-toplevel"])
    if proc.returncode != 0:
        raise WorkerEnsureError(f"worker target is not a Git repository root: {path}")
    return Path(proc.stdout.strip()).resolve()


def resolve_repo(value: str) -> Path:
    requested = str(value or "").strip()
    if not requested:
        raise WorkerEnsureError("worker repository is required")

    roots = _configured_repo_roots()
    candidate = Path(requested).expanduser()
    if candidate.is_absolute() or requested in {".", ".."} or "/" in requested:
        candidates = [candidate.resolve()]
    else:
        candidates = [(root / requested).resolve() for root in roots]

    existing = [item for item in candidates if item.is_dir()]
    if len(existing) != 1:
        if len(existing) > 1:
            raise WorkerEnsureError(
                f"worker repository name is ambiguous across configured roots: {requested}"
            )
        raise WorkerEnsureError(f"worker repository does not exist: {requested}")

    repo = existing[0]
    if not any(_is_below(repo, root) for root in roots):
        raise WorkerEnsureError(f"worker repository is outside configured roots: {repo}")
    git_root = _git_root(repo)
    if git_root != repo:
        raise WorkerEnsureError(f"worker target must be the Git repository root: {git_root}")
    return repo


def session_name_for_repo(repo: Path) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", repo.name).strip("-.").lower()
    if not slug:
        raise WorkerEnsureError(f"repository name cannot form a tmux session name: {repo.name}")
    session = f"codex-{slug}"
    if len(session) > 80:
        raise WorkerEnsureError("repository name is too long for a deterministic tmux session")
    if not _SAFE_SESSION.fullmatch(session):
        raise WorkerEnsureError(f"invalid deterministic tmux session name: {session}")
    return session


def _codex_executable() -> str:
    for candidate in _CODEX_CANDIDATES:
        path = Path(candidate)
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    raise WorkerEnsureError("trusted Codex executable not found in /usr/local/bin or /usr/bin")


def _tmux_error(proc: subprocess.CompletedProcess[str], fallback: str) -> str:
    detail = (proc.stderr or proc.stdout or fallback).strip().splitlines()
    return detail[-1] if detail else fallback


def _inspect_session(session: str) -> dict[str, str] | None:
    exact = f"={session}"
    exists = _run_command(["tmux", "has-session", "-t", exact])
    if exists.returncode != 0:
        return None
    fields = _FIELD_SEPARATOR.join(
        [
            "#{session_name}",
            "#{pane_current_path}",
            "#{pane_current_command}",
            "#{pane_dead}",
            "#{window_panes}",
        ]
    )
    proc = _run_command(["tmux", "display-message", "-p", "-t", f"{exact}:0.0", fields])
    if proc.returncode != 0:
        raise WorkerEnsureError(_tmux_error(proc, "cannot inspect existing worker session"))
    parts = proc.stdout.rstrip("\n").split(_FIELD_SEPARATOR)
    if len(parts) != 5 or parts[0] != session:
        raise WorkerEnsureError("existing worker session metadata changed during inspection")
    return {
        "session": parts[0],
        "repo": parts[1],
        "command": Path(parts[2]).name.lower(),
        "dead": parts[3],
        "panes": parts[4],
    }


def _validate_reusable(existing: dict[str, str], repo: Path) -> None:
    try:
        existing_repo = Path(existing["repo"]).resolve()
    except (OSError, RuntimeError) as exc:
        raise WorkerEnsureError("cannot resolve existing worker repository") from exc
    if existing_repo != repo:
        raise WorkerEnsureError(
            f"session {existing['session']} already belongs to a different repository: {existing_repo}"
        )
    if existing["dead"] != "0" or existing["panes"] != "1":
        raise WorkerEnsureError(
            f"session {existing['session']} is not one live single-pane worker"
        )
    if existing["command"] not in {"codex", "codex-cli"}:
        raise WorkerEnsureError(
            f"session {existing['session']} exists but its active process is "
            f"{existing['command'] or '<empty>'}, not Codex"
        )


def ensure_codex_worker(repo_value: str, *, expected_session: str = "") -> dict[str, object]:
    repo = resolve_repo(repo_value)
    session = session_name_for_repo(repo)
    if expected_session:
        if not _SAFE_SESSION.fullmatch(expected_session) or expected_session != session:
            raise WorkerEnsureError(
                f"requested worker {expected_session} does not match deterministic session {session}"
            )

    existing = _inspect_session(session)
    if existing is not None:
        _validate_reusable(existing, repo)
        return {"session": session, "repo": str(repo), "created": False, "ready": True}

    codex = _codex_executable()
    codex_argv = [codex, "--dangerously-bypass-approvals-and-sandbox", "-C", str(repo)]
    startup = "exec " + shlex.join(codex_argv)
    created = _run_command(
        ["tmux", "new-session", "-d", "-s", session, "-c", str(repo), startup]
    )
    if created.returncode != 0:
        # tmux session creation is atomic. A concurrent winner may now be reusable.
        existing = _inspect_session(session)
        if existing is None:
            raise WorkerEnsureError(_tmux_error(created, "cannot create Codex worker session"))
        _validate_reusable(existing, repo)
        return {"session": session, "repo": str(repo), "created": False, "ready": True}

    for _ in range(20):
        existing = _inspect_session(session)
        if existing is None:
            raise WorkerEnsureError(
                f"Codex worker {session} exited during startup; inspect Codex authentication/configuration"
            )
        try:
            _validate_reusable(existing, repo)
        except WorkerEnsureError as exc:
            if existing["command"] in {"sh", "bash", "zsh"}:
                time.sleep(0.1)
                continue
            raise exc
        return {"session": session, "repo": str(repo), "created": True, "ready": True}
    raise WorkerEnsureError(f"Codex worker {session} did not become ready")


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create or reuse one local Codex tmux worker.")
    parser.add_argument("repo", help="Configured Git repository root or unambiguous repo name.")
    parser.add_argument("--expect-session", default="", help="Require this deterministic name.")
    parser.add_argument("--json", action="store_true", help="Emit structured JSON.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        result = ensure_codex_worker(args.repo, expected_session=args.expect_session)
    except (OSError, subprocess.SubprocessError, WorkerEnsureError) as exc:
        print(f"Error: {exc}", file=os.sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        action = "created" if result["created"] else "reused"
        print(
            f"[mesh live ensure-codex] session={result['session']} repo={result['repo']} "
            f"action={action} ready=yes"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
