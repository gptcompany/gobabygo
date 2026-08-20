#!/usr/bin/env python3
"""Inspect the pinned official Spec Kit runtime and project capabilities."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
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
_ISO_TIMESTAMP = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})$"
)
_IMMUTABLE_REVIEW_SCOPE = re.compile(
    r"^(?:commit:[0-9a-f]{40}(?:\.\.[0-9a-f]{40})?|diff-sha256:[0-9a-f]{64})$"
)
_MAX_RELEASE_BYTES = 1024 * 1024
_MAX_MIGRATION_FILES = 512
_MAX_MIGRATION_FILE_BYTES = 8 * 1024 * 1024
_MAX_MIGRATION_TOTAL_BYTES = 32 * 1024 * 1024
_GENERATED_UPDATE_PREFIXES = (
    ".specify/templates/",
    ".specify/scripts/",
    ".specify/workflows/",
)
_GENERATED_UPDATE_FILES = {
    ".specify/.gitignore",
    ".specify/init-options.json",
}
_PROTECTED_MIGRATION_FILES = {".specify/memory/constitution.md"}
_VOLATILE_WORKFLOW_REGISTRY = ".specify/workflows/workflow-registry.json"
_VOLATILE_INTEGRATION_PREFIX = ".specify/integrations/"
_LEGACY_CONSTITUTION_PATH = "memory/constitution.md"
_LEGACY_COMMAND_NAMES = {
    "analyze.md",
    "checklist.md",
    "clarify.md",
    "constitution.md",
    "implement.md",
    "plan.md",
    "specify.md",
    "tasks.md",
    "taskstoissues.md",
}


class SpeckitRuntimeError(RuntimeError):
    pass


class _MigrationInterrupted(BaseException):
    def __init__(self, signal_number: int) -> None:
        self.signal_number = signal_number
        super().__init__(f"received {signal.Signals(signal_number).name}")


@contextmanager
def _defer_migration_signals():
    pending: list[int] = []
    previous: dict[int, Any] = {}

    def defer(signal_number: int, _frame: Any) -> None:
        if not pending:
            pending.append(signal_number)

    try:
        for signal_number in (signal.SIGINT, signal.SIGTERM):
            previous[signal_number] = signal.getsignal(signal_number)
            signal.signal(signal_number, defer)
    except ValueError as exc:
        for signal_number, handler in previous.items():
            signal.signal(signal_number, handler)
        raise SpeckitRuntimeError(
            "migration apply must run in the main process thread"
        ) from exc
    try:
        yield pending
    finally:
        for signal_number, handler in previous.items():
            signal.signal(
                signal_number, signal.SIG_DFL if handler is None else handler
            )


def _specify_executable() -> str | None:
    executable = shutil.which("specify")
    if executable:
        return executable
    user_executable = Path("~/.local/bin/specify").expanduser()
    if user_executable.is_file() and os.access(user_executable, os.X_OK):
        return str(user_executable)
    return None


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
    executable = _specify_executable()
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


def _require_pinned_runtime(required_version: str) -> dict[str, Any]:
    runtime = installed_version()
    if runtime["version"] != required_version:
        raise SpeckitRuntimeError(
            f"operation requires pinned Spec Kit {required_version}; "
            f"installed={runtime['version'] or 'missing'}"
        )
    return runtime


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


def _legacy_project_evidence(root: Path) -> list[str]:
    """Return bounded evidence for pre-manifest Spec Kit projects."""
    evidence: list[str] = []
    specify_root = root / ".specify"
    if specify_root.is_dir():
        evidence.append(".specify/")
    legacy_constitution = root / _LEGACY_CONSTITUTION_PATH
    if legacy_constitution.is_file() or legacy_constitution.is_symlink():
        evidence.append(_LEGACY_CONSTITUTION_PATH)

    command_roots = (
        root / ".claude" / "commands",
        root / ".agents" / "skills",
    )
    for command_root in command_roots:
        if not command_root.is_dir():
            continue
        entries = {path.name for path in command_root.iterdir()}
        if any(name.startswith("speckit") for name in entries):
            evidence.append(command_root.relative_to(root).as_posix() + "/speckit*")
        elif (
            (legacy_names := sorted(entries.intersection(_LEGACY_COMMAND_NAMES)))
            and (len(legacy_names) >= 2 or bool(evidence))
        ):
            evidence.append(
                command_root.relative_to(root).as_posix()
                + "/{"
                + ",".join(legacy_names)
                + "}"
            )

    specs_root = root / "specs"
    if specs_root.is_dir():
        for feature in sorted(specs_root.iterdir()):
            if not feature.is_dir():
                continue
            artifacts = [
                name for name in ("spec.md", "plan.md", "tasks.md") if (feature / name).is_file()
            ]
            if len(artifacts) >= 2:
                evidence.append(
                    feature.relative_to(root).as_posix() + "/{" + ",".join(artifacts) + "}"
                )
                break
    return evidence[:4]


def _legacy_command_paths(root: Path) -> list[str]:
    command_root = root / ".claude" / "commands"
    if not command_root.is_dir():
        return []
    paths: list[str] = []
    for path in sorted(command_root.iterdir()):
        if not path.is_file():
            continue
        if path.name.startswith("speckit") or path.name in _LEGACY_COMMAND_NAMES:
            paths.append(path.relative_to(root).as_posix())
    return paths[:32]


def inspect_project(
    repo: Path,
    required_integrations: Sequence[str],
    required_version: str | None = None,
) -> dict[str, Any]:
    root = repo.expanduser().resolve()
    manifest_path = root / ".specify" / "integration.json"
    result: dict[str, Any] = {
        "root": str(root),
        "manifest": str(manifest_path),
        "state": "missing",
        "default_integration": None,
        "manifest_version": None,
        "version_aligned": None,
        "installed_integrations": [],
        "missing_integrations": list(required_integrations),
        "unsupported_integrations": [],
        "capabilities": {},
        "enabled_capabilities": [],
        "legacy_evidence": [],
        "legacy_commands": [],
        "error": None,
    }
    if not root.is_dir():
        result["state"] = "invalid"
        result["error"] = "project directory does not exist"
        return result
    try:
        result["legacy_commands"] = _legacy_command_paths(root)
    except OSError as exc:
        result["state"] = "invalid"
        result["error"] = f"cannot inspect project commands: {exc}"
        return result
    try:
        payload = _load_json_object(manifest_path, label="Spec Kit integration manifest")
    except SpeckitRuntimeError as exc:
        try:
            if manifest_path.exists():
                result["state"] = "invalid"
                result["error"] = str(exc)
            else:
                evidence = _legacy_project_evidence(root)
                if evidence:
                    result["state"] = "legacy"
                    result["legacy_evidence"] = evidence
        except OSError as inspect_exc:
            result["state"] = "invalid"
            result["error"] = f"cannot inspect legacy project: {inspect_exc}"
        return result

    installed, default = _manifest_integrations(payload)
    manifest_version = _version_from_text(str(payload.get("version", "")))
    version_aligned = required_version is None or manifest_version == required_version
    required = list(required_integrations)
    missing = [item for item in required if item not in installed]
    unsupported = [item for item in installed if item not in ALLOWED_INTEGRATIONS]
    capabilities: dict[str, list[str]] = {}
    try:
        for integration in required:
            if integration not in installed:
                capabilities[integration] = []
                continue
            capabilities[integration] = _skill_capabilities(
                root / INTEGRATION_SKILL_ROOTS[integration]
            )
    except OSError as exc:
        result["state"] = "invalid"
        result["error"] = f"cannot inspect project skills: {exc}"
        return result
    enabled_sets = [set(capabilities[item]) for item in required if item in installed]
    enabled = sorted(set.intersection(*enabled_sets)) if enabled_sets and not missing else []
    has_empty = any(not capabilities[item] for item in required if item in installed)

    if unsupported:
        state = "unsupported"
    elif missing or has_empty or not version_aligned:
        state = "partial"
    else:
        state = "aligned"
    result.update(
        {
            "state": state,
            "default_integration": default,
            "manifest_version": manifest_version,
            "version_aligned": version_aligned,
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
    project = inspect_project(repo, lock["integrations"], lock["version"])
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
    project = inspect_project(root, lock["integrations"], lock["version"])
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
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    executable = Path(str(args[0])).name if args else "command"
    try:
        return subprocess.run(
            list(args),
            cwd=str(cwd) if cwd else None,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            input=input_text,
        )
    except subprocess.TimeoutExpired as exc:
        raise SpeckitRuntimeError(
            f"{executable} timed out after {timeout:g} seconds"
        ) from exc
    except OSError as exc:
        raise SpeckitRuntimeError(f"cannot start {executable}: {exc}") from exc


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


def _git_head(repo: Path) -> str:
    proc = _run_command(["git", "-C", str(repo), "rev-parse", "HEAD"], timeout=10)
    value = proc.stdout.strip().lower()
    if proc.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise SpeckitRuntimeError(f"cannot resolve Git HEAD for {repo}")
    return value


def _git_ignored_paths(repo: Path, paths: Sequence[str]) -> list[str]:
    if not paths:
        return []
    proc = _run_command(
        ["git", "-C", str(repo), "check-ignore", "--stdin", "-z"],
        timeout=15,
        input_text="\0".join(paths) + "\0",
    )
    if proc.returncode not in {0, 1}:
        raise SpeckitRuntimeError(f"cannot inspect ignored paths for {repo}")
    return sorted({path for path in proc.stdout.split("\0") if path})


def _git_internal_path(repo: Path, name: str) -> Path:
    proc = _run_command(
        ["git", "-C", str(repo), "rev-parse", "--git-path", name], timeout=10
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        raise SpeckitRuntimeError(f"cannot resolve Git internal path for {repo}")
    path = Path(proc.stdout.strip())
    return path if path.is_absolute() else (repo / path).resolve()


@contextmanager
def _migration_lock(repo: Path):
    lock_path = _git_internal_path(repo, "mesh-speckit-migrate.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    locked = False
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except BlockingIOError as exc:
            raise SpeckitRuntimeError(
                f"another Spec Kit migration is active for {repo}"
            ) from exc
        yield
    finally:
        try:
            if locked:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


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


def _project_init_commands() -> list[list[str]]:
    specify = _specify_executable() or "specify"
    return [
        [
            specify,
            "init",
            "--here",
            "--force",
            "--integration",
            "claude",
            "--script",
            "sh",
            "--ignore-agent-tools",
        ],
        [specify, "integration", "install", "codex"],
        [specify, "integration", "install", "agy", "--force"],
        [specify, "integration", "use", "claude"],
    ]


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(64 * 1024):
            size += len(chunk)
            if size > _MAX_MIGRATION_FILE_BYTES:
                raise SpeckitRuntimeError(f"migration file exceeds size limit: {path.name}")
            digest.update(chunk)
    return digest.hexdigest()


def _is_volatile_metadata(relative: str) -> bool:
    return relative == _VOLATILE_WORKFLOW_REGISTRY or (
        relative.startswith(_VOLATILE_INTEGRATION_PREFIX)
        and relative.endswith(".manifest.json")
    )


def _normalized_generated_digest(data: bytes, relative: str) -> str:
    if not _is_volatile_metadata(relative):
        return hashlib.sha256(data).hexdigest()
    try:
        payload = json.loads(data.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("top-level value is not an object")
        if relative == _VOLATILE_WORKFLOW_REGISTRY:
            workflows = payload.get("workflows")
            if not isinstance(workflows, dict):
                raise ValueError("workflows is not an object")
            for workflow in workflows.values():
                if not isinstance(workflow, dict):
                    raise ValueError("workflow entry is not an object")
                _normalize_generated_timestamp(workflow, "installed_at")
                _normalize_generated_timestamp(workflow, "updated_at")
        else:
            _normalize_generated_timestamp(payload, "installed_at")
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise SpeckitRuntimeError(
            f"invalid generated metadata file: {relative}"
        ) from exc
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


def _normalize_generated_timestamp(payload: dict[str, Any], key: str) -> None:
    if key not in payload:
        return
    value = payload[key]
    if not isinstance(value, str) or _ISO_TIMESTAMP.fullmatch(value) is None:
        raise ValueError(f"{key} is not an ISO timestamp")
    payload[key] = "<volatile-timestamp>"


def _generated_file_digest(path: Path, relative: str) -> str:
    data = path.read_bytes()
    if len(data) > _MAX_MIGRATION_FILE_BYTES:
        raise SpeckitRuntimeError(f"migration file exceeds size limit: {path.name}")
    return _normalized_generated_digest(data, relative)


def _is_generated_update(relative: str) -> bool:
    return relative in _GENERATED_UPDATE_FILES or relative.startswith(
        _GENERATED_UPDATE_PREFIXES
    )


def _migration_path_collides(repo: Path, relative: str) -> bool:
    path = Path(relative)
    current = repo
    for part in path.parts[:-1]:
        current = current / part
        if current.is_symlink() or (current.exists() and not current.is_dir()):
            return True
    target = repo / path
    return target.is_symlink() or (target.exists() and not target.is_file())


def _migration_path_has_symlink(repo: Path, relative: str) -> bool:
    current = repo
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _generate_migration_tree(staging: Path, commands: Sequence[Sequence[str]]) -> None:
    if _specify_executable() is None:
        raise SpeckitRuntimeError("specify CLI is required to inspect a legacy migration")
    init = _run_command(["git", "init", "-q"], cwd=staging, timeout=10)
    if init.returncode != 0:
        raise SpeckitRuntimeError("cannot initialize Spec Kit migration sandbox")
    for command in commands:
        proc = _run_command(command, cwd=staging)
        if proc.returncode != 0:
            raise SpeckitRuntimeError(
                f"Spec Kit migration sandbox failed ({proc.returncode}): "
                f"{' '.join(command)}"
            )


def _migration_inventory_from_tree(repo: Path, staging: Path) -> dict[str, Any]:
    additions: list[str] = []
    updates: list[str] = []
    preserved: list[str] = []
    collisions: list[str] = []
    generated = 0
    total_size = 0
    constitution_migrations: dict[str, str] = {}
    content_digests: dict[str, str] = {}
    for source in sorted(staging.rglob("*")):
        if ".git" in source.relative_to(staging).parts:
            continue
        if source.is_symlink():
            raise SpeckitRuntimeError("migration sandbox generated a symlink")
        if not source.is_file():
            continue
        generated += 1
        if generated > _MAX_MIGRATION_FILES:
            raise SpeckitRuntimeError("migration sandbox exceeds file count limit")
        size = source.stat().st_size
        total_size += size
        if size > _MAX_MIGRATION_FILE_BYTES or total_size > _MAX_MIGRATION_TOTAL_BYTES:
            raise SpeckitRuntimeError("migration sandbox exceeds size limit")
        relative = source.relative_to(staging).as_posix()
        target = repo / relative
        legacy_constitution = repo / _LEGACY_CONSTITUTION_PATH
        if (
            relative == ".specify/memory/constitution.md"
            and not target.exists()
            and (legacy_constitution.exists() or legacy_constitution.is_symlink())
        ):
            if (
                _migration_path_collides(repo, relative)
                or _migration_path_has_symlink(repo, _LEGACY_CONSTITUTION_PATH)
                or not legacy_constitution.is_file()
            ):
                collisions.append(relative)
            else:
                additions.append(relative)
                constitution_migrations[relative] = _LEGACY_CONSTITUTION_PATH
                content_digests[relative] = _file_digest(legacy_constitution)
            continue
        if _migration_path_collides(repo, relative):
            collisions.append(relative)
        elif not target.exists():
            additions.append(relative)
            content_digests[relative] = _generated_file_digest(source, relative)
        elif _file_digest(source) == _file_digest(target):
            continue
        elif relative in _PROTECTED_MIGRATION_FILES:
            preserved.append(relative)
        elif _is_generated_update(relative):
            updates.append(relative)
            content_digests[relative] = _generated_file_digest(source, relative)
        else:
            collisions.append(relative)
    return {
        "generated_files": generated,
        "additions": additions,
        "generated_updates": updates,
        "protected_preserved": preserved,
        "blocking_collisions": collisions,
        "legacy_constitution_migrations": constitution_migrations,
        "legacy_commands_preserved": _legacy_command_paths(repo),
        "generated_content_sha256": content_digests,
    }


def _migration_inventory(repo: Path, commands: Sequence[Sequence[str]]) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="mesh-speckit-migrate-") as temporary:
        staging = Path(temporary)
        _generate_migration_tree(staging, commands)
        return _migration_inventory_with_git(repo, staging)


def _migration_inventory_with_git(repo: Path, staging: Path) -> dict[str, Any]:
    inventory = _migration_inventory_from_tree(repo, staging)
    writable = inventory["additions"] + inventory["generated_updates"]
    return {
        **inventory,
        "ignored_generated_paths": _git_ignored_paths(repo, writable),
    }


def build_project_plan(
    action: str,
    repo: Path,
    lock: dict[str, Any],
    *,
    allow_multi_install_force: bool = False,
    accept_generated_updates: bool = False,
) -> dict[str, Any]:
    root = _git_root(repo)
    status = _git_status(root)
    if status:
        raise SpeckitRuntimeError(
            f"project worktree must be clean before Spec Kit {action}: {root}"
        )
    project = inspect_project(root, lock["integrations"], lock["version"])
    manifest_exists = Path(project["manifest"]).is_file()
    if action == "init":
        if manifest_exists:
            raise SpeckitRuntimeError("project is already initialized; use project upgrade")
        if project["state"] == "legacy":
            raise SpeckitRuntimeError("legacy project requires project migrate")
        if not allow_multi_install_force:
            raise SpeckitRuntimeError(
                "AGY multi-install in Spec Kit v0.16.5 requires explicit "
                "--allow-multi-install-force"
            )
        commands = _project_init_commands()
        _require_pinned_runtime(lock["version"])
        migration = _migration_inventory(root, commands)
    elif action == "migrate":
        if manifest_exists:
            raise SpeckitRuntimeError("project is already initialized; use project upgrade")
        if project["state"] != "legacy":
            raise SpeckitRuntimeError("project migrate requires legacy Spec Kit evidence")
        if not allow_multi_install_force:
            raise SpeckitRuntimeError(
                "AGY multi-install in Spec Kit v0.16.5 requires explicit "
                "--allow-multi-install-force"
            )
        _require_pinned_runtime(lock["version"])
        commands = _project_init_commands()
        migration = _migration_inventory(root, commands)
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
        specify = _specify_executable() or "specify"
        commands = [
            [specify, "integration", "upgrade", integration]
            for integration in lock["integrations"]
        ]
        migration = None
    else:
        raise SpeckitRuntimeError(f"unsupported project action: {action}")
    plan = {
        "schema": "mesh.speckit.project-plan.v1",
        "action": action,
        "repo": str(root),
        "required_version": lock["version"],
        "integrations": lock["integrations"],
        "commands": commands,
        "apply_required": True,
        "base_head": _git_head(root),
    }
    if migration is not None:
        ready = (
            not migration["blocking_collisions"]
            and not migration["ignored_generated_paths"]
            and (accept_generated_updates or not migration["generated_updates"])
        )
        plan.update(
            {
                "legacy_evidence": project["legacy_evidence"],
                "migration": migration,
                "accept_generated_updates": accept_generated_updates,
                "ready_to_apply": ready,
            }
        )
    return plan


def apply_project_plan(plan: dict[str, Any]) -> dict[str, Any]:
    if plan.get("action") in {"init", "migrate"} and plan.get("migration"):
        return apply_migration_plan(plan)
    _require_pinned_runtime(str(plan.get("required_version", "")))
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


def _safe_migration_target(root: Path, relative: str) -> tuple[Path, list[Path]]:
    path = Path(relative)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise SpeckitRuntimeError(f"invalid migration path: {relative}")
    current = root
    created: list[Path] = []
    for part in path.parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise SpeckitRuntimeError(f"migration path traverses symlink: {relative}")
        if current.exists() and not current.is_dir():
            raise SpeckitRuntimeError(f"migration parent is not a directory: {relative}")
        if not current.exists():
            current.mkdir()
            created.append(current)
    target = root / path
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise SpeckitRuntimeError(f"migration target is not a regular file: {relative}")
    return target, created


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_fd = os.open(path, flags)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _atomic_copy_migration_file(
    source: Path,
    target: Path,
    expected_digest: str | None = None,
    relative: str = "",
) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        target_handle = os.fdopen(fd, "wb")
        fd = -1
        digest = hashlib.sha256()
        normalized_data = bytearray() if _is_volatile_metadata(relative) else None
        with source.open("rb") as source_handle, target_handle:
            while chunk := source_handle.read(64 * 1024):
                digest.update(chunk)
                if normalized_data is not None:
                    normalized_data.extend(chunk)
                target_handle.write(chunk)
            target_handle.flush()
            os.fsync(target_handle.fileno())
            os.fchmod(target_handle.fileno(), source.stat().st_mode & 0o777)
        actual_digest = (
            _normalized_generated_digest(bytes(normalized_data), relative)
            if normalized_data is not None
            else digest.hexdigest()
        )
        if expected_digest is not None and actual_digest != expected_digest:
            raise SpeckitRuntimeError(
                f"migration source changed while copying: {source.name}"
            )
        os.replace(temporary, target)
        _fsync_directory(target.parent)
    except BaseException:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _restore_migration_file(target: Path, backup: tuple[bytes, int] | None) -> None:
    if backup is None:
        target.unlink(missing_ok=True)
        _fsync_directory(target.parent)
        return
    data, mode = backup
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.restore.", dir=target.parent)
    try:
        handle = os.fdopen(fd, "wb")
        fd = -1
        with handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), mode)
        os.replace(temporary, target)
        _fsync_directory(target.parent)
    except BaseException:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def apply_migration_plan(plan: dict[str, Any]) -> dict[str, Any]:
    root = _git_root(Path(plan["repo"]))
    with _migration_lock(root):
        return _apply_migration_plan_locked(plan, root)


def _apply_migration_plan_locked(plan: dict[str, Any], root: Path) -> dict[str, Any]:
    if _git_status(root):
        raise SpeckitRuntimeError("project worktree changed after migration planning")
    if _git_head(root) != plan.get("base_head"):
        raise SpeckitRuntimeError("project HEAD changed after migration planning")
    if not plan.get("ready_to_apply"):
        migration = plan.get("migration", {})
        if migration.get("blocking_collisions"):
            raise SpeckitRuntimeError("migration has blocking collisions")
        if migration.get("ignored_generated_paths"):
            raise SpeckitRuntimeError(
                "migration generated paths are ignored by Git: "
                + ",".join(migration["ignored_generated_paths"])
            )
        raise SpeckitRuntimeError(
            "migration generated updates require --accept-generated-updates"
        )

    try:
        _require_pinned_runtime(str(plan.get("required_version", "")))
    except SpeckitRuntimeError as exc:
        raise SpeckitRuntimeError(
            "Spec Kit runtime changed after migration planning"
        ) from exc

    with tempfile.TemporaryDirectory(prefix="mesh-speckit-migrate-apply-") as temporary:
        staging = Path(temporary)
        _generate_migration_tree(staging, plan["commands"])
        if _git_status(root) or _git_head(root) != plan.get("base_head"):
            raise SpeckitRuntimeError(
                "project changed while preparing the migration sandbox"
            )
        inventory = _migration_inventory_with_git(root, staging)
        if inventory != plan.get("migration"):
            raise SpeckitRuntimeError("migration inventory changed after planning")

        copy_paths = list(inventory["additions"])
        if plan.get("accept_generated_updates"):
            copy_paths.extend(inventory["generated_updates"])
        backups: dict[Path, tuple[bytes, int] | None] = {}
        created_dirs: list[Path] = []
        with _defer_migration_signals() as pending_signals:
            try:
                for relative in sorted(copy_paths):
                    legacy_source = inventory["legacy_constitution_migrations"].get(
                        relative
                    )
                    source = root / legacy_source if legacy_source else staging / relative
                    target, created = _safe_migration_target(root, relative)
                    created_dirs.extend(created)
                    backups[target] = (
                        (target.read_bytes(), target.stat().st_mode & 0o777)
                        if target.exists()
                        else None
                    )
                    _atomic_copy_migration_file(
                        source,
                        target,
                        inventory["generated_content_sha256"][relative],
                        relative,
                    )
                project = inspect_project(
                    root, ALLOWED_INTEGRATIONS, plan["required_version"]
                )
                manifest = _load_json_object(
                    root / ".specify" / "integration.json",
                    label="Spec Kit integration manifest",
                )
                manifest_version = _version_from_text(
                    str(manifest.get("version", ""))
                )
                if (
                    project["state"] != "aligned"
                    or manifest_version != plan["required_version"]
                ):
                    raise SpeckitRuntimeError(
                        "migration output is not aligned with the pinned runtime"
                    )
                changed_paths = _git_status(root)
                if pending_signals:
                    raise _MigrationInterrupted(pending_signals[0])
            except BaseException as exc:
                rollback_errors: list[str] = []
                for target, backup in reversed(list(backups.items())):
                    try:
                        _restore_migration_file(target, backup)
                    except BaseException as rollback_exc:  # pragma: no cover - filesystem failure
                        rollback_errors.append(f"{target}: {rollback_exc}")
                for directory in reversed(created_dirs):
                    try:
                        directory.rmdir()
                    except OSError:
                        pass
                if pending_signals and not isinstance(exc, _MigrationInterrupted):
                    exc = _MigrationInterrupted(pending_signals[0])
                detail = (
                    f"; rollback errors: {', '.join(rollback_errors)}"
                    if rollback_errors
                    else ""
                )
                raise SpeckitRuntimeError(
                    f"migration apply failed and was rolled back: {exc}{detail}"
                ) from exc

    return {
        **plan,
        "applied": True,
        "changed_paths": changed_paths,
        "preserved_paths": inventory["protected_preserved"],
    }


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
    for action in ("init", "migrate", "upgrade"):
        project_action = project_sub.add_parser(action)
        project_action.add_argument("repo", type=Path)
        project_action.add_argument("--apply", action="store_true")
        project_action.add_argument("--json", action="store_true")
        project_action.add_argument("--allow-multi-install-force", action="store_true")
        if action == "migrate":
            project_action.add_argument("--accept-generated-updates", action="store_true")
        else:
            project_action.set_defaults(accept_generated_updates=False)
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
                accept_generated_updates=args.accept_generated_updates,
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
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"Error: operating system failure: {exc}", file=sys.stderr)
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
        if output.get("migration"):
            migration = output["migration"]
            for label in (
                "additions",
                "generated_updates",
                "protected_preserved",
                "blocking_collisions",
                "ignored_generated_paths",
                "legacy_commands_preserved",
            ):
                print(f"{label}={','.join(migration[label]) or '-'}")
            constitution_moves = migration["legacy_constitution_migrations"]
            print(
                "legacy_constitution_migrations="
                + (
                    ",".join(
                        f"{source}->{target}"
                        for target, source in sorted(constitution_moves.items())
                    )
                    or "-"
                )
            )
            print(f"ready_to_apply={'yes' if output['ready_to_apply'] else 'no'}")
    elif args.command == "context":
        print(_render_context(output))
    else:
        print(_render_status(status))
    return 0 if aligned else 1


if __name__ == "__main__":
    raise SystemExit(main())
