#!/usr/bin/env python3
"""Archived mesh-lite custom spawner experiment.

This branch is intentionally archived because `mesh ui` remains the canonical
layout lifecycle. The symbols here are kept only as historical reference for
the discarded custom `mesh-lite` spawn path.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from scripts.mesh_lite.iterm import _clean, _osascript, _send_line_via_osascript, _tty_via_osascript


@dataclass
class SpawnedRoleSession:
    role: str
    session_id: str
    tty: str
    title: str
    badge: str


def _split_groups(items: list[str], size: int) -> list[list[str]]:
    if not items:
        return []
    if size <= 0:
        size = 3
    return [items[i : i + size] for i in range(0, len(items), size)]


async def _create_panes_for_roles(tab, roles: list[str]):
    if tab is None:
        raise RuntimeError("iTerm2 tab not available")

    current = getattr(tab, "current_session", None)
    if current is None:
        sessions_attr = getattr(tab, "sessions", None)
        if isinstance(sessions_attr, list) and sessions_attr:
            current = sessions_attr[0]
    if current is None:
        raise RuntimeError("iTerm2 tab has no initial session")

    sessions = [current]
    while len(sessions) < len(roles):
        vertical = (len(sessions) % 2) == 1
        new_session = await sessions[-1].async_split_pane(vertical=vertical)
        sessions.append(new_session)
    return sessions


async def _spawn_team_layout(
    connection,
    *,
    repo: str,
    ui_group_id: str,
    roles: list[str],
    commands: dict[str, str],
    single_tab: bool,
    max_panes_per_tab: int,
) -> list[SpawnedRoleSession]:
    import iterm2

    window = await iterm2.Window.async_create(connection)
    groups = [roles] if single_tab else _split_groups(roles, max_panes_per_tab)
    created: list[SpawnedRoleSession] = []

    for idx, group in enumerate(groups):
        if idx == 0:
            tab = getattr(window, "current_tab", None)
            if tab is None:
                tab = await window.async_create_tab()
        else:
            tab = await window.async_create_tab()
        sessions = await _create_panes_for_roles(tab, group)
        for session, role in zip(sessions, group):
            try:
                await session.async_set_variable("user.mesh_lite", "1")
                await session.async_set_variable("user.mesh_repo", repo)
                await session.async_set_variable("user.mesh_role", role)
                await session.async_set_variable("user.mesh_team", ui_group_id)
            except Exception:
                pass
            banner = f"printf \"\\033[3J\\033[H\\033[2J\"; clear; echo '[mesh-lite:{role}] repo={os.path.basename(repo)}'; "
            await session.async_send_text(f"{banner}{commands[role]}\n")
            tty = _clean(getattr(session, "tty", "")) or _tty_via_osascript(session.session_id)
            created.append(
                SpawnedRoleSession(
                    role=role,
                    session_id=session.session_id,
                    tty=tty,
                    title=f"mesh-lite:{role}",
                    badge=role,
                )
            )
    return created


def spawn_team_layout(
    *,
    repo: str,
    ui_group_id: str,
    roles: list[str],
    commands: dict[str, str],
    single_tab: bool = False,
    max_panes_per_tab: int = 3,
) -> list[SpawnedRoleSession]:
    result: list[SpawnedRoleSession] = []

    async def _run(connection):
        nonlocal result
        result = await _spawn_team_layout(
            connection,
            repo=repo,
            ui_group_id=ui_group_id,
            roles=roles,
            commands=commands,
            single_tab=single_tab,
            max_panes_per_tab=max_panes_per_tab,
        )

    try:
        import iterm2

        iterm2.run_until_complete(_run, retry=False)
        return result
    except BaseException:
        return _spawn_team_layout_via_osascript(
            repo=repo,
            ui_group_id=ui_group_id,
            roles=roles,
            commands=commands,
            single_tab=single_tab,
            max_panes_per_tab=max_panes_per_tab,
        )


def _create_layout_via_osascript(
    *,
    groups: list[list[str]],
) -> list[str]:
    if not groups:
        return []
    script_lines = [
        'tell application "iTerm2"',
        "  activate",
        "  set outputLines to {}",
        "  set theWindow to (create window with default profile)",
        "  delay 0.2",
    ]
    for group_index, group in enumerate(groups):
        if group_index == 0:
            script_lines.append("  set theTab to current tab of theWindow")
        else:
            script_lines.append('  tell theWindow to create tab with default profile command ""')
            script_lines.append("  delay 0.15")
            script_lines.append("  set theTab to current tab of theWindow")
        script_lines.append("  copy (id of current session of theTab as text) to end of outputLines")
        for pane_index in range(1, len(group)):
            direction = "vertically" if (pane_index % 2) == 1 else "horizontally"
            script_lines.append("  tell current session of theTab")
            script_lines.append(f'    split {direction} with default profile command ""')
            script_lines.append("  end tell")
            script_lines.append("  delay 0.1")
            script_lines.append("  tell theTab")
            script_lines.append("    set sessionIds to id of every session")
            script_lines.append("  end tell")
            script_lines.append("  copy (item -1 of sessionIds as text) to end of outputLines")
        script_lines.append("  delay 0.1")
    script_lines.extend(["  return outputLines as text", "end tell"])
    raw = _osascript("\n".join(script_lines))
    normalized = raw.replace(", ", "\n").replace(",", "\n")
    return [line.strip() for line in normalized.splitlines() if line.strip()]


def _spawn_team_layout_via_osascript(
    *,
    repo: str,
    ui_group_id: str,
    roles: list[str],
    commands: dict[str, str],
    single_tab: bool,
    max_panes_per_tab: int,
) -> list[SpawnedRoleSession]:
    groups = [roles] if single_tab else _split_groups(roles, max_panes_per_tab)
    session_ids = _create_layout_via_osascript(groups=groups)
    if len(session_ids) < len(roles):
        raise RuntimeError(
            f"AppleScript spawn created too few sessions: expected {len(roles)}, got {len(session_ids)}"
        )

    created: list[SpawnedRoleSession] = []
    for role, session_id in zip(roles, session_ids):
        _send_line_via_osascript(
            session_id,
            f'printf "\\033[3J\\033[H\\033[2J"; clear; echo "[mesh-lite:{role}] repo={os.path.basename(repo)}"; {commands[role]}',
        )
        created.append(
            SpawnedRoleSession(
                role=role,
                session_id=session_id,
                tty=_tty_via_osascript(session_id),
                title=f"mesh-lite:{role}",
                badge=role,
            )
        )
    return created
