#!/usr/bin/env python3
"""Inspect the pinned, repository-opt-in Probity runtime."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCK_FILE = ROOT / "config" / "probity.lock.json"
CONFIG_NAMES = (
    "probity.config.ts",
    "probity.config.mts",
    "probity.config.js",
    "probity.config.mjs",
)
_VERSION = re.compile(r"(?<![0-9])([1-9][0-9]*)\.(0|[0-9]+)\.(0|[0-9]+)(?![0-9])")


class ProbityRuntimeError(RuntimeError):
    pass


def _load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ProbityRuntimeError(f"{label} not found: {path}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProbityRuntimeError(f"cannot read {label} {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProbityRuntimeError(f"{label} must contain a JSON object: {path}")
    return payload


def load_lock(path: Path = DEFAULT_LOCK_FILE) -> dict[str, Any]:
    payload = _load_json_object(path.resolve(), label="Probity lock")
    version = str(payload.get("version", ""))
    if (
        payload.get("schema") != 1
        or _VERSION.fullmatch(version) is None
        or payload.get("package") != "@nizos/probity"
        or not str(payload.get("integrity", "")).startswith("sha512-")
        or payload.get("source") != "https://github.com/nizos/probity"
        or payload.get("supported_agents") != ["codex"]
    ):
        raise ProbityRuntimeError("invalid Probity lock")
    return {
        "schema": 1,
        "package": "@nizos/probity",
        "version": version,
        "integrity": str(payload["integrity"]),
        "source": str(payload["source"]),
        "supported_agents": ["codex"],
    }


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


def installed_runtime() -> dict[str, str | None]:
    executable = _probity_executable()
    if executable is None:
        return {"executable": None, "version": None}
    try:
        proc = subprocess.run(
            [executable, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"executable": executable, "version": None}
    match = _VERSION.search(f"{proc.stdout}\n{proc.stderr}")
    return {
        "executable": executable,
        "version": ".".join(match.groups()) if proc.returncode == 0 and match else None,
    }


def _git_root(path: Path) -> Path:
    candidate = path.expanduser().resolve()
    cwd = candidate if candidate.is_dir() else candidate.parent
    try:
        proc = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProbityRuntimeError(f"cannot inspect Git root for {path}: {exc}") from exc
    if proc.returncode != 0 or not proc.stdout.strip():
        raise ProbityRuntimeError(f"not inside a Git repository: {path}")
    return Path(proc.stdout.strip()).resolve()


def inspect_project(repo: Path) -> dict[str, Any]:
    root = _git_root(repo)
    configs = [name for name in CONFIG_NAMES if (root / name).is_file()]
    state = "enabled" if len(configs) == 1 else "missing" if not configs else "ambiguous"
    return {
        "root": str(root),
        "state": state,
        "config": configs[0] if len(configs) == 1 else None,
        "config_candidates": configs,
    }


def build_status(repo: Path, *, lock_file: Path = DEFAULT_LOCK_FILE) -> dict[str, Any]:
    lock = load_lock(lock_file)
    runtime = installed_runtime()
    project = inspect_project(repo)
    return {
        "schema": "mesh.probity.status.v1",
        "required_version": lock["version"],
        "installed": runtime,
        "runtime_aligned": runtime["version"] == lock["version"],
        "project": project,
        "supported_agents": lock["supported_agents"],
        "aligned": runtime["version"] == lock["version"] and project["state"] == "enabled",
    }


def _print_status(payload: dict[str, Any]) -> None:
    installed = payload["installed"]
    project = payload["project"]
    print(f"Probity required: {payload['required_version']}")
    print(f"Probity installed: {installed['version'] or 'missing'}")
    print(f"Executable: {installed['executable'] or 'missing'}")
    print(f"Repository: {project['root']}")
    print(f"Project opt-in: {project['state']}")
    print(f"Config: {project['config'] or '-'}")
    print("Agents: Codex supported; Antigravity contract/evidence only")
    print(f"Aligned: {'yes' if payload['aligned'] else 'no'}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock-file", type=Path, default=DEFAULT_LOCK_FILE)
    subparsers = parser.add_subparsers(dest="command", required=True)
    status = subparsers.add_parser("status", help="Inspect runtime and repository opt-in.")
    status.add_argument("repo", nargs="?", type=Path, default=Path.cwd())
    status.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = build_status(args.repo, lock_file=args.lock_file)
    except ProbityRuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(payload, sort_keys=True))
    else:
        _print_status(payload)
    return 0 if payload["aligned"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
