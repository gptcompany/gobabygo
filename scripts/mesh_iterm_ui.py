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
import os
import platform
import shlex
import sys
from dataclasses import dataclass


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


def _role_env_key(role: str) -> str:
    return "MESH_UI_CMD_" + role.upper().replace("-", "_")


def _default_command_for_role(role: str, repo_name: str) -> str:
    # Keep default bootstrap minimal and robust.
    return f"wss {shlex.quote(repo_name)}"


def _command_for_role(role: str, repo: str, repo_name: str) -> str:
    env_key = _role_env_key(role)
    template = os.environ.get(env_key, "").strip()
    if template:
        return template.format(repo=repo, repo_name=repo_name)
    return _default_command_for_role(role, repo_name)


async def _create_panes_for_roles(tab, roles: list[str]):
    sessions = [tab.current_session]
    while len(sessions) < len(roles):
        # Alternate split direction for a readable grid-like layout.
        vertical = (len(sessions) % 2) == 1
        new_session = await sessions[-1].async_split_pane(vertical=vertical)
        sessions.append(new_session)
    return sessions


async def _launch_layout(connection, cfg: UiConfig) -> None:
    import iterm2

    app = await iterm2.async_get_app(connection)
    window = app.current_window
    if window is None:
        window = await iterm2.Window.async_create(connection)

    groups = [cfg.roles] if cfg.single_tab else _split_groups(cfg.roles, cfg.max_panes_per_tab)
    for tab_index, roles in enumerate(groups):
        if tab_index == 0 and window.current_tab is not None:
            tab = window.current_tab
        else:
            tab = await window.async_create_tab()
        sessions = await _create_panes_for_roles(tab, roles)
        for sess, role in zip(sessions, roles):
            cmd = _command_for_role(role, cfg.repo, cfg.repo_name)
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
    )

    try:
        import iterm2  # type: ignore
    except Exception:
        print(
            "Error: Python package 'iterm2' not found. Install with: pip3 install iterm2",
            file=sys.stderr,
        )
        return 2

    try:
        iterm2.run_until_complete(lambda conn: _launch_layout(conn, cfg))
    except Exception as exc:
        print(f"Error: failed to open iTerm2 layout: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
