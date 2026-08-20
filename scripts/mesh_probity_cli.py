#!/usr/bin/env python3
"""Inspect the pinned, repository-opt-in Probity runtime."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import tomllib
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCK_FILE = ROOT / "config" / "probity.lock.json"
DEFAULT_NPM_PREFIX = Path("~/.npm-global").expanduser()
DEFAULT_CODEX_HOME = Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()
HOOK_SOURCE = ROOT / "scripts" / "mesh_probity_hook.py"
HOOK_INSTALL_PATH = Path("~/.local/lib/gobabygo/mesh_probity_hook.py").expanduser()
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
            timeout=30,
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
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProbityRuntimeError(f"cannot inspect Git root for {path}: {exc}") from exc
    if proc.returncode != 0 or not proc.stdout.strip():
        raise ProbityRuntimeError(f"not inside a Git repository: {path}")
    return Path(proc.stdout.strip()).resolve()


def inspect_project(repo: Path) -> dict[str, Any]:
    root = _git_root(repo)
    candidates = [
        name
        for name in CONFIG_NAMES
        if (root / name).exists() or (root / name).is_symlink()
    ]
    unsafe = [name for name in candidates if (root / name).is_symlink() or not (root / name).is_file()]
    configs = [name for name in candidates if name not in unsafe]
    state = (
        "unsafe"
        if unsafe
        else "enabled"
        if len(configs) == 1
        else "missing"
        if not configs
        else "ambiguous"
    )
    return {
        "root": str(root),
        "state": state,
        "config": configs[0] if len(configs) == 1 else None,
        "config_candidates": candidates,
        "unsafe_config_candidates": unsafe,
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


def _atomic_write(path: Path, content: str, *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise ProbityRuntimeError(f"refusing symlink: {path}")
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _enable_hooks_feature(content: str) -> str:
    try:
        parsed = tomllib.loads(content) if content.strip() else {}
    except tomllib.TOMLDecodeError as exc:
        raise ProbityRuntimeError(f"invalid Codex config.toml: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ProbityRuntimeError("invalid Codex config.toml")
    lines = content.splitlines()
    headers = [index for index, line in enumerate(lines) if line.strip() == "[features]"]
    if len(headers) > 1:
        raise ProbityRuntimeError("Codex config.toml contains duplicate [features] sections")
    assignment = re.compile(r"^\s*(?:hooks|codex_hooks)\s*=")
    if not headers:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend(["[features]", "hooks = true"])
    else:
        start = headers[0]
        end = next(
            (index for index in range(start + 1, len(lines)) if lines[index].lstrip().startswith("[")),
            len(lines),
        )
        body = [line for line in lines[start + 1 : end] if not assignment.match(line)]
        lines[start + 1 : end] = ["hooks = true", *body]
    result = "\n".join(lines).rstrip() + "\n"
    try:
        verified = tomllib.loads(result)
    except tomllib.TOMLDecodeError as exc:
        raise ProbityRuntimeError(f"cannot produce valid Codex config.toml: {exc}") from exc
    if verified.get("features", {}).get("hooks") is not True:
        raise ProbityRuntimeError("cannot enable canonical Codex hooks feature")
    return result


def _merge_codex_hook(content: str, *, command: str) -> str:
    try:
        payload = json.loads(content) if content.strip() else {}
    except json.JSONDecodeError as exc:
        raise ProbityRuntimeError(f"invalid Codex hooks.json: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProbityRuntimeError("Codex hooks.json must contain an object")
    hooks = payload.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ProbityRuntimeError("Codex hooks.json hooks must contain an object")
    entries = hooks.setdefault("PreToolUse", [])
    if not isinstance(entries, list):
        raise ProbityRuntimeError("Codex hooks.json PreToolUse must contain a list")
    retained: list[Any] = []
    for entry in entries:
        serialized = json.dumps(entry, sort_keys=True) if isinstance(entry, dict) else ""
        if "mesh_probity_hook.py" not in serialized:
            retained.append(entry)
    retained.append(
        {
            "matcher": "^(Bash|apply_patch|Edit|Write)$",
            "hooks": [
                {
                    "type": "command",
                    "command": command,
                    "timeout": 125,
                    "statusMessage": "Checking repository TDD policy",
                }
            ],
        }
    )
    hooks["PreToolUse"] = retained
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _npm_pack(lock: dict[str, Any], destination: Path) -> Path:
    npm = shutil.which("npm")
    if npm is None:
        raise ProbityRuntimeError("npm is required to install Probity")
    package = f"{lock['package']}@{lock['version']}"
    try:
        proc = subprocess.run(
            [npm, "pack", package, "--json", "--pack-destination", str(destination)],
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProbityRuntimeError(f"cannot download pinned Probity package: {exc}") from exc
    try:
        records = json.loads(proc.stdout)
        filename = records[0]["filename"]
    except (json.JSONDecodeError, IndexError, KeyError, TypeError) as exc:
        raise ProbityRuntimeError("npm pack returned an invalid response") from exc
    archive = destination / Path(str(filename)).name
    if proc.returncode != 0 or not archive.is_file():
        raise ProbityRuntimeError("npm pack failed for pinned Probity package")
    digest = base64.b64encode(hashlib.sha512(archive.read_bytes()).digest()).decode("ascii")
    if f"sha512-{digest}" != lock["integrity"]:
        raise ProbityRuntimeError("Probity package integrity does not match the lock")
    return archive


def install_codex(
    *,
    apply: bool,
    lock_file: Path = DEFAULT_LOCK_FILE,
    npm_prefix: Path = DEFAULT_NPM_PREFIX,
    codex_home: Path = DEFAULT_CODEX_HOME,
    hook_path: Path = HOOK_INSTALL_PATH,
) -> dict[str, Any]:
    lock = load_lock(lock_file)
    npm_prefix = npm_prefix.expanduser().resolve()
    codex_home = codex_home.expanduser().resolve()
    hook_path = hook_path.expanduser().resolve()
    plan = {
        "schema": "mesh.probity.install.v1",
        "apply": apply,
        "version": lock["version"],
        "npm_prefix": str(npm_prefix),
        "hook_path": str(hook_path),
        "codex_config": str(codex_home / "config.toml"),
        "codex_hooks": str(codex_home / "hooks.json"),
        "restart_required": True,
    }
    if not apply:
        return plan
    if not HOOK_SOURCE.is_file():
        raise ProbityRuntimeError(f"dispatcher source not found: {HOOK_SOURCE}")
    with tempfile.TemporaryDirectory(prefix="mesh-probity-install-") as temporary:
        archive = _npm_pack(lock, Path(temporary))
        npm = shutil.which("npm")
        assert npm is not None
        try:
            proc = subprocess.run(
                [npm, "install", "--global", "--prefix", str(npm_prefix), str(archive)],
                check=False,
                capture_output=True,
                text=True,
                timeout=180,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ProbityRuntimeError(f"cannot install pinned Probity package: {exc}") from exc
        if proc.returncode != 0:
            raise ProbityRuntimeError("npm install failed for pinned Probity package")
    executable = npm_prefix / "bin" / "probity"
    try:
        proc = subprocess.run(
            [str(executable), "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProbityRuntimeError(f"cannot verify installed Probity: {exc}") from exc
    if proc.returncode != 0 or proc.stdout.strip() != lock["version"]:
        raise ProbityRuntimeError("installed Probity version does not match the lock")
    _atomic_write(hook_path, HOOK_SOURCE.read_text(encoding="utf-8"), mode=0o755)
    python = Path("/usr/bin/python3")
    if not python.is_file():
        found = shutil.which("python3")
        if found is None:
            raise ProbityRuntimeError("python3 is required for the Probity dispatcher")
        python = Path(found)
    command = (
        f"MESH_PROBITY_EXPECTED_VERSION={shlex.quote(lock['version'])} "
        f"MESH_PROBITY_BIN={shlex.quote(str(executable))} "
        f"{shlex.quote(str(python))} {shlex.quote(str(hook_path))}"
    )
    config_path = codex_home / "config.toml"
    hooks_path = codex_home / "hooks.json"
    config_content = config_path.read_text(encoding="utf-8") if config_path.is_file() else ""
    hooks_content = hooks_path.read_text(encoding="utf-8") if hooks_path.is_file() else ""
    _atomic_write(config_path, _enable_hooks_feature(config_content), mode=0o600)
    _atomic_write(hooks_path, _merge_codex_hook(hooks_content, command=command), mode=0o600)
    plan["installed_executable"] = str(executable)
    return plan


def smoke(*, executable: Path | None = None) -> dict[str, Any]:
    runtime = str(executable.resolve()) if executable else _probity_executable()
    if runtime is None:
        raise ProbityRuntimeError("Probity runtime is unavailable")
    with tempfile.TemporaryDirectory(prefix="mesh-probity-smoke-") as temporary:
        repo = Path(temporary) / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        (repo / "probity.config.mjs").write_text(
            "import { defineConfig, forbidCommandPattern } from '@nizos/probity'\n"
            "export default defineConfig({ rules: [forbidCommandPattern({\n"
            "  match: 'MESH_PROBITY_SMOKE_BLOCK', reason: 'synthetic smoke block'\n"
            "})] })\n",
            encoding="utf-8",
        )
        hook = HOOK_SOURCE
        base = {
            "session_id": "mesh-probity-smoke",
            "transcript_path": None,
            "cwd": str(repo),
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_use_id": "smoke-1",
        }

        def invoke(command: str) -> dict[str, Any]:
            payload = {**base, "tool_input": {"command": command}}
            proc = subprocess.run(
                [sys.executable, str(hook)],
                input=json.dumps(payload),
                env={**os.environ, "MESH_PROBITY_BIN": runtime},
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if proc.returncode != 0:
                raise ProbityRuntimeError("Probity smoke dispatcher failed")
            try:
                return json.loads(proc.stdout)
            except json.JSONDecodeError as exc:
                raise ProbityRuntimeError("Probity smoke returned invalid JSON") from exc

        allowed = invoke("printf safe")
        blocked = invoke("printf MESH_PROBITY_SMOKE_BLOCK")
    denied = (
        blocked.get("decision") == "block"
        or blocked.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
    )
    if allowed or not denied or "synthetic smoke block" not in json.dumps(blocked):
        raise ProbityRuntimeError("Probity smoke did not observe the expected allow/block decisions")
    return {
        "schema": "mesh.probity.smoke.v1",
        "runtime": runtime,
        "allow": "pass",
        "block": "pass",
        "target": "temporary-git-repository",
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
    install = subparsers.add_parser("install", help="Plan or apply the pinned Codex integration.")
    install.add_argument("--apply", action="store_true")
    install.add_argument("--npm-prefix", type=Path, default=DEFAULT_NPM_PREFIX)
    install.add_argument("--codex-home", type=Path, default=DEFAULT_CODEX_HOME)
    install.add_argument("--hook-path", type=Path, default=HOOK_INSTALL_PATH)
    install.add_argument("--json", action="store_true")
    smoke_parser = subparsers.add_parser("smoke", help="Run an isolated deterministic allow/block smoke.")
    smoke_parser.add_argument("--executable", type=Path)
    smoke_parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "status":
            payload = build_status(args.repo, lock_file=args.lock_file)
        elif args.command == "install":
            payload = install_codex(
                apply=args.apply,
                lock_file=args.lock_file,
                npm_prefix=args.npm_prefix,
                codex_home=args.codex_home,
                hook_path=args.hook_path,
            )
        else:
            payload = smoke(executable=args.executable)
    except ProbityRuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(payload, sort_keys=True))
    elif args.command == "status":
        _print_status(payload)
    else:
        print(json.dumps(payload, indent=2, sort_keys=True))
    if args.command == "status":
        return 0 if payload["aligned"] else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
