#!/usr/bin/env python3
"""Open a GobabyGo operator layout in iTerm2 using the iTerm2 Python API.

Default roles:
  boss, president, lead, worker-gemini, verifier

Each pane runs `wss <repo>` by default, so the shell lands on WS in target repo.
You can override per-role boot commands with env vars:
  MESH_UI_CMD_BOSS
  MESH_UI_CMD_PRESIDENT
  MESH_UI_CMD_LEAD
  MESH_UI_CMD_WORKER_CLAUDE
  ...
Templates support {repo} and {repo_name}.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import plistlib
import re
import shlex
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import yaml


FALLBACK_DEFAULT_ROLES = [
    "boss",
    "president",
    "lead",
    "worker-gemini",
    "verifier",
]


@dataclass(frozen=True)
class UiConfig:
    repo: str
    repo_name: str
    roles: list[str]
    max_panes_per_tab: int
    single_tab: bool
    replace_tabs: bool
    preset: str
    attach_live: bool
    ui_group_id: str = ""


@dataclass(frozen=True)
class RoleLaunchPlan:
    role: str
    mode: str
    remote_init: str = ""
    session_id: str = ""
    task_id: str = ""
    cli_type: str = ""
    error: str = ""


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _default_ui_config_path() -> str:
    return str(_repo_root() / "mapping" / "operator_ui.yaml")


def _configured_default_roles(config_path: str | None = None) -> list[str]:
    path_value = config_path
    if path_value is None:
        path_value = os.environ.get("MESH_UI_CONFIG") or _default_ui_config_path()
    if not path_value:
        return list(FALLBACK_DEFAULT_ROLES)

    path = Path(path_value)
    if not path.is_file():
        return list(FALLBACK_DEFAULT_ROLES)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return list(FALLBACK_DEFAULT_ROLES)

    value = raw.get("default_roles")
    if not isinstance(value, list):
        return list(FALLBACK_DEFAULT_ROLES)
    roles = [str(item).strip() for item in value if str(item).strip()]
    return roles or list(FALLBACK_DEFAULT_ROLES)


DEFAULT_ROLES = _configured_default_roles()
_ITERM2_CLEAR_SCROLLBACK = b"\x1b]1337;ClearScrollback\x07"
_ANSI_CLEAR_SCREEN = b"\x1b[3J\x1b[H\x1b[2J"


def _default_provider_runtime_config_path() -> str:
    override = os.environ.get("MESH_PROVIDER_RUNTIME_CONFIG")
    if override is not None:
        return override
    return str(_repo_root() / "mapping" / "provider_runtime.yaml")


def _ui_group_cache_dir() -> Path:
    override = os.environ.get("MESH_UI_GROUP_CACHE_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".mesh" / "ui_groups"


def _iterm2_version_string() -> str:
    info_path = Path("/Applications/iTerm.app/Contents/Info.plist")
    if not info_path.is_file():
        return ""
    try:
        payload = plistlib.loads(info_path.read_bytes())
    except Exception:
        return ""
    return str(payload.get("CFBundleShortVersionString") or "").strip()


def _should_avoid_split_panes(version: str | None = None) -> bool:
    override = os.environ.get("MESH_UI_TABS_ONLY", "").strip().lower()
    if override in {"1", "true", "yes", "on"}:
        return True
    if override in {"0", "false", "no", "off"}:
        return False
    resolved = version if version is not None else _iterm2_version_string()
    return resolved.startswith("3.6.9")


def _iterm_retry_enabled() -> bool:
    return str(os.environ.get("MESH_ITERM_RETRY", "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _cache_repo_path(repo_path: str) -> str:
    candidate = str(repo_path or "").strip()
    if not candidate:
        return ""
    return os.path.abspath(candidate)


def _ui_group_cache_path(repo_name: str, *, repo_path: str = "", cache_dir: Path | None = None) -> Path:
    directory = cache_dir or _ui_group_cache_dir()
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", repo_name).strip("-") or "repo"
    normalized_repo = _cache_repo_path(repo_path)
    if not normalized_repo:
        return directory / f"{safe_name}.json"
    digest = hashlib.sha256(normalized_repo.encode("utf-8")).hexdigest()[:12]
    return directory / f"{safe_name}-{digest}.json"


def _read_ui_group_cache(
    repo_name: str,
    *,
    repo_path: str = "",
    cache_dir: Path | None = None,
) -> dict[str, str] | None:
    normalized_repo = _cache_repo_path(repo_path)
    path = _ui_group_cache_path(repo_name, repo_path=normalized_repo, cache_dir=cache_dir)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    cached_repo = str(payload.get("repo_name", "")).strip()
    ui_group_id = str(payload.get("ui_group_id", "")).strip()
    cached_repo_path = _cache_repo_path(str(payload.get("repo_path", "")).strip())
    if cached_repo != repo_name or not ui_group_id:
        return None
    if normalized_repo and cached_repo_path != normalized_repo:
        return None
    result = {"repo_name": cached_repo, "ui_group_id": ui_group_id}
    if cached_repo_path:
        result["repo_path"] = cached_repo_path
    return result


def _write_ui_group_cache(
    repo_name: str,
    ui_group_id: str,
    *,
    repo_path: str = "",
    cache_dir: Path | None = None,
) -> Path:
    normalized_repo = _cache_repo_path(repo_path)
    path = _ui_group_cache_path(repo_name, repo_path=normalized_repo, cache_dir=cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "repo_name": repo_name,
        "ui_group_id": ui_group_id,
    }
    if normalized_repo:
        payload["repo_path"] = normalized_repo
    path.write_text(
        json.dumps(payload, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def _clear_ui_group_cache(repo_name: str, *, repo_path: str = "", cache_dir: Path | None = None) -> None:
    path = _ui_group_cache_path(repo_name, repo_path=repo_path, cache_dir=cache_dir)
    try:
        path.unlink()
    except FileNotFoundError:
        return


def _generate_ui_group_id(repo_name: str, *, timestamp: str | None = None) -> str:
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", repo_name).strip("-") or "repo"
    stamp = timestamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{safe_name}-ui-{stamp}"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Open iTerm2 mesh operator layout.")
    parser.add_argument(
        "repo",
        nargs="?",
        default="",
        help="Repo name or path. Default: basename of current directory.",
    )
    parser.add_argument(
        "--roles",
        default=os.environ.get("MESH_UI_ROLES", ",".join(DEFAULT_ROLES)),
        help="Comma-separated role list.",
    )
    parser.add_argument(
        "--max-panes-per-tab",
        type=int,
        default=int(os.environ.get("MESH_UI_MAX_PANES_PER_TAB", "3")),
        help="Maximum panes per tab (default: 3).",
    )
    parser.add_argument(
        "--single-tab",
        action="store_true",
        help="Put all roles in one tab (ignores max-panes-per-tab).",
    )
    parser.add_argument(
        "--keep-existing",
        action="store_true",
        help="Keep previous mesh-ui tabs instead of replacing them.",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Deprecated alias; fresh launch is now the default.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Reuse the cached live UI group instead of starting fresh.",
    )
    parser.add_argument(
        "--no-attach-live",
        action="store_true",
        help="Open static role shells only; do not auto-attach live tmux sessions.",
    )
    parser.add_argument(
        "--preset",
        choices=["team-4x3", "auto"],
        default=os.environ.get("MESH_UI_PRESET", "auto"),
        help="Layout preset. auto = chunk by max panes (default); team-4x3 = legacy 2 tabs (4 panes + 3 panes).",
    )
    return parser.parse_args()


def _repo_root_path(root: str) -> str:
    target = os.path.abspath(root)
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=target,
            capture_output=True,
            text=True,
            check=True,
        )
        repo_path = proc.stdout.strip()
        if repo_path:
            return repo_path
    except (OSError, subprocess.CalledProcessError):
        pass
    return target


def _ws_repo_base() -> str:
    return os.environ.get("MESH_WS_REPO_BASE", "/media/sam/1TB").strip() or "/media/sam/1TB"


def _resolve_repo(repo_arg: str) -> tuple[str, str]:
    if repo_arg:
        if "/" in repo_arg or repo_arg.startswith("."):
            repo_path = _repo_root_path(repo_arg)
            repo_name = os.path.basename(repo_path.rstrip("/"))
            return repo_path, repo_name
        return os.path.join(_ws_repo_base(), repo_arg), repo_arg
    cwd = _repo_root_path(os.getcwd())
    return cwd, os.path.basename(cwd)


def _split_groups(items: list[str], size: int) -> list[list[str]]:
    if not items:
        return []
    if size <= 0:
        size = 5
    return [items[i : i + size] for i in range(0, len(items), size)]


def _team_4x3_groups(roles: list[str]) -> list[list[str]]:
    if not roles:
        return []
    first = roles[:4]
    second = roles[4:7]
    tail = roles[7:]
    groups: list[list[str]] = []
    if first:
        groups.append(first)
    if second:
        groups.append(second)
    if tail:
        groups.extend(_split_groups(tail, 4))
    return groups


def _role_env_key(role: str) -> str:
    return "MESH_UI_CMD_" + role.upper().replace("-", "_")


def _role_bootstrap_env_key(role: str) -> str:
    return "MESH_UI_BOOTSTRAP_PROMPT_" + role.upper().replace("-", "_")


def _extract_env_value(text: str, key: str) -> str:
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if not line.startswith(f"{key}="):
            continue
        value = line.split("=", 1)[1].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        return value
    return ""


def _load_router_env() -> tuple[str, str]:
    router_url = os.environ.get("MESH_ROUTER_URL", "").strip()
    auth_token = os.environ.get("MESH_AUTH_TOKEN", "").strip()
    if router_url and auth_token:
        return router_url, auth_token

    candidates = _router_env_candidate_paths()
    for path in candidates:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if not router_url:
            router_url = _extract_env_value(text, "MESH_ROUTER_URL")
        if not auth_token:
            auth_token = _extract_env_value(text, "MESH_AUTH_TOKEN")
        if router_url and auth_token:
            return router_url, auth_token
    return "", ""


def _router_env_candidate_paths(home: Path | None = None) -> list[Path]:
    home_dir = home or Path.home()
    return [
        Path("/etc/mesh-worker/common.env"),
        home_dir / ".mesh" / "router.env",
        home_dir / ".mesh" / ".env.mesh",
    ]


def _router_get_json(router_url: str, auth_token: str, path: str) -> Any:
    req = Request(router_url.rstrip("/") + path)
    req.add_header("Authorization", f"Bearer {auth_token}")
    with urlopen(req, timeout=30) as resp:
        return json.load(resp)


def _router_post_json(router_url: str, auth_token: str, path: str, payload: dict[str, Any]) -> Any:
    req = Request(
        router_url.rstrip("/") + path,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
    )
    req.add_header("Authorization", f"Bearer {auth_token}")
    req.add_header("Content-Type", "application/json")
    with urlopen(req, timeout=30) as resp:
        return json.load(resp)


def _router_has_live_ui_group(router_url: str, auth_token: str, ui_group_id: str) -> bool | None:
    if not router_url or not auth_token or not ui_group_id:
        return False
    try:
        payload = _router_get_json(router_url, auth_token, "/sessions?state=open&limit=200")
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None
    sessions = payload.get("sessions") if isinstance(payload, dict) else None
    if not isinstance(sessions, list):
        return False
    for session in sessions:
        if not isinstance(session, dict):
            continue
        metadata = session.get("metadata") if isinstance(session.get("metadata"), dict) else {}
        if str(metadata.get("ui_group_id", "")).strip() == ui_group_id:
            return True
    return False


def _discover_live_remote_inits(cfg: UiConfig) -> dict[str, str]:
    """Helper for the WS-side live attach resolution."""
    router_url, auth_token = _load_router_env()
    if not router_url or not auth_token:
        return {}
    session_pairs = _fetch_live_session_pairs(router_url, auth_token)
    plans = _build_role_launch_plans(cfg, session_pairs)
    return {
        role: plan.remote_init
        for role, plan in plans.items()
        if plan.mode == "attach" and plan.remote_init
    }


def _resolve_active_ui_group_id(
    repo_name: str,
    *,
    repo_path: str = "",
    router_url: str = "",
    auth_token: str = "",
    cache_dir: Path | None = None,
    timestamp: str | None = None,
    fresh: bool = False,
) -> str:
    if fresh:
        ui_group_id = _generate_ui_group_id(repo_name, timestamp=timestamp)
        _write_ui_group_cache(repo_name, ui_group_id, repo_path=repo_path, cache_dir=cache_dir)
        return ui_group_id

    cached = _read_ui_group_cache(repo_name, repo_path=repo_path, cache_dir=cache_dir)
    cached_group = str((cached or {}).get("ui_group_id", "")).strip()
    if cached_group:
        live_group = _router_has_live_ui_group(router_url, auth_token, cached_group)
        if live_group is True:
            return cached_group

    ui_group_id = _generate_ui_group_id(repo_name, timestamp=timestamp)
    _write_ui_group_cache(repo_name, ui_group_id, repo_path=repo_path, cache_dir=cache_dir)
    return ui_group_id


def _load_provider_session_users(config_path: str | None = None) -> dict[str, str]:
    path_value = config_path if config_path is not None else _default_provider_runtime_config_path()
    if path_value == "":
        return {}
    path = Path(path_value)
    if not path.is_file():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}

    providers = raw.get("providers")
    if not isinstance(providers, dict):
        return {}

    users: dict[str, str] = {}
    for cli_type, entry in providers.items():
        if not isinstance(entry, dict):
            continue
        user = str(entry.get("session_service_user", "")).strip()
        if user:
            users[str(cli_type).strip()] = user
    return users


def _load_provider_runtime(config_path: str | None = None) -> dict[str, dict[str, str]]:
    path_value = config_path if config_path is not None else _default_provider_runtime_config_path()
    if path_value == "":
        return {}
    path = Path(path_value)
    if not path.is_file():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}

    providers = raw.get("providers")
    if not isinstance(providers, dict):
        return {}

    result: dict[str, dict[str, str]] = {}
    for provider, entry in providers.items():
        if not isinstance(entry, dict):
            continue
        result[str(provider).strip()] = {
            str(key).strip(): str(value).strip()
            for key, value in entry.items()
            if str(key).strip() and str(value).strip()
        }
    return result


def _load_ui_role_rules(config_path: str | None = None) -> dict[str, dict[str, object]]:
    path_value = config_path
    if path_value is None:
        path_value = os.environ.get("MESH_UI_CONFIG") or _default_ui_config_path()
    if path_value == "":
        return {}

    path = Path(path_value)
    if not path.is_file():
        return {}

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}

    roles = raw.get("roles")
    if not isinstance(roles, dict):
        return {}

    def _normalize_rule_value(value: object) -> object | None:
        if isinstance(value, dict):
            nested: dict[str, object] = {}
            for nested_key, nested_value in value.items():
                key_text = str(nested_key).strip()
                if not key_text:
                    continue
                normalized = _normalize_rule_value(nested_value)
                if normalized is not None:
                    nested[key_text] = normalized
            return nested or None
        if isinstance(value, list):
            items = [str(item).strip() for item in value if str(item).strip()]
            return items or None
        value_text = str(value).strip()
        return value_text or None

    result: dict[str, dict[str, object]] = {}
    for role, entry in roles.items():
        if not isinstance(entry, dict):
            continue
        normalized: dict[str, object] = {}
        for key, value in entry.items():
            key_text = str(key).strip()
            if not key_text:
                continue
            normalized_value = _normalize_rule_value(value)
            if normalized_value is not None:
                normalized[key_text] = normalized_value
        result[str(role).strip()] = normalized
    return result


def _default_remote_init_for_role(role: str) -> str:
    defaults = {
        "boss": "ccs gemini",
        "president": "ccs gemini",
        "lead": "ccs gemini",
        "worker-claude": "ccs work-claude",
        "worker-codex": "ccs codex",
        "worker-gemini": "ccs gemini",
        "verifier": "ccs gemini",
    }
    return defaults.get(role, "")


def _is_agent_role(role: str) -> bool:
    return True


def _resolved_provider_for_role(role: str, rule: dict[str, str]) -> str:
    provider = os.environ.get("MESH_UI_PROVIDER_OVERRIDE", "").strip() or rule.get("provider", "").strip()
    if not provider and role.startswith("worker-"):
        provider = role.split("-", 1)[1].strip()
    return provider


def _default_target_account_for_provider(provider: str) -> str:
    if provider == "claude":
        return "work-claude"
    return provider


def _read_text_if_exists(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _has_exact_repo_entry(repo_path: Path, name: str) -> bool:
    try:
        return name in {entry.name for entry in repo_path.iterdir()}
    except OSError:
        return False


def _infer_workflow_context(repo: str) -> dict[str, str]:
    repo_path = Path(repo).expanduser()
    if not repo_path.exists():
        return {
            "framework": "generic",
            "phase": "implement",
            "summary": "Framework unknown; default to generic implementation orchestration.",
        }

    speckit_spec = repo_path / "spec.md"
    speckit_plan = repo_path / "plan.md"
    speckit_tasks = repo_path / "tasks.md"
    gsd_roadmap = repo_path / "ROADMAP.md"
    gsd_planning_roadmap = repo_path / ".planning" / "ROADMAP.md"
    gsd_research = repo_path / "RESEARCH.md"
    gsd_context = repo_path / "CONTEXT.md"
    gsd_plan = repo_path / "PLAN.md"

    has_speckit_spec = _has_exact_repo_entry(repo_path, "spec.md")
    has_speckit_plan = _has_exact_repo_entry(repo_path, "plan.md")
    has_speckit_tasks = _has_exact_repo_entry(repo_path, "tasks.md")
    has_gsd_roadmap = _has_exact_repo_entry(repo_path, "ROADMAP.md")
    has_gsd_research = _has_exact_repo_entry(repo_path, "RESEARCH.md")
    has_gsd_context = _has_exact_repo_entry(repo_path, "CONTEXT.md")
    has_gsd_plan = _has_exact_repo_entry(repo_path, "PLAN.md")
    has_gsd_planning_roadmap = _has_exact_repo_entry(repo_path / ".planning", "ROADMAP.md")

    has_speckit_markers = any((has_speckit_spec, has_speckit_plan, has_speckit_tasks))
    has_gsd_markers = any((has_gsd_roadmap, has_gsd_planning_roadmap, has_gsd_research, has_gsd_context, has_gsd_plan))

    if has_speckit_markers and not has_gsd_markers:
        phase = "specify"
        summary = "Speckit repo with no spec artifacts yet."
        if has_speckit_spec:
            phase = "clarify"
            summary = "Speckit spec exists; tighten ambiguities before planning."
            spec_text = _read_text_if_exists(speckit_spec)
            if "[NEEDS CLARIFICATION]" not in spec_text:
                phase = "plan"
                summary = "Speckit spec looks clarified; plan next."
        if has_speckit_plan:
            phase = "tasks"
            summary = "Speckit plan exists; generate or refine tasks next."
        if has_speckit_tasks:
            phase = "implement"
            summary = "Speckit tasks exist; implementation and verification are next."
        return {
            "framework": "speckit",
            "phase": phase,
            "summary": summary,
        }

    if has_gsd_markers:
        phase = "research"
        summary = "GSD repo; start by collecting research evidence."
        if has_gsd_research:
            phase = "discuss"
            summary = "GSD research exists; discuss context and assumptions next."
        if has_gsd_context:
            phase = "plan"
            summary = "GSD context exists; planning is the next checkpoint."
        if has_gsd_plan:
            phase = "execute"
            summary = "GSD plan exists; execution and verification are next."
        return {
            "framework": "gsd",
            "phase": phase,
            "summary": summary,
        }

    return {
        "framework": "generic",
        "phase": "implement",
        "summary": "Framework unknown; default to generic implementation orchestration.",
    }


def _workflow_policy_text(cfg: UiConfig) -> str:
    context = _infer_workflow_context(cfg.repo)
    framework = context["framework"]
    phase = context["phase"]
    summary = context["summary"]
    if framework == "speckit":
        return (
            f"Current inferred workflow is Speckit, phase {phase}. {summary} "
            "Use the normal Speckit order: specify, clarify, plan, tasks, analyze, implement, verify."
        )
    if framework == "gsd":
        return (
            f"Current inferred workflow is GSD, phase {phase}. {summary} "
            "Use the normal GSD order: research, discuss, plan, execute, verify."
        )
    return (
        f"Current inferred workflow is generic, default phase {phase}. {summary} "
        "Infer the exact phase from the operator request and existing repo artifacts."
    )


def _absolute_mesh_script_for_prompts() -> str:
    control_repo = os.environ.get("MESH_CONTROL_REPO", "").strip()
    if control_repo:
        return f"{control_repo.rstrip('/')}/scripts/mesh"
    ws_repo_base = os.environ.get("MESH_WS_REPO_BASE", "/media/sam/1TB").strip() or "/media/sam/1TB"
    return f"{ws_repo_base.rstrip('/')}/gobabygo/scripts/mesh"


def _ui_role_bootstrap_prompt(cfg: UiConfig, role: str, target_cli: str) -> str:
    env_key = _role_bootstrap_env_key(role)
    explicit = os.environ.get(env_key, "").strip() or os.environ.get("MESH_UI_BOOTSTRAP_PROMPT", "").strip()
    if explicit:
        return explicit.format(
            repo=cfg.repo,
            repo_name=cfg.repo_name,
            role=role,
            target_cli=target_cli,
        )
    workflow_policy = _workflow_policy_text(cfg)
    mesh_script = _absolute_mesh_script_for_prompts()
    boss_president_only = len(cfg.roles) == 2 and set(cfg.roles) == {"boss", "president"}
    if role == "boss":
        if boss_president_only:
            return (
                f"You are boss for repository {cfg.repo_name} at {cfg.repo}. "
                "You are the primary AI interface for the human operator. "
                "President is your only live peer in this test group. "
                f"{workflow_policy} "
                "The operator should talk only to you. Do not tell the operator to run mesh commands or manually message other panes. "
                "The runtime may auto-relay your response summary to president after you answer; assume that relay path exists. "
                "Do not enter planning mode before you have given a concise answer suitable for president to coordinate from when delegation is possible. "
                "Do not inspect files, run implementation steps, or start solving the task yourself before president has enough context to act, unless the operator explicitly asks for boss-only analysis. "
                "Keep operator-facing updates concise. Only interrupt the operator for confirmations, blocking ambiguity, or corrections. "
                "Stay in this interactive session, answer the operator directly, and delegate through president by default. Do not exit."
            )
        return (
            f"You are boss for repository {cfg.repo_name} at {cfg.repo}. "
            "You are the primary AI interface for the human operator. "
            "President is your execution coordinator and your live peers include president, lead, worker-gemini, and verifier. "
            f"{workflow_policy} "
            "The operator should talk only to you. Do not tell the operator to run mesh commands or manually message other panes. "
            "The runtime may auto-relay your response summary to president after you answer; assume that relay path exists. "
            "Do not enter planning mode before you have given a concise answer suitable for delegation when delegation is possible. "
            "Do not inspect files, run implementation steps, or start solving the task yourself before president has enough context to act, unless the operator explicitly asks for boss-only analysis. "
            "If the operator asks you to message or notify president, do that immediately instead of explaining your limitations. "
            "Keep operator-facing updates concise. Only interrupt the operator for confirmations, blocking ambiguity, or corrections. "
            "Stay in this interactive session, answer the operator directly, and delegate through the mesh hierarchy by default. Do not exit."
        )
    if role == "president":
        if boss_president_only:
            return (
                f"You are president for repository {cfg.repo_name} at {cfg.repo}. "
                "Boss is your only live peer in this test group. "
                f"{workflow_policy} "
                "Do not mention lead, workers, or verifier. They are not active in this test group. "
                "The runtime may auto-relay your response summary back to boss after each reply; assume that return path exists. "
                "When you need an explicit manual message to boss, reply back through the mesh bus. "
                "Always use the absolute repo command "
                f"`{mesh_script} send boss \"<message>\"` "
                "instead of relying on `mesh` being in PATH. "
                "Do not inspect files, create plans, or start implementation unless boss explicitly asks you to do that. "
                "Keep replies concise and stay in this interactive session. Do not exit."
            )
        return (
            f"You are president for repository {cfg.repo_name} at {cfg.repo}. "
            "Your live peers in this UI group are lead, worker-gemini, and verifier. "
            f"{workflow_policy} "
            "You are the autonomous execution coordinator. When boss sends work, infer the current framework and phase from the request and repo artifacts, then delegate execution ownership to lead by default. "
            "Use lead as the delivery owner for the repo. Lead is responsible for deciding whether worker-gemini and verifier are needed, coordinating them, and consolidating their outputs. "
            "Talk directly to worker-gemini or verifier only if lead is blocked, absent, or you are explicitly handling an exception path. "
            "Coordinate through the repo command "
            f"`mesh send <role> \"<message>\"` from {cfg.repo}. "
            "If a subprocess cannot find `mesh`, use the absolute command "
            f"`{mesh_script} send <role> \"<message>\"` instead. "
            "You may talk to lead, worker-gemini, verifier, and boss through the mesh hierarchy. "
            "Do not wait for operator instructions once the request is clear. Expect status and completion reports back from lead, escalate only for approvals or hard blockers, and report concise status back to boss. Do not exit."
        )
    if role == "lead":
        return (
            f"You are lead for repository {cfg.repo_name} at {cfg.repo}. "
            "You are the delivery lead for the current mesh UI group. "
            f"{workflow_policy} "
            "President is your coordinator, but you own execution inside the repo once work is delegated to you. "
            "Do not default to implementing everything yourself. First decide whether worker-gemini and verifier are needed. "
            "Worker-gemini is your implementation or parallel-analysis subordinate. Verifier is your validation and risk-review subordinate. "
            "You are responsible for coordinating them, keeping them scoped, and controlling any sandbox or repo-level constraints they must respect. "
            "Only do the implementation directly yourself when the task is small enough that delegation would slow the flow down, or when president explicitly asks for lead-only execution. "
            "Use `mesh send <role> \"<message>\"` from "
            f"{cfg.repo} "
            "to coordinate subordinate roles. If a subprocess cannot find `mesh`, use the absolute command "
            f"`{mesh_script} send <role> \"<message>\"` instead. "
            "Send concise progress and completion updates back to president. When your task is complete, report summary, artifacts, and commit hash if you made one. Do not exit."
        )
    return (
        f"You are {role} for repository {cfg.repo_name} at {cfg.repo}. "
        "You are part of the current mesh UI group. "
        f"{workflow_policy} "
        "Lead is your coordinator for execution work in this repo. If asked to communicate with another live role, use "
        f"`mesh send <role> \"<message>\"` from {cfg.repo}. "
        "If a subprocess cannot find `mesh`, use the absolute command "
        f"`{mesh_script} send <role> \"<message>\"` instead, and stay within the mesh hierarchy. "
        "Acknowledge readiness briefly when you first come up. "
        "Act immediately on a clear assignment from lead. When your task is complete, keep the final report concise: summary, artifacts, and commit hash if you made one, and return it to lead. "
        "Escalate only for blockers or missing approvals, then remain in this interactive session for follow-up. Do not exit."
    )


def _resolve_role_task_target(role: str) -> tuple[str, str]:
    rules = _load_ui_role_rules()
    rule = rules.get(role, {})
    provider = _resolved_provider_for_role(role, rule)
    if not provider:
        provider = "gemini"
    target_account = rule.get("target_account", "").strip() or _default_target_account_for_provider(provider)
    return provider, target_account


def _role_cli_args(role: str) -> list[str]:
    rules = _load_ui_role_rules()
    rule = rules.get(role, {})
    args: list[str] = []
    max_turns = str(rule.get("max_turns", "")).strip()
    if max_turns:
        args.extend(["--max-turns", max_turns])
    extra_args = rule.get("extra_args")
    if isinstance(extra_args, list):
        args.extend(str(item).strip() for item in extra_args if str(item).strip())
    elif isinstance(extra_args, str) and extra_args.strip():
        args.append(extra_args.strip())
    return args


def _role_relay_config(role: str) -> dict[str, object]:
    rules = _load_ui_role_rules()
    rule = rules.get(role, {})
    relay = rule.get("relay")
    if not isinstance(relay, dict):
        return {}

    enabled = str(relay.get("enabled", "")).strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return {}

    target_role = str(relay.get("target_role", "")).strip()
    if not target_role:
        return {}

    config: dict[str, object] = {
        "enabled": True,
        "mode": str(relay.get("mode", "prompt_submit")).strip() or "prompt_submit",
        "target_role": target_role,
    }
    if str(relay.get("ignore_slash_commands", "")).strip().lower() in {"1", "true", "yes", "on"}:
        config["ignore_slash_commands"] = True
    message_prefix = str(relay.get("message_prefix", "")).strip()
    if message_prefix:
        config["message_prefix"] = message_prefix
    passthrough_to_child = str(relay.get("passthrough_to_child", "")).strip().lower()
    if passthrough_to_child in {"0", "false", "no", "off"}:
        config["passthrough_to_child"] = False
    local_ack = str(relay.get("local_ack", "")).strip()
    if local_ack:
        config["local_ack"] = local_ack
    return config


def _provider_remote_init_for_role(role: str, rule: dict[str, str]) -> str:
    provider = os.environ.get("MESH_UI_PROVIDER_OVERRIDE", "").strip() or rule.get("provider", "").strip()
    if not provider and role.startswith("worker-"):
        provider = role.split("-", 1)[1].strip()
    if not provider:
        return ""

    runtime = _load_provider_runtime()
    provider_cfg = runtime.get(provider, {})
    template = provider_cfg.get("command_template", "").strip()
    if not template:
        return ""

    target_account = rule.get("target_account", "").strip()
    if not target_account:
        if provider == "claude":
            target_account = "work-claude"
        else:
            target_account = provider

    command = template.format(
        target_account=target_account,
        account_profile=target_account,
        worker_account_profile=target_account,
    ).strip()
    if not command:
        return ""
    return command


def _default_command_for_role(role: str, repo: str, repo_name: str) -> str:
    helper = _repo_root() / "scripts" / "mesh_ui_role_shell.sh"
    remote_init = _default_remote_init_for_role(role)
    return " ".join(
        [
            shlex.quote(str(helper)),
            shlex.quote(role),
            shlex.quote(repo),
            shlex.quote(repo_name),
            shlex.quote(remote_init),
        ]
    )


def _build_tmux_attach_remote_init(role: str, session: dict[str, Any], task: dict[str, Any]) -> str:
    meta = session.get("metadata") or {}
    tmux_session = str(meta.get("tmux_session", "")).strip()
    if not tmux_session:
        return ""

    cli_type = str(session.get("cli_type", "")).strip()
    users = _load_provider_session_users()
    user = users.get(cli_type, "").strip()
    attach_cmd = f"tmux attach -t {shlex.quote(tmux_session)}"
    if user and user != "sam":
        attach_cmd = f"sudo -u {shlex.quote(user)} {attach_cmd}"

    title = str(task.get("title", "")).strip() or str(meta.get("task_title", "")).strip()
    banner = f"[mesh:{role}] attaching live session {tmux_session}"
    if title:
        banner += f" :: {title}"
    return (
        f"printf '%s\\n' {shlex.quote(banner)}; "
        f"{attach_cmd} || printf '[mesh:{role}] attach failed for %s\\n' {shlex.quote(tmux_session)}"
    )


def _session_metadata(session: dict[str, Any]) -> dict[str, Any]:
    return session.get("metadata") if isinstance(session.get("metadata"), dict) else {}


def _session_group_id(
    session: dict[str, Any],
    task: dict[str, Any],
) -> str:
    meta = _session_metadata(session)
    payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
    return str(meta.get("ui_group_id") or payload.get("ui_group_id") or "").strip()


def _session_repo(
    session: dict[str, Any],
    task: dict[str, Any],
) -> str:
    meta = _session_metadata(session)
    return str(meta.get("repo") or task.get("repo") or meta.get("working_dir") or "").strip()


def _session_role(
    session: dict[str, Any],
    task: dict[str, Any],
) -> str:
    meta = _session_metadata(session)
    return str(meta.get("ui_role") or task.get("role") or meta.get("role") or "").strip()


def _session_matches_repo(
    repo: str,
    repo_name: str,
    session: dict[str, Any],
    task: dict[str, Any],
) -> bool:
    candidates = [value for value in (_session_repo(session, task),) if value]
    if repo in candidates:
        return True
    for value in candidates:
        if os.path.basename(value.rstrip("/")) == repo_name:
            return True
    return False


def _session_matches_ui_group(ui_group_id: str, session: dict[str, Any], task: dict[str, Any]) -> bool:
    if not ui_group_id:
        return True
    return _session_group_id(session, task) == ui_group_id


def _role_session_score(role: str, session: dict[str, Any], task: dict[str, Any]) -> int:
    cli_type = str(session.get("cli_type", "") or task.get("target_cli", "")).strip()
    task_role = _session_role(session, task)
    task_status = str(task.get("status", "") or "").strip()

    if role == "boss":
        return 300 if task_role == "boss" else -1
    if role == "president":
        return 300 if task_role == "president" else -1
    if role == "lead":
        return 300 if task_role == "lead" else -1
    if role == "verifier":
        if task_role in {"verifier", "reviewer"}:
            return 300
        if task_status == "review":
            return 250
        return -1
    if role.startswith("worker-"):
        provider = role.split("-", 1)[1]
        if task_role == role:
            return 325
        if cli_type != provider:
            return -1
        if task_role == "worker":
            return 300
        if not task_role:
            return 220
        return 150
    return -1


def _sort_key_for_session_pair(session: dict[str, Any], task: dict[str, Any]) -> tuple[str, str]:
    return (
        str(task.get("updated_at", "") or session.get("updated_at", "")),
        str(session.get("created_at", "")),
    )


def _select_live_sessions_for_roles(
    roles: list[str],
    repo: str,
    repo_name: str,
    ui_group_id: str,
    session_pairs: list[tuple[dict[str, Any], dict[str, Any]]],
) -> dict[str, tuple[dict[str, Any], dict[str, Any]]]:
    available = [
        (session, task)
        for session, task in session_pairs
        if _session_matches_ui_group(ui_group_id, session, task) and _session_matches_repo(repo, repo_name, session, task)
    ]
    selected: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    used_session_ids: set[str] = set()

    for role in roles:
        best_pair: tuple[dict[str, Any], dict[str, Any]] | None = None
        best_score = -1
        for session, task in available:
            session_id = str(session.get("session_id", ""))
            if session_id in used_session_ids:
                continue
            score = _role_session_score(role, session, task)
            if score < 0:
                continue
            if best_pair is None or score > best_score or (
                score == best_score and _sort_key_for_session_pair(session, task) > _sort_key_for_session_pair(*best_pair)
            ):
                best_pair = (session, task)
                best_score = score
        if best_pair is not None:
            selected[role] = best_pair
            used_session_ids.add(str(best_pair[0].get("session_id", "")))
    return selected


def _fetch_live_session_pairs(router_url: str, auth_token: str) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    if not router_url or not auth_token:
        return []
    try:
        sessions_payload = _router_get_json(router_url, auth_token, "/sessions?state=open&limit=200")
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
        return []

    sessions = sessions_payload.get("sessions") if isinstance(sessions_payload, dict) else None
    if not isinstance(sessions, list):
        return []

    session_pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    task_cache: dict[str, dict[str, Any]] = {}
    for session in sessions:
        if not isinstance(session, dict):
            continue
        # Optimization: only fetch tasks for sessions that look like UI roles.
        # This avoids sequential GETs for unrelated sessions.
        meta = _session_metadata(session)
        if not meta.get("ui_group_id") and not meta.get("ui_role"):
            continue

        task_id = str(session.get("task_id", "")).strip()
        task: dict[str, Any] = {}
        if task_id:
            task = task_cache.get(task_id) or {}
            if not task:
                try:
                    task_payload = _router_get_json(router_url, auth_token, f"/tasks/{quote(task_id)}")
                except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
                    task_payload = None
                if isinstance(task_payload, dict):
                    task = task_payload
                    task_cache[task_id] = task
        if task_id and not task:
            task = {"task_id": task_id}
        session_pairs.append((session, task))
    return session_pairs


def _fallback_task_from_session(task_id: str, session: dict[str, Any]) -> dict[str, Any]:
    meta = _session_metadata(session)
    return {
        "task_id": task_id,
        "repo": str(meta.get("repo") or meta.get("working_dir") or "").strip(),
        "role": str(meta.get("ui_role") or meta.get("role") or "").strip(),
        "target_cli": str(session.get("cli_type", "")).strip(),
        "status": "running",
        "title": str(meta.get("task_title", "")).strip(),
        "payload": {
            "ui_group_id": str(meta.get("ui_group_id") or "").strip(),
            "ui_role": str(meta.get("ui_role") or meta.get("role") or "").strip(),
        },
    }


def _find_open_session_for_task(router_url: str, auth_token: str, task_id: str) -> dict[str, Any] | None:
    if not router_url or not auth_token or not task_id:
        return None
    try:
        sessions_payload = _router_get_json(router_url, auth_token, "/sessions?state=open&limit=200")
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None

    sessions = sessions_payload.get("sessions") if isinstance(sessions_payload, dict) else None
    if not isinstance(sessions, list):
        return None

    for session in sessions:
        if not isinstance(session, dict):
            continue
        if str(session.get("task_id", "")).strip() == task_id:
            return session
    return None


def _fetch_live_session_pair_for_task(
    router_url: str,
    auth_token: str,
    task_id: str,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    if not router_url or not auth_token or not task_id:
        return None
    try:
        task_payload = _router_get_json(router_url, auth_token, f"/tasks/{quote(task_id)}")
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
        task_payload = None
    if not isinstance(task_payload, dict):
        session_payload = _find_open_session_for_task(router_url, auth_token, task_id)
        if not isinstance(session_payload, dict):
            return None
        return session_payload, _fallback_task_from_session(task_id, session_payload)

    session_id = str(task_payload.get("session_id", "")).strip()
    if session_id:
        try:
            session_payload = _router_get_json(router_url, auth_token, f"/sessions/{quote(session_id)}")
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
            session_payload = None
        if isinstance(session_payload, dict):
            return session_payload, task_payload

    session_payload = _find_open_session_for_task(router_url, auth_token, task_id)
    if not isinstance(session_payload, dict):
        return None
    return session_payload, task_payload


def _build_role_launch_plan(
    role: str,
    pair: tuple[dict[str, Any], dict[str, Any]] | None,
) -> RoleLaunchPlan:
    if pair is None:
        return RoleLaunchPlan(role=role, mode="spawn")

    session, task = pair
    remote_init = _build_tmux_attach_remote_init(role, session, task)
    if not remote_init:
        return RoleLaunchPlan(role=role, mode="spawn")

    return RoleLaunchPlan(
        role=role,
        mode="attach",
        remote_init=remote_init,
        session_id=str(session.get("session_id", "")).strip(),
        task_id=str(task.get("task_id", "")).strip(),
        cli_type=str(session.get("cli_type", "") or task.get("target_cli", "")).strip(),
    )


def _build_role_launch_plans(
    cfg: UiConfig,
    session_pairs: list[tuple[dict[str, Any], dict[str, Any]]],
) -> dict[str, RoleLaunchPlan]:
    selected = _select_live_sessions_for_roles(
        cfg.roles,
        cfg.repo,
        cfg.repo_name,
        cfg.ui_group_id,
        session_pairs,
    )
    return {
        role: _build_role_launch_plan(role, selected.get(role))
        for role in cfg.roles
    }


def _spawn_error_remote_init(role: str, message: str) -> str:
    return (
        f"printf '%s\\n' {shlex.quote(f'[mesh:{role}] ERROR: {message}')}; "
        f"printf '%s\\n' {shlex.quote(f'[mesh:{role}] retry hint: mesh ui respawn {role}')}"
    )


def _ui_role_task_idempotency_key(cfg: UiConfig, role: str) -> str:
    return f"mesh-ui::{cfg.ui_group_id}::{role}::{time.time_ns()}"


def _task_payload(task: dict[str, Any]) -> dict[str, Any]:
    return task.get("payload") if isinstance(task.get("payload"), dict) else {}


def _task_matches_ui_role(cfg: UiConfig, role: str, task: dict[str, Any]) -> bool:
    payload = _task_payload(task)
    if not payload.get("ui_role_session"):
        return False
    task_repo = str(task.get("repo") or payload.get("working_dir") or "").strip()
    if task_repo != cfg.repo and os.path.basename(task_repo.rstrip("/")) != cfg.repo_name:
        return False
    if str(task.get("role") or payload.get("ui_role") or "").strip() != role:
        return False
    if str(payload.get("ui_group_id") or "").strip() != cfg.ui_group_id:
        return False
    return True


def _is_terminal_task_status(status: str) -> bool:
    return status in {"completed", "failed", "cancelled", "canceled"}


def _find_existing_ui_role_task(
    router_url: str,
    auth_token: str,
    cfg: UiConfig,
    role: str,
) -> dict[str, Any] | None:
    status_priority = {
        "running": 7,
        "assigned": 6,
        "review": 5,
        "blocked": 4,
        "queued": 3,
        "completed": 2,
        "failed": 1,
        "cancelled": 0,
        "canceled": 0,
    }
    # Optimize by fetching recent tasks once instead of per-status.
    try:
        payload = _router_get_json(router_url, auth_token, "/tasks?limit=300")
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None

    tasks = payload.get("tasks") if isinstance(payload, dict) else None
    if not isinstance(tasks, list):
        return None

    matches = [
        task
        for task in tasks
        if isinstance(task, dict) and _task_matches_ui_role(cfg, role, task)
    ]
    if not matches:
        return None
    matches.sort(
        key=lambda task: (
            status_priority.get(str(task.get("status", "")).strip(), 0),
            str(task.get("updated_at", "")),
            str(task.get("created_at", "")),
            str(task.get("task_id", "")),
        ),
        reverse=True,
    )
    return matches[0]


def _cancel_ui_role_task(router_url: str, auth_token: str, task_id: str) -> None:
    if not task_id:
        return
    try:
        _router_post_json(
            router_url,
            auth_token,
            "/tasks/cancel",
            {"task_id": task_id, "reason": "mesh_ui_spawn_timeout"},
        )
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
        return


def _create_ui_role_task(router_url: str, auth_token: str, cfg: UiConfig, role: str) -> dict[str, Any]:
    target_cli, target_account = _resolve_role_task_target(role)
    task_payload = {
        "ui_role_session": True,
        "ui_role": role,
        "ui_group_id": cfg.ui_group_id,
        "working_dir": cfg.repo,
    }
    cli_args = ["--session-id", str(uuid.uuid4()), *_role_cli_args(role)]
    if cli_args:
        task_payload["cli_args"] = cli_args
    relay = _role_relay_config(role)
    if relay:
        task_payload["relay"] = relay
    bootstrap_prompt = _ui_role_bootstrap_prompt(cfg, role, target_cli)
    if bootstrap_prompt:
        task_payload["prompt"] = bootstrap_prompt

    payload = {
        "title": f"mesh ui {role} {cfg.repo_name}",
        "repo": cfg.repo,
        "role": role,
        "target_cli": target_cli,
        "target_account": target_account,
        "execution_mode": "session",
        "payload": task_payload,
        "idempotency_key": _ui_role_task_idempotency_key(cfg, role),
    }
    try:
        created = _router_post_json(router_url, auth_token, "/tasks", payload)
    except HTTPError as exc:
        if exc.code != 409:
            raise
        existing = _find_existing_ui_role_task(router_url, auth_token, cfg, role)
        if existing is None:
            # We got a 409 but could not find the task. This can happen if the router
            # has a stale task that didn't match _task_matches_ui_role or synchronization delay.
            # Force a retry with a fresh idempotency key.
            payload["idempotency_key"] = _ui_role_task_idempotency_key(cfg, role)
            created = _router_post_json(router_url, auth_token, "/tasks", payload)
        elif _is_terminal_task_status(str(existing.get("status", "")).strip()):
            payload["idempotency_key"] = _ui_role_task_idempotency_key(cfg, role)
            created = _router_post_json(router_url, auth_token, "/tasks", payload)
        else:
            return {
                "role": role,
                "task_id": str(existing.get("task_id", "")).strip(),
                "target_cli": str(existing.get("target_cli", "")).strip() or target_cli,
                "created": False,
            }
    if not isinstance(created, dict):
        raise RuntimeError(f"invalid task creation response for role {role}")
    task_id = str(created.get("task_id", "")).strip()
    if not task_id:
        raise RuntimeError(f"missing task_id for role {role}")
    return {
        "role": role,
        "task_id": task_id,
        "target_cli": target_cli,
        "created": True,
    }


def _spawn_missing_agent_role_plans(
    cfg: UiConfig,
    existing_plans: dict[str, RoleLaunchPlan],
    *,
    router_url: str,
    auth_token: str,
    timeout_s: float = 180.0,
    poll_interval_s: float = 1.0,
) -> dict[str, RoleLaunchPlan]:
    pending: dict[str, dict[str, Any]] = {}
    if not router_url or not auth_token:
        for role in cfg.roles:
            if not _is_agent_role(role):
                continue
            current = existing_plans.get(role) or RoleLaunchPlan(role=role, mode="spawn")
            if current.mode == "attach":
                continue
            existing_plans[role] = RoleLaunchPlan(
                role=role,
                mode="error",
                remote_init=_spawn_error_remote_init(role, "router unavailable"),
                error="router unavailable",
            )
        return existing_plans

    for role in cfg.roles:
        if not _is_agent_role(role):
            continue
        current = existing_plans.get(role) or RoleLaunchPlan(role=role, mode="spawn")
        if current.mode == "attach":
            continue
        try:
            pending[role] = _create_ui_role_task(router_url, auth_token, cfg, role)
        except Exception as exc:
            existing_plans[role] = RoleLaunchPlan(
                role=role,
                mode="error",
                remote_init=_spawn_error_remote_init(role, f"spawn failed: {exc}"),
                error=str(exc),
            )

    if not pending:
        return existing_plans

    deadline = time.monotonic() + max(0.0, timeout_s)
    while pending and time.monotonic() < deadline:
        print(f"DEBUG: Waiting for {len(pending)} sessions to materialize... ({int(deadline - time.monotonic())}s left)")
        resolved: list[str] = []
        for role, task_info in pending.items():
            pair = _fetch_live_session_pair_for_task(router_url, auth_token, str(task_info.get("task_id", "")).strip())
            if pair is None:
                continue
            plan = _build_role_launch_plan(role, pair)
            if plan.mode != "attach" or plan.task_id != task_info["task_id"]:
                continue
            existing_plans[role] = RoleLaunchPlan(
                role=role,
                mode="spawn" if bool(task_info.get("created")) else "attach",
                remote_init=plan.remote_init,
                session_id=plan.session_id,
                task_id=plan.task_id,
                cli_type=plan.cli_type,
            )
            resolved.append(role)
        for role in resolved:
            pending.pop(role, None)
        if pending:
            time.sleep(poll_interval_s)

    for role in pending:
        if bool(pending[role].get("created")):
            _cancel_ui_role_task(router_url, auth_token, str(pending[role].get("task_id", "")).strip())
        existing_plans[role] = RoleLaunchPlan(
            role=role,
            mode="error",
            remote_init=_spawn_error_remote_init(role, f"session spawn timeout after {int(timeout_s)}s"),
            error=f"session spawn timeout after {int(timeout_s)}s",
        )
    return existing_plans


def _command_for_role(
    role: str,
    repo: str,
    repo_name: str,
    *,
    ui_group_id: str = "",
    launch_mode: str = "",
    provider: str = "",
    session_id: str = "",
    all_roles: list[str] | None = None,
    live_remote_init: str = "",
) -> str:
    effective_provider = provider

    def _wrap_custom_command(command: str) -> str:
        if not command:
            return command
        return " ".join(
            [
                "env",
                f"MESH_UI_GROUP_ID={shlex.quote(ui_group_id)}",
                f"MESH_UI_LAUNCH_MODE={shlex.quote(launch_mode)}",
                f"MESH_UI_PROVIDER={shlex.quote(effective_provider)}",
                f"MESH_UI_SESSION_ID={shlex.quote(session_id)}",
                f"MESH_UI_ROLE={shlex.quote(role)}",
                f"MESH_UI_REPO_NAME={shlex.quote(repo_name)}",
                "bash",
                "-lc",
                shlex.quote(command),
            ]
        )

    env_key = _role_env_key(role)
    template = os.environ.get(env_key, "").strip()
    if template:
        return _wrap_custom_command(
            template.format(
                repo=repo,
                repo_name=repo_name,
                role=role,
                ui_group_id=ui_group_id,
            )
        )

    rules = _load_ui_role_rules()
    rule = rules.get(role, {})
    if not effective_provider:
        effective_provider = _resolved_provider_for_role(role, rule) or "gemini"
    template = rule.get("command_template", "").strip()
    if template:
        return _wrap_custom_command(
            template.format(
                repo=repo,
                repo_name=repo_name,
                role=role,
                ui_group_id=ui_group_id,
            )
        )

    remote_init = (
        live_remote_init
        or rule.get("remote_init", "").strip()
        or _provider_remote_init_for_role(role, rule)
        or _default_remote_init_for_role(role)
    )
    helper = _repo_root() / "scripts" / "mesh_ui_role_shell.sh"
    live_attach_mode = "pre_resolved" if live_remote_init and launch_mode == "attach" else "auto"
    return " ".join(
        [
            shlex.quote(str(helper)),
            shlex.quote(role),
            shlex.quote(repo),
            shlex.quote(repo_name),
            shlex.quote(",".join(all_roles or [role])),
            shlex.quote(remote_init),
            shlex.quote(live_attach_mode),
            shlex.quote(ui_group_id),
            shlex.quote(launch_mode),
            shlex.quote(effective_provider),
            shlex.quote(session_id),
        ]
    )


async def _create_panes_for_roles(tab, roles: list[str]):
    sessions = [tab.current_session]
    while len(sessions) < len(roles):
        # Alternate split direction for a readable grid-like layout.
        vertical = (len(sessions) % 2) == 1
        new_session = await sessions[-1].async_split_pane(vertical=vertical)
        sessions.append(new_session)
    return sessions


def _tab_sessions(tab) -> list[Any]:
    sessions = getattr(tab, "sessions", None)
    if isinstance(sessions, list) and sessions:
        return sessions
    current = getattr(tab, "current_session", None)
    return [current] if current is not None else []


async def _is_mesh_ui_tab(tab, repo: str) -> bool:
    for session in _tab_sessions(tab):
        try:
            marker = await session.async_get_variable("user.mesh_ui_tab")
            if str(marker) != "1":
                continue
            tab_repo = await session.async_get_variable("user.mesh_repo")
            if not tab_repo or str(tab_repo) == repo:
                return True
        except Exception:
            continue
    return False


async def _mark_mesh_ui_sessions(sessions: list[Any], cfg: UiConfig, roles: list[str]) -> None:
    for session, role in zip(sessions, roles):
        try:
            await session.async_set_variable("user.mesh_ui_tab", "1")
            await session.async_set_variable("user.mesh_repo", cfg.repo)
            await session.async_set_variable("user.mesh_role", role)
        except Exception:
            continue


async def _close_tab(tab) -> None:
    close_fn = getattr(tab, "async_close", None)
    if close_fn is None:
        return
    try:
        await close_fn(force=True)
    except TypeError:
        await close_fn()
    except Exception:
        pass


async def _cleanup_existing_mesh_tabs(window, repo: str) -> None:
    tabs = list(window.tabs)
    for tab in tabs:
        if await _is_mesh_ui_tab(tab, repo):
            await _close_tab(tab)


async def _cleanup_existing_mesh_tabs_in_app(app, repo: str) -> None:
    for window in list(getattr(app, "windows", []) or []):
        await _cleanup_existing_mesh_tabs(window, repo)


async def _launch_layout(connection, cfg: UiConfig) -> None:
    import iterm2

    app = await iterm2.async_get_app(connection)
    if cfg.replace_tabs:
        await _cleanup_existing_mesh_tabs_in_app(app, cfg.repo)
    window = await iterm2.Window.async_create(connection)

    if cfg.single_tab:
        groups = [cfg.roles]
    elif cfg.preset == "team-4x3":
        groups = _team_4x3_groups(cfg.roles)
    else:
        groups = _split_groups(cfg.roles, cfg.max_panes_per_tab)

    # iTerm2 3.6.9 is crashing in apiServerSplitPane on this host. Use tabs-only
    # as the safe default unless the operator explicitly overrides MESH_UI_TABS_ONLY=0.
    if _should_avoid_split_panes():
        groups = [[role] for role_group in groups for role in role_group]

    tab_surfaces: list[tuple[list[Any], list[str]]] = []
    for roles in groups:
        tab = await window.async_create_tab()
        sessions = await _create_panes_for_roles(tab, roles)
        await _mark_mesh_ui_sessions(sessions, cfg, roles)
        tab_surfaces.append((sessions, roles))

    router_url, auth_token = _load_router_env()
    session_pairs = _fetch_live_session_pairs(router_url, auth_token) if cfg.attach_live else []
    launch_plans = _build_role_launch_plans(cfg, session_pairs)
    if cfg.attach_live:
        launch_plans = _spawn_missing_agent_role_plans(
            cfg,
            launch_plans,
            router_url=router_url,
            auth_token=auth_token,
        )

    for sessions, roles in tab_surfaces:
        for sess, role in zip(sessions, roles):
            plan = launch_plans.get(role) or RoleLaunchPlan(role=role, mode="spawn")
            cmd = _command_for_role(
                role,
                cfg.repo,
                cfg.repo_name,
                ui_group_id=cfg.ui_group_id,
                launch_mode=plan.mode,
                provider=plan.cli_type,
                session_id=plan.session_id,
                all_roles=cfg.roles,
                live_remote_init=plan.remote_init if plan.mode in {"attach", "spawn", "error"} else "",
            )
            try:
                await sess.async_inject(_ITERM2_CLEAR_SCROLLBACK)
                await sess.async_inject(_ANSI_CLEAR_SCREEN)
            except Exception:
                pass
            banner = f"printf \"\\033[3J\\033[H\\033[2J\"; clear; echo '[mesh:{role}] repo={cfg.repo_name}'; "
            await sess.async_send_text(f"{banner}{cmd}\n")


def main() -> int:
    args = _parse_args()
    if platform.system() != "Darwin":
        print("Error: mesh ui is available only on macOS/iTerm2.", file=sys.stderr)
        return 2

    repo, repo_name = _resolve_repo(args.repo)
    router_url, auth_token = _load_router_env()
    roles = [r.strip() for r in args.roles.split(",") if r.strip()]
    if not roles:
        print("Error: role list is empty.", file=sys.stderr)
        return 2

    cfg = UiConfig(
        repo=repo,
        repo_name=repo_name,
        roles=roles,
        max_panes_per_tab=max(1, args.max_panes_per_tab),
        single_tab=bool(args.single_tab),
        replace_tabs=not bool(args.keep_existing),
        preset=args.preset,
        attach_live=not bool(args.no_attach_live),
        ui_group_id=_resolve_active_ui_group_id(
            repo_name,
            repo_path=repo,
            router_url=router_url,
            auth_token=auth_token,
            fresh=not bool(args.resume),
        ),
    )

    try:
        import iterm2  # type: ignore
    except Exception:
        print(
            "Error: Python package 'iterm2' not found. Install with: uv run --with iterm2 -- python scripts/mesh_iterm_ui.py ... (or pip3 install iterm2)",
            file=sys.stderr,
        )
        return 3

    socket_path = os.path.expanduser("~/Library/Application Support/iTerm2/private/socket")
    try:
        iterm2.run_until_complete(lambda conn: _launch_layout(conn, cfg), retry=_iterm_retry_enabled())
    except Exception as exc:
        if not os.path.exists(socket_path):
            print(
                "Error: iTerm2 Python API is not active (private socket missing). "
                "Open iTerm2 and enable the Python API, or restart iTerm2.",
                file=sys.stderr,
            )
        else:
            print(f"Error: failed to open iTerm2 layout: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
