#!/usr/bin/env python3
"""Inspect the pinned official Spec Kit runtime and project capabilities."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Callable, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCK_FILE = ROOT / "config" / "speckit.lock.json"
DEFAULT_STATE_FILE = Path(
    os.environ.get(
        "MESH_SPECKIT_UPDATE_STATE",
        "~/.local/state/gobabygo/speckit-update.json",
    )
).expanduser()
RELEASE_API = "https://api.github.com/repos/github/spec-kit/releases/latest"
ALLOWED_INTEGRATIONS = ("claude", "codex", "agy")
INTEGRATION_SKILL_ROOTS = {
    "claude": Path(".claude/skills"),
    "codex": Path(".agents/skills"),
    "agy": Path(".agents/skills"),
}
_VERSION = re.compile(r"(?<![0-9])v?(0|[1-9][0-9]*)\.(0|[0-9]+)\.(0|[0-9]+)(?![0-9])")
_SAFE_INTEGRATION = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_SAFE_CAPABILITY = re.compile(r"^[a-z][a-z0-9.-]{0,79}$")
_IMMUTABLE_REVIEW_SCOPE = re.compile(
    r"^(?:commit:[0-9a-f]{40}(?:\.\.[0-9a-f]{40})?|diff-sha256:[0-9a-f]{64})$"
)
_MAX_RELEASE_BYTES = 1024 * 1024


class SpeckitRuntimeError(RuntimeError):
    pass


def _version_from_text(value: str) -> str | None:
    match = _VERSION.search(str(value or ""))
    if match is None:
        return None
    return ".".join(match.groups())


def _version_tuple(value: str | None) -> tuple[int, int, int] | None:
    version = _version_from_text(str(value or ""))
    if version is None:
        return None
    return tuple(int(item) for item in version.split("."))  # type: ignore[return-value]


def _load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SpeckitRuntimeError(f"{label} not found: {path}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SpeckitRuntimeError(f"cannot read {label} {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SpeckitRuntimeError(f"{label} must contain a JSON object: {path}")
    return payload


def load_lock(path: Path = DEFAULT_LOCK_FILE) -> dict[str, Any]:
    payload = _load_json_object(path.resolve(), label="Spec Kit lock")
    version = _version_from_text(str(payload.get("version", "")))
    tag_version = _version_from_text(str(payload.get("tag", "")))
    integrations = payload.get("integrations")
    if payload.get("schema") != 1 or version is None or tag_version != version:
        raise SpeckitRuntimeError("invalid Spec Kit lock schema, version, or tag")
    if integrations != list(ALLOWED_INTEGRATIONS):
        raise SpeckitRuntimeError(
            "Spec Kit lock integrations must be exactly claude, codex, agy"
        )
    return {
        "schema": 1,
        "version": version,
        "tag": f"v{version}",
        "source": str(payload.get("source", "")),
        "integrations": list(ALLOWED_INTEGRATIONS),
    }


def installed_version(
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    executable = shutil.which("specify")
    if executable is None:
        return {"available": False, "executable": None, "version": None, "error": None}
    try:
        proc = runner(
            [executable, "version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "available": True,
            "executable": executable,
            "version": None,
            "error": str(exc),
        }
    output = f"{proc.stdout}\n{proc.stderr}"
    version = _version_from_text(output) if proc.returncode == 0 else None
    error = None
    if version is None:
        error = f"specify version failed with exit {proc.returncode}"
    return {
        "available": True,
        "executable": executable,
        "version": version,
        "error": error,
    }


def _manifest_integrations(payload: dict[str, Any]) -> tuple[list[str], str | None]:
    raw = payload.get("installed_integrations")
    installed: list[str] = []
    if isinstance(raw, list):
        for item in raw:
            value = str(item or "").strip()
            if _SAFE_INTEGRATION.fullmatch(value) and value not in installed:
                installed.append(value)
    default = str(payload.get("default_integration") or payload.get("integration") or "").strip()
    if not _SAFE_INTEGRATION.fullmatch(default):
        default = ""
    if default and default not in installed:
        installed.insert(0, default)
    return installed, default or None


def _skill_capabilities(root: Path) -> list[str]:
    if not root.is_dir():
        return []
    capabilities: list[str] = []
    for skill in sorted(root.glob("speckit-*/SKILL.md")):
        name = skill.parent.name.removeprefix("speckit-").replace("-", ".")
        if _SAFE_CAPABILITY.fullmatch(name) and name not in capabilities:
            capabilities.append(name)
    return capabilities


def inspect_project(repo: Path, required_integrations: Sequence[str]) -> dict[str, Any]:
    root = repo.expanduser().resolve()
    manifest_path = root / ".specify" / "integration.json"
    result: dict[str, Any] = {
        "root": str(root),
        "manifest": str(manifest_path),
        "state": "missing",
        "default_integration": None,
        "installed_integrations": [],
        "missing_integrations": list(required_integrations),
        "unsupported_integrations": [],
        "capabilities": {},
        "enabled_capabilities": [],
        "error": None,
    }
    if not root.is_dir():
        result["state"] = "invalid"
        result["error"] = "project directory does not exist"
        return result
    try:
        payload = _load_json_object(manifest_path, label="Spec Kit integration manifest")
    except SpeckitRuntimeError as exc:
        if manifest_path.exists():
            result["state"] = "invalid"
            result["error"] = str(exc)
        return result

    installed, default = _manifest_integrations(payload)
    required = list(required_integrations)
    missing = [item for item in required if item not in installed]
    unsupported = [item for item in installed if item not in ALLOWED_INTEGRATIONS]
    capabilities: dict[str, list[str]] = {}
    for integration in required:
        if integration not in installed:
            capabilities[integration] = []
            continue
        capabilities[integration] = _skill_capabilities(
            root / INTEGRATION_SKILL_ROOTS[integration]
        )
    enabled_sets = [set(capabilities[item]) for item in required if item in installed]
    enabled = sorted(set.intersection(*enabled_sets)) if enabled_sets and not missing else []
    has_empty = any(not capabilities[item] for item in required if item in installed)

    if unsupported:
        state = "unsupported"
    elif missing or has_empty:
        state = "partial"
    else:
        state = "aligned"
    result.update(
        {
            "state": state,
            "default_integration": default,
            "installed_integrations": installed,
            "missing_integrations": missing,
            "unsupported_integrations": unsupported,
            "capabilities": capabilities,
            "enabled_capabilities": enabled,
        }
    )
    return result


def _load_cached_release(path: Path) -> dict[str, Any] | None:
    try:
        payload = _load_json_object(path, label="Spec Kit update state")
    except SpeckitRuntimeError:
        return None
    version = _version_from_text(str(payload.get("version", "")))
    tag = str(payload.get("tag", ""))
    if version is None or tag != f"v{version}":
        return None
    return {
        "version": version,
        "tag": tag,
        "published_at": str(payload.get("published_at", ""))[:40],
        "html_url": str(payload.get("html_url", ""))[:300],
        "checked_at": str(payload.get("checked_at", ""))[:40],
    }


def build_status(
    repo: Path,
    *,
    lock_file: Path = DEFAULT_LOCK_FILE,
    state_file: Path = DEFAULT_STATE_FILE,
) -> dict[str, Any]:
    lock = load_lock(lock_file)
    installed = installed_version()
    cached = _load_cached_release(state_file)
    project = inspect_project(repo, lock["integrations"])
    latest = cached["version"] if cached else None
    latest_tuple = _version_tuple(latest)
    required_tuple = _version_tuple(lock["version"])
    return {
        "schema": "mesh.speckit.status.v1",
        "required_version": lock["version"],
        "installed": installed,
        "latest_known_version": latest,
        "update_available": bool(
            latest_tuple and required_tuple and latest_tuple > required_tuple
        ),
        "runtime_aligned": installed["version"] == lock["version"],
        "project": project,
        "aligned": installed["version"] == lock["version"] and project["state"] == "aligned",
    }


def _bounded_repo_path(root: Path, value: Path, *, label: str) -> tuple[Path, str]:
    candidate = value if value.is_absolute() else root / value
    resolved = candidate.resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise SpeckitRuntimeError(f"{label} must stay inside the repository") from exc
    if not relative.parts:
        raise SpeckitRuntimeError(f"{label} cannot be the repository root")
    return resolved, relative.as_posix()


def build_delegation_context(
    repo: Path,
    *,
    phase: str,
    feature_dir: Path,
    artifacts: Sequence[Path],
    role: str,
    review_scope: str = "",
    lock_file: Path = DEFAULT_LOCK_FILE,
) -> dict[str, Any]:
    """Build a provider-neutral, repository-derived delegation envelope."""
    root = _git_root(repo)
    lock = load_lock(lock_file)
    installed = installed_version()
    project = inspect_project(root, lock["integrations"])
    normalized_phase = str(phase or "").strip()
    if installed["version"] != lock["version"] or project["state"] != "aligned":
        raise SpeckitRuntimeError("Spec Kit runtime and project must be aligned")
    if normalized_phase not in project["enabled_capabilities"]:
        raise SpeckitRuntimeError(
            f"Spec Kit phase is not enabled for this project: {normalized_phase or '-'}"
        )
    if role not in {"writer", "reviewer"}:
        raise SpeckitRuntimeError("delegation role must be writer or reviewer")

    feature_path, feature_relative = _bounded_repo_path(
        root, feature_dir, label="feature directory"
    )
    if not feature_path.is_dir():
        raise SpeckitRuntimeError(f"feature directory does not exist: {feature_relative}")
    if not artifacts or len(artifacts) > 32:
        raise SpeckitRuntimeError("one to 32 allowed artifacts are required")
    allowed: list[str] = []
    for artifact in artifacts:
        artifact_path, artifact_relative = _bounded_repo_path(
            root,
            artifact if artifact.is_absolute() else feature_path / artifact,
            label="allowed artifact",
        )
        try:
            artifact_path.relative_to(feature_path)
        except ValueError as exc:
            raise SpeckitRuntimeError(
                "allowed artifacts must stay inside the feature directory"
            ) from exc
        if artifact_relative not in allowed:
            allowed.append(artifact_relative)
    allowed.sort()

    immutable_scope = str(review_scope or "").strip().lower()
    if role == "reviewer":
        if not _IMMUTABLE_REVIEW_SCOPE.fullmatch(immutable_scope):
            raise SpeckitRuntimeError(
                "reviewer requires --review-scope commit:<sha>[..<sha>] or diff-sha256:<digest>"
            )
    elif immutable_scope:
        raise SpeckitRuntimeError("--review-scope is valid only for reviewer context")

    return {
        "schema": "mesh.speckit.context.v1",
        "version": lock["version"],
        "phase": normalized_phase,
        "feature_dir": feature_relative,
        "allowed_artifacts": allowed,
        "role": role,
        "review_scope": immutable_scope or "not-applicable",
        "review_policy": (
            "read-only-independent-provider" if role == "reviewer" else "different-provider-required"
        ),
    }


def _fetch_latest_release() -> dict[str, str]:
    request = Request(
        RELEASE_API,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "gobabygo-speckit-check"},
    )
    try:
        with urlopen(request, timeout=10) as response:  # noqa: S310 - fixed HTTPS endpoint
            raw = response.read(_MAX_RELEASE_BYTES + 1)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise SpeckitRuntimeError(f"Spec Kit update check failed: {exc}") from exc
    if len(raw) > _MAX_RELEASE_BYTES:
        raise SpeckitRuntimeError("Spec Kit release response exceeds size limit")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise SpeckitRuntimeError(f"invalid Spec Kit release response: {exc}") from exc
    if not isinstance(payload, dict):
        raise SpeckitRuntimeError("invalid Spec Kit release response shape")
    version = _version_from_text(str(payload.get("tag_name", "")))
    if version is None:
        raise SpeckitRuntimeError("Spec Kit release response has no valid tag")
    url = str(payload.get("html_url", ""))
    if not url.startswith("https://github.com/github/spec-kit/releases/tag/"):
        raise SpeckitRuntimeError("Spec Kit release response has an unexpected URL")
    return {
        "version": version,
        "tag": f"v{version}",
        "published_at": str(payload.get("published_at", ""))[:40],
        "html_url": url[:300],
    }


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise SpeckitRuntimeError(f"refusing symlink update state: {path}")
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def update_check(state_file: Path = DEFAULT_STATE_FILE) -> dict[str, Any]:
    from datetime import datetime, timezone

    release = _fetch_latest_release()
    payload = {**release, "checked_at": datetime.now(timezone.utc).isoformat()}
    _atomic_write_json(state_file, payload)
    return payload


def _run_command(
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout: float = 300,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        cwd=str(cwd) if cwd else None,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _git_root(repo: Path) -> Path:
    root = repo.expanduser().resolve()
    if not root.is_dir():
        raise SpeckitRuntimeError(f"project directory does not exist: {root}")
    proc = _run_command(["git", "-C", str(root), "rev-parse", "--show-toplevel"], timeout=10)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise SpeckitRuntimeError(f"project is not a Git repository: {root}")
    git_root = Path(proc.stdout.strip()).resolve()
    if git_root != root:
        raise SpeckitRuntimeError(f"project must be the exact Git repository root: {root}")
    return root


def _git_status(repo: Path) -> list[str]:
    proc = _run_command(["git", "-C", str(repo), "status", "--short"], timeout=15)
    if proc.returncode != 0:
        raise SpeckitRuntimeError(f"cannot inspect Git status for {repo}")
    return [line for line in proc.stdout.splitlines() if line]


def build_install_plan(version: str, lock: dict[str, Any]) -> dict[str, Any]:
    requested = _version_from_text(version)
    if requested is None or version not in {requested, f"v{requested}"}:
        raise SpeckitRuntimeError("install requires one exact semantic version")
    if requested != lock["version"]:
        raise SpeckitRuntimeError(
            f"requested Spec Kit {requested} does not match lock {lock['version']}"
        )
    uv = shutil.which("uv") or str(Path("~/.local/bin/uv").expanduser())
    if not Path(uv).is_file() and shutil.which("uv") is None:
        raise SpeckitRuntimeError("uv is required to install the pinned Spec Kit CLI")
    return {
        "schema": "mesh.speckit.install-plan.v1",
        "version": requested,
        "commands": [
            [
                uv,
                "tool",
                "install",
                "--force",
                "specify-cli",
                "--from",
                f"git+https://github.com/github/spec-kit.git@v{requested}",
            ],
            ["specify", "check"],
        ],
    }


def apply_install_plan(plan: dict[str, Any]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for command in plan["commands"]:
        proc = _run_command(command)
        results.append({"command": command, "returncode": proc.returncode})
        if proc.returncode != 0:
            raise SpeckitRuntimeError(
                f"Spec Kit install command failed ({proc.returncode}): {' '.join(command)}"
            )
    installed = installed_version()
    if installed["version"] != plan["version"]:
        raise SpeckitRuntimeError(
            f"installed Spec Kit version {installed['version'] or 'unknown'} "
            f"does not match {plan['version']}"
        )
    return {**plan, "applied": True, "results": results, "installed": installed}


def build_project_plan(
    action: str,
    repo: Path,
    lock: dict[str, Any],
    *,
    allow_multi_install_force: bool = False,
) -> dict[str, Any]:
    root = _git_root(repo)
    status = _git_status(root)
    if status:
        raise SpeckitRuntimeError(
            f"project worktree must be clean before Spec Kit {action}: {root}"
        )
    project = inspect_project(root, lock["integrations"])
    manifest_exists = Path(project["manifest"]).is_file()
    if action == "init":
        if manifest_exists:
            raise SpeckitRuntimeError("project is already initialized; use project upgrade")
        if not allow_multi_install_force:
            raise SpeckitRuntimeError(
                "AGY multi-install in Spec Kit v0.16.5 requires explicit "
                "--allow-multi-install-force"
            )
        commands = [
            [
                "specify",
                "init",
                "--here",
                "--force",
                "--integration",
                "claude",
                "--script",
                "sh",
                "--ignore-agent-tools",
            ],
            ["specify", "integration", "install", "codex"],
            ["specify", "integration", "install", "agy", "--force"],
            ["specify", "integration", "use", "claude"],
        ]
    elif action == "upgrade":
        if not manifest_exists:
            raise SpeckitRuntimeError("project is not initialized; use project init")
        if project["unsupported_integrations"]:
            raise SpeckitRuntimeError(
                "project has unsupported active integrations: "
                + ",".join(project["unsupported_integrations"])
            )
        if project["missing_integrations"]:
            raise SpeckitRuntimeError(
                "project is missing required integrations: "
                + ",".join(project["missing_integrations"])
            )
        commands = [
            ["specify", "integration", "upgrade", integration]
            for integration in lock["integrations"]
        ]
    else:
        raise SpeckitRuntimeError(f"unsupported project action: {action}")
    return {
        "schema": "mesh.speckit.project-plan.v1",
        "action": action,
        "repo": str(root),
        "required_version": lock["version"],
        "integrations": lock["integrations"],
        "commands": commands,
        "apply_required": True,
    }


def apply_project_plan(plan: dict[str, Any]) -> dict[str, Any]:
    if shutil.which("specify") is None:
        raise SpeckitRuntimeError("specify CLI is not installed")
    root = Path(plan["repo"])
    results: list[dict[str, Any]] = []
    for command in plan["commands"]:
        proc = _run_command(command, cwd=root)
        results.append({"command": command, "returncode": proc.returncode})
        if proc.returncode != 0:
            changed = _git_status(root)
            raise SpeckitRuntimeError(
                f"Spec Kit project command failed ({proc.returncode}); "
                f"partial changed paths: {', '.join(changed) or '-'}"
            )
    changed = _git_status(root)
    return {**plan, "applied": True, "results": results, "changed_paths": changed}


def _render_status(payload: dict[str, Any]) -> str:
    project = payload["project"]
    installed = payload["installed"]["version"] or "missing"
    latest = payload["latest_known_version"] or "unknown"
    capabilities = ",".join(project["enabled_capabilities"]) or "-"
    integrations = ",".join(project["installed_integrations"]) or "-"
    return "\n".join(
        [
            "Spec Kit",
            f"required={payload['required_version']}",
            f"installed={installed}",
            f"latest_known={latest}",
            f"project={project['state']}",
            f"integrations={integrations}",
            f"capabilities={capabilities}",
            f"aligned={'yes' if payload['aligned'] else 'no'}",
        ]
    )


def _render_context(payload: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"SPECKIT_CONTEXT version={payload['version']} phase={payload['phase']}",
            f"feature_dir={payload['feature_dir']}",
            f"role={payload['role']}",
            f"review_scope={payload['review_scope']}",
            f"review_policy={payload['review_policy']}",
            "allowed_artifacts=" + ",".join(payload["allowed_artifacts"]),
        ]
    )


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock-file", type=Path, default=DEFAULT_LOCK_FILE)
    parser.add_argument("--state-file", type=Path, default=DEFAULT_STATE_FILE)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "capabilities"):
        command = sub.add_parser(name)
        command.add_argument("repo", nargs="?", type=Path, default=Path.cwd())
        command.add_argument("--json", action="store_true")
    check = sub.add_parser("update-check")
    check.add_argument("--json", action="store_true")
    install = sub.add_parser("install")
    install.add_argument("version")
    install.add_argument("--apply", action="store_true")
    install.add_argument("--json", action="store_true")
    project = sub.add_parser("project")
    project_sub = project.add_subparsers(dest="project_action", required=True)
    for action in ("init", "upgrade"):
        project_action = project_sub.add_parser(action)
        project_action.add_argument("repo", type=Path)
        project_action.add_argument("--apply", action="store_true")
        project_action.add_argument("--json", action="store_true")
        project_action.add_argument("--allow-multi-install-force", action="store_true")
    context = sub.add_parser("context")
    context.add_argument("repo", type=Path)
    context.add_argument("--phase", required=True)
    context.add_argument("--feature-dir", type=Path, required=True)
    context.add_argument("--artifact", action="append", type=Path, required=True)
    context.add_argument("--role", choices=("writer", "reviewer"), required=True)
    context.add_argument("--review-scope", default="")
    context.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        if args.command == "install":
            plan = build_install_plan(args.version, load_lock(args.lock_file))
            output = apply_install_plan(plan) if args.apply else {**plan, "applied": False}
            aligned = True
        elif args.command == "context":
            output = build_delegation_context(
                args.repo,
                phase=args.phase,
                feature_dir=args.feature_dir,
                artifacts=args.artifact,
                role=args.role,
                review_scope=args.review_scope,
                lock_file=args.lock_file,
            )
            aligned = True
        elif args.command == "project":
            plan = build_project_plan(
                args.project_action,
                args.repo,
                load_lock(args.lock_file),
                allow_multi_install_force=args.allow_multi_install_force,
            )
            output = apply_project_plan(plan) if args.apply else {**plan, "applied": False}
            aligned = True
        elif args.command == "update-check":
            payload = update_check(args.state_file)
            output: Any = payload
            aligned = True
        else:
            status = build_status(
                args.repo,
                lock_file=args.lock_file,
                state_file=args.state_file,
            )
            output = status if args.command == "status" else {
                "schema": "mesh.speckit.capabilities.v1",
                "required_version": status["required_version"],
                "runtime_aligned": status["runtime_aligned"],
                "project": status["project"],
            }
            aligned = status["aligned"]
    except SpeckitRuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(output, indent=2, sort_keys=True))
    elif args.command == "update-check":
        print(f"Spec Kit latest={output['version']} checked_at={output['checked_at']}")
    elif args.command in {"install", "project"}:
        print(f"Spec Kit plan applied={'yes' if output['applied'] else 'no'}")
        for command in output["commands"]:
            print("  " + " ".join(command))
        if output.get("changed_paths"):
            print("Changed paths:")
            for path in output["changed_paths"]:
                print(f"  {path}")
    elif args.command == "context":
        print(_render_context(output))
    else:
        print(_render_status(status))
    return 0 if aligned else 1


if __name__ == "__main__":
    raise SystemExit(main())
