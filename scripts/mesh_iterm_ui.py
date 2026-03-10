#!/usr/bin/env python3
"""Open a GobabyGo operator layout in iTerm2 using the iTerm2 Python API.

Default roles:
  boss, president, lead, worker-claude, worker-codex, worker-gemini, verifier

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
import json
import os
import platform
import shlex
import sys
from dataclasses import dataclass
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
    "worker-claude",
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


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _default_ui_config_path() -> str:
    return str(_repo_root() / "mapping" / "operator_ui.yaml")


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
        default=int(os.environ.get("MESH_UI_MAX_PANES_PER_TAB", "5")),
        help="Maximum panes per tab (default: 5).",
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
        default=os.environ.get("MESH_UI_PRESET", "team-4x3"),
        help="Layout preset. team-4x3 = 2 tabs (4 panes + 3 panes). auto = chunk by max panes.",
    )
    return parser.parse_args()


def _resolve_repo(repo_arg: str) -> tuple[str, str]:
    if repo_arg:
        if "/" in repo_arg or repo_arg.startswith("."):
            repo_path = os.path.abspath(repo_arg)
            repo_name = os.path.basename(repo_path.rstrip("/"))
            return repo_path, repo_name
        return repo_arg, repo_arg
    cwd = os.path.abspath(os.getcwd())
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


def _load_provider_session_users(config_path: str | None = None) -> dict[str, str]:
    path_value = config_path or str(_repo_root() / "mapping" / "provider_runtime.yaml")
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
        "boss": "mesh status || true; printf '\\n[mesh:boss] Next: mesh thread | mesh start | mesh run <phase>\\n'",
        "president": "mesh thread || true; printf '\\n[mesh:president] Next: inspect the latest thread and coordinate delegation.\\n'",
        "lead": "git status --short || true; printf '\\n[mesh:lead] Next: inspect repo state, branch, and implementation boundaries.\\n'",
        "worker-claude": "printf '[mesh:worker-claude] Claude worker shell ready in %s\\n' \"$PWD\"",
        "worker-codex": "printf '[mesh:worker-codex] Codex worker shell ready in %s\\n' \"$PWD\"",
        "worker-gemini": "printf '[mesh:worker-gemini] Gemini worker shell ready in %s\\n' \"$PWD\"",
        "verifier": "printf '[mesh:verifier] Review/verifier shell ready in %s\\n' \"$PWD\"",
    }
    return defaults.get(role, "")


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


def _session_matches_repo(
    repo: str,
    repo_name: str,
    session: dict[str, Any],
    task: dict[str, Any],
) -> bool:
    task_repo = str(task.get("repo", "") or "").strip()
    working_dir = str((session.get("metadata") or {}).get("working_dir", "") or "").strip()
    candidates = [value for value in (task_repo, working_dir) if value]
    if repo in candidates:
        return True
    for value in candidates:
        if os.path.basename(value.rstrip("/")) == repo_name:
            return True
    return False


def _role_session_score(role: str, session: dict[str, Any], task: dict[str, Any]) -> int:
    cli_type = str(session.get("cli_type", "") or task.get("target_cli", "")).strip()
    task_role = str(task.get("role", "") or "").strip()
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
    session_pairs: list[tuple[dict[str, Any], dict[str, Any]]],
) -> dict[str, tuple[dict[str, Any], dict[str, Any]]]:
    available = [
        (session, task)
        for session, task in session_pairs
        if _session_matches_repo(repo, repo_name, session, task)
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


def _discover_live_remote_inits(cfg: UiConfig) -> dict[str, str]:
    router_url, auth_token = _load_router_env()
    if not router_url or not auth_token:
        return {}
    try:
        sessions_payload = _router_get_json(router_url, auth_token, "/sessions?state=open&limit=200")
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
        return {}

    sessions = sessions_payload.get("sessions") if isinstance(sessions_payload, dict) else None
    if not isinstance(sessions, list):
        return {}

    session_pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    task_cache: dict[str, dict[str, Any]] = {}
    for session in sessions:
        if not isinstance(session, dict):
            continue
        task_id = str(session.get("task_id", "")).strip()
        if not task_id:
            continue
        task = task_cache.get(task_id)
        if task is None:
            try:
                task_payload = _router_get_json(router_url, auth_token, f"/tasks/{quote(task_id)}")
            except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
                continue
            if not isinstance(task_payload, dict):
                continue
            task = task_payload
            task_cache[task_id] = task
        session_pairs.append((session, task))

    selected = _select_live_sessions_for_roles(cfg.roles, cfg.repo, cfg.repo_name, session_pairs)
    return {
        role: _build_tmux_attach_remote_init(role, session, task)
        for role, (session, task) in selected.items()
        if _build_tmux_attach_remote_init(role, session, task)
    }


def _command_for_role(
    role: str,
    repo: str,
    repo_name: str,
    *,
    live_remote_init: str = "",
) -> str:
    env_key = _role_env_key(role)
    template = os.environ.get(env_key, "").strip()
    if template:
        return template.format(repo=repo, repo_name=repo_name, role=role)

    rules = _load_ui_role_rules()
    rule = rules.get(role, {})
    template = rule.get("command_template", "").strip()
    if template:
        return template.format(repo=repo, repo_name=repo_name, role=role)

    remote_init = live_remote_init or rule.get("remote_init", "").strip() or _default_remote_init_for_role(role)
    helper = _repo_root() / "scripts" / "mesh_ui_role_shell.sh"
    return " ".join(
        [
            shlex.quote(str(helper)),
            shlex.quote(role),
            shlex.quote(repo),
            shlex.quote(repo_name),
            shlex.quote(remote_init),
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


async def _is_mesh_ui_tab(tab) -> bool:
    try:
        marker = await tab.current_session.async_get_variable("user.mesh_ui_tab")
        return str(marker) == "1"
    except Exception:
        return False


async def _mark_mesh_ui_tab(tab) -> None:
    try:
        await tab.current_session.async_set_variable("user.mesh_ui_tab", "1")
    except Exception:
        pass


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
    live_remote_inits = _discover_live_remote_inits(cfg) if cfg.attach_live else {}
    for tab_index, roles in enumerate(groups):
        tab = await window.async_create_tab()
        await _mark_mesh_ui_tab(tab)
        sessions = await _create_panes_for_roles(tab, roles)
        for sess, role in zip(sessions, roles):
            cmd = _command_for_role(
                role,
                cfg.repo,
                cfg.repo_name,
                live_remote_init=live_remote_inits.get(role, ""),
            )
            banner = f"clear; echo '[mesh:{role}] repo={cfg.repo_name}'; "
            await sess.async_send_text(f"{banner}{cmd}\n")


def main() -> int:
    args = _parse_args()
    if platform.system() != "Darwin":
        print("Error: mesh ui is available only on macOS/iTerm2.", file=sys.stderr)
        return 2

    repo, repo_name = _resolve_repo(args.repo)
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
