#!/usr/bin/env python3
"""Create or reuse one constrained local tmux worker."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import time
from typing import Callable, Sequence


_FIELD_SEPARATOR = "\x1f"
_SAFE_SESSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
_DEFAULT_REPO_ROOTS = ("/data/sata/1TB", "/media/sam/1TB", "/media/sam/1TB1")
_CODEX_CANDIDATES = ("/usr/local/bin/codex", "/usr/bin/codex")
_ANTIGRAVITY_CANDIDATES = ("/home/sam/.local/bin/agy", "/usr/local/bin/agy")
_ANTIGRAVITY_BOOTSTRAP_PROMPT = (
    "Initialize this repository session and wait idle for a coordinator delegation. "
    "Do not inspect or modify files and do not run commands."
)
_CODEX_STARTUP_ATTEMPTS = 20
_ANTIGRAVITY_STARTUP_ATTEMPTS = 300


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


def _control_plane_root() -> Path | None:
    script_path = Path(__file__).resolve()
    proc = _run_command(
        ["git", "-C", str(script_path.parent), "rev-parse", "--show-toplevel"]
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
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
    control_plane_root = _control_plane_root()
    if control_plane_root is not None and git_root == control_plane_root:
        raise WorkerEnsureError(
            "worker target is the active mesh live control-plane checkout; use a separate "
            "clean development checkout or Git worktree"
        )
    return repo


def session_name_for_repo(repo: Path, provider: str = "codex") -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", repo.name).strip("-.").lower()
    if not slug:
        raise WorkerEnsureError(f"repository name cannot form a tmux session name: {repo.name}")
    session = f"{provider}-{slug}"
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


def _antigravity_executable() -> str:
    for candidate in _ANTIGRAVITY_CANDIDATES:
        path = Path(candidate)
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    raise WorkerEnsureError(
        "trusted Antigravity executable not found in /home/sam/.local/bin or /usr/local/bin"
    )


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


def _validate_reusable(
    existing: dict[str, str],
    repo: Path,
    *,
    provider: str = "codex",
) -> None:
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
    expected_commands = {
        "codex": {"codex", "codex-cli"},
        "antigravity": {"agy"},
    }[provider]
    if existing["command"] not in expected_commands:
        raise WorkerEnsureError(
            f"session {existing['session']} exists but its active process is "
            f"{existing['command'] or '<empty>'}, not {provider.title()}"
        )


def _startup_transition_pending(
    existing: dict[str, str],
    repo: Path,
    *,
    provider: str = "codex",
) -> bool:
    if existing["dead"] != "0" or existing["panes"] != "1":
        return False
    provider_commands = {
        "codex": {"codex", "codex-cli"},
        "antigravity": {"agy"},
    }[provider]
    if existing["command"] not in {"sh", "bash", "zsh", "tmux", *provider_commands}:
        return False
    try:
        existing_repo = Path(existing["repo"]).resolve()
    except (OSError, RuntimeError):
        return False
    return existing["command"] in {"sh", "bash", "zsh", "tmux"} or existing_repo != repo


def _wait_for_reusable_session(
    session: str,
    repo: Path,
    *,
    provider: str,
    attempts: int,
) -> dict[str, str]:
    last_error: WorkerEnsureError | None = None
    for _ in range(attempts):
        existing = _inspect_session(session)
        if existing is None:
            raise WorkerEnsureError(
                f"{provider.title()} worker {session} exited during startup; "
                f"inspect {provider.title()} authentication/configuration"
            )
        try:
            _validate_reusable(existing, repo, provider=provider)
        except WorkerEnsureError as exc:
            last_error = exc
            if _startup_transition_pending(existing, repo, provider=provider):
                time.sleep(0.1)
                continue
            raise
        return existing
    if last_error is not None:
        raise last_error
    raise WorkerEnsureError(f"{provider.title()} worker {session} did not become ready")


def _ensure_worker(
    repo_value: str,
    *,
    provider: str,
    executable_resolver: Callable[[], str],
    expected_session: str = "",
) -> dict[str, object]:
    repo = resolve_repo(repo_value)
    session = session_name_for_repo(repo, provider)
    if expected_session:
        if not _SAFE_SESSION.fullmatch(expected_session) or expected_session != session:
            raise WorkerEnsureError(
                f"requested worker {expected_session} does not match deterministic session {session}"
            )

    existing = _inspect_session(session)
    if existing is not None:
        if provider == "antigravity" and _startup_transition_pending(
            existing, repo, provider=provider
        ):
            _wait_for_reusable_session(
                session,
                repo,
                provider=provider,
                attempts=_ANTIGRAVITY_STARTUP_ATTEMPTS,
            )
        else:
            _validate_reusable(existing, repo, provider=provider)
        return {"session": session, "repo": str(repo), "created": False, "ready": True}

    executable = executable_resolver()
    if provider == "codex":
        launch_argv = [
            executable,
            "--dangerously-bypass-approvals-and-sandbox",
            "-C",
            str(repo),
        ]
    elif provider == "antigravity":
        launch_argv = [
            executable,
            "--dangerously-skip-permissions",
            "--new-project",
            "--prompt-interactive",
            _ANTIGRAVITY_BOOTSTRAP_PROMPT,
        ]
    else:
        raise WorkerEnsureError(f"unsupported worker provider: {provider}")
    startup = "exec " + shlex.join(launch_argv)
    created = _run_command(
        ["tmux", "new-session", "-d", "-s", session, "-c", str(repo), startup]
    )
    if created.returncode != 0:
        # tmux session creation is atomic. A concurrent winner may now be reusable.
        existing = _inspect_session(session)
        if existing is None:
            raise WorkerEnsureError(
                _tmux_error(created, f"cannot create {provider.title()} worker session")
            )
        if provider == "antigravity" and _startup_transition_pending(
            existing, repo, provider=provider
        ):
            _wait_for_reusable_session(
                session,
                repo,
                provider=provider,
                attempts=_ANTIGRAVITY_STARTUP_ATTEMPTS,
            )
        else:
            _validate_reusable(existing, repo, provider=provider)
        return {"session": session, "repo": str(repo), "created": False, "ready": True}

    attempts = (
        _ANTIGRAVITY_STARTUP_ATTEMPTS
        if provider == "antigravity"
        else _CODEX_STARTUP_ATTEMPTS
    )
    _wait_for_reusable_session(session, repo, provider=provider, attempts=attempts)
    return {"session": session, "repo": str(repo), "created": True, "ready": True}


def ensure_codex_worker(repo_value: str, *, expected_session: str = "") -> dict[str, object]:
    return _ensure_worker(
        repo_value,
        provider="codex",
        executable_resolver=_codex_executable,
        expected_session=expected_session,
    )


def ensure_antigravity_worker(
    repo_value: str,
    *,
    expected_session: str = "",
) -> dict[str, object]:
    return _ensure_worker(
        repo_value,
        provider="antigravity",
        executable_resolver=_antigravity_executable,
        expected_session=expected_session,
    )


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create or reuse one local tmux worker.")
    parser.add_argument("repo", help="Configured Git repository root or unambiguous repo name.")
    parser.add_argument(
        "--provider",
        choices=("codex", "antigravity"),
        default="codex",
        help="Worker CLI provider (default: codex).",
    )
    parser.add_argument("--expect-session", default="", help="Require this deterministic name.")
    parser.add_argument("--json", action="store_true", help="Emit structured JSON.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        ensure = (
            ensure_antigravity_worker if args.provider == "antigravity" else ensure_codex_worker
        )
        result = ensure(args.repo, expected_session=args.expect_session)
    except (OSError, subprocess.SubprocessError, WorkerEnsureError) as exc:
        print(f"Error: {exc}", file=os.sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        action = "created" if result["created"] else "reused"
        print(
            f"[mesh live ensure-{args.provider}] session={result['session']} repo={result['repo']} "
            f"action={action} ready=yes"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
