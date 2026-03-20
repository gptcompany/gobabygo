#!/usr/bin/env python3
"""Open a GobabyGo operator layout in iTerm2 using the iTerm2 Python API.

Default roles:
  boss, president, lead, worker-codex, worker-gemini, verifier

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
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import yaml


DEFAULT_ROLES = [
    "boss",
    "president",
    "lead",
    "worker-codex",
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


def _resolve_repo(repo_arg: str) -> tuple[str, str]:
    if repo_arg:
        if "/" in repo_arg or repo_arg.startswith("."):
            repo_path = _repo_root_path(repo_arg)
            repo_name = os.path.basename(repo_path.rstrip("/"))
            return repo_path, repo_name
        candidate = os.path.abspath(repo_arg)
        if os.path.isdir(candidate):
            repo_path = _repo_root_path(candidate)
            repo_name = os.path.basename(repo_path.rstrip("/"))
            return repo_path, repo_name
        return repo_arg, repo_arg
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

    candidates = [
        Path.home() / ".mesh" / "router.env",
        Path.home() / ".mesh" / ".env.mesh",
        Path("/etc/mesh-worker/common.env"),
    ]
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


def _router_get_json(router_url: str, auth_token: str, path: str) -> Any:
    req = Request(router_url.rstrip("/") + path)
    req.add_header("Authorization", f"Bearer {auth_token}")
    with urlopen(req, timeout=5) as resp:
        return json.load(resp)


def _router_post_json(router_url: str, auth_token: str, path: str, payload: dict[str, Any]) -> Any:
    req = Request(
        router_url.rstrip("/") + path,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
    )
    req.add_header("Authorization", f"Bearer {auth_token}")
    req.add_header("Content-Type", "application/json")
    with urlopen(req, timeout=10) as resp:
        return json.load(resp)


def _router_has_live_ui_group(router_url: str, auth_token: str, ui_group_id: str) -> bool:
    if not router_url or not auth_token or not ui_group_id:
        return False
    try:
        payload = _router_get_json(router_url, auth_token, "/sessions?state=open&limit=200")
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
        return False
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
) -> str:
    cached = _read_ui_group_cache(repo_name, repo_path=repo_path, cache_dir=cache_dir)
    cached_group = str((cached or {}).get("ui_group_id", "")).strip()
    if cached_group and _router_has_live_ui_group(router_url, auth_token, cached_group):
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


def _load_ui_role_rules(config_path: str | None = None) -> dict[str, dict[str, str]]:
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

    result: dict[str, dict[str, str]] = {}
    for role, entry in roles.items():
        if not isinstance(entry, dict):
            continue
        result[str(role).strip()] = {
            str(key).strip(): str(value).strip()
            for key, value in entry.items()
            if str(key).strip() and str(value).strip()
        }
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
    return role != "boss"


def _resolved_provider_for_role(role: str, rule: dict[str, str]) -> str:
    provider = os.environ.get("MESH_UI_PROVIDER_OVERRIDE", "").strip() or rule.get("provider", "").strip()
    if not provider and role.startswith("worker-"):
        provider = role.split("-", 1)[1].strip()
    return provider


def _default_target_account_for_provider(provider: str) -> str:
    if provider == "claude":
        return "work-claude"
    return provider


def _resolve_role_task_target(role: str) -> tuple[str, str]:
    rules = _load_ui_role_rules()
    rule = rules.get(role, {})
    provider = _resolved_provider_for_role(role, rule)
    if not provider:
        provider = "gemini"
    target_account = rule.get("target_account", "").strip() or _default_target_account_for_provider(provider)
    return provider, target_account


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
    return f"mesh-ui::{cfg.ui_group_id}::{role}"


def _task_payload(task: dict[str, Any]) -> dict[str, Any]:
    return task.get("payload") if isinstance(task.get("payload"), dict) else {}


def _task_matches_ui_role(cfg: UiConfig, role: str, task: dict[str, Any]) -> bool:
    payload = _task_payload(task)
    if not payload.get("ui_role_session"):
        return False
    if str(task.get("repo") or "").strip() != cfg.repo:
        return False
    if str(task.get("role") or payload.get("ui_role") or "").strip() != role:
        return False
    if str(payload.get("ui_group_id") or "").strip() != cfg.ui_group_id:
        return False
    return True


def _find_existing_ui_role_task(
    router_url: str,
    auth_token: str,
    cfg: UiConfig,
    role: str,
) -> dict[str, Any] | None:
    status_priority = {
        "running": 5,
        "assigned": 4,
        "review": 3,
        "blocked": 2,
        "queued": 1,
    }
    matches: list[dict[str, Any]] = []
    for status in ("queued", "assigned", "blocked", "review", "running"):
        try:
            payload = _router_get_json(router_url, auth_token, f"/tasks?status={quote(status)}&limit=200")
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
            continue
        tasks = payload.get("tasks") if isinstance(payload, dict) else None
        if not isinstance(tasks, list):
            continue
        status_matches = [
            task
            for task in tasks
            if isinstance(task, dict) and _task_matches_ui_role(cfg, role, task)
        ]
        matches.extend(status_matches)
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
    existing = _find_existing_ui_role_task(router_url, auth_token, cfg, role)
    if existing is not None:
        return {
            "role": role,
            "task_id": str(existing.get("task_id", "")).strip(),
            "target_cli": str(existing.get("target_cli", "")).strip(),
            "created": False,
        }

    target_cli, target_account = _resolve_role_task_target(role)
    payload = {
        "title": f"mesh ui {role} {cfg.repo_name}",
        "repo": cfg.repo,
        "role": role,
        "target_cli": target_cli,
        "target_account": target_account,
        "execution_mode": "session",
        "payload": {
            "ui_role_session": True,
            "ui_role": role,
            "ui_group_id": cfg.ui_group_id,
            "working_dir": cfg.repo,
        },
        "idempotency_key": _ui_role_task_idempotency_key(cfg, role),
    }
    try:
        created = _router_post_json(router_url, auth_token, "/tasks", payload)
    except HTTPError as exc:
        if exc.code != 409:
            raise
        existing = _find_existing_ui_role_task(router_url, auth_token, cfg, role)
        if existing is None:
            raise RuntimeError(f"duplicate ui role task for {role} but existing task was not found") from exc
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
    timeout_s: float = 60.0,
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
        session_pairs = _fetch_live_session_pairs(router_url, auth_token)
        latest_plans = _build_role_launch_plans(cfg, session_pairs)
        resolved: list[str] = []
        for role, task_info in pending.items():
            plan = latest_plans.get(role)
            if plan is None or plan.mode != "attach" or plan.task_id != task_info["task_id"]:
                continue
            existing_plans[role] = RoleLaunchPlan(
                role=role,
                mode="spawn",
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
            remote_init=_spawn_error_remote_init(role, "session spawn timeout after 60s"),
            error="session spawn timeout after 60s",
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
    if not effective_provider and role != "boss":
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
    live_attach_mode = "pre_resolved" if live_remote_init else "auto"
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


async def _is_mesh_ui_tab(tab) -> bool:
    for session in _tab_sessions(tab):
        try:
            marker = await session.async_get_variable("user.mesh_ui_tab")
            if str(marker) == "1":
                return True
        except Exception:
            continue
    return False


async def _mark_mesh_ui_sessions(sessions: list[Any]) -> None:
    for session in sessions:
        try:
            await session.async_set_variable("user.mesh_ui_tab", "1")
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


async def _cleanup_existing_mesh_tabs(window) -> None:
    tabs = list(window.tabs)
    for tab in tabs:
        if await _is_mesh_ui_tab(tab):
            await _close_tab(tab)


async def _launch_layout(connection, cfg: UiConfig) -> None:
    import iterm2

    app = await iterm2.async_get_app(connection)
    window = app.current_window
    if window is None:
        window = await iterm2.Window.async_create(connection)

    if cfg.replace_tabs:
        await _cleanup_existing_mesh_tabs(window)

    if cfg.single_tab:
        groups = [cfg.roles]
    elif cfg.preset == "team-4x3":
        groups = _team_4x3_groups(cfg.roles)
    else:
        groups = _split_groups(cfg.roles, cfg.max_panes_per_tab)
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
    for roles in groups:
        tab = await window.async_create_tab()
        sessions = await _create_panes_for_roles(tab, roles)
        await _mark_mesh_ui_sessions(sessions)
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
            banner = f"clear; echo '[mesh:{role}] repo={cfg.repo_name}'; "
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

    try:
        iterm2.run_until_complete(lambda conn: _launch_layout(conn, cfg))
    except Exception as exc:
        print(f"Error: failed to open iTerm2 layout: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
