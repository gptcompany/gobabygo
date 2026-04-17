#!/usr/bin/env python3
"""Minimal iTerm2 control helper for mesh-marked panes.

This script is intentionally mechanical. It does not infer workflow or routing;
it only finds panes marked by ``mesh_iterm_ui.py`` and performs direct actions:

- list mesh panes
- focus a pane
- send text
- send a key escape
- dump recent screen contents
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import os
import shlex
import shutil
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class MeshPane:
    window_index: int
    tab_index: int
    session_index: int
    repo: str
    role: str
    ui_group_id: str
    tab: Any
    session: Any


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Control mesh-marked iTerm2 panes.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    list_parser = sub.add_parser("list", help="List mesh-marked panes.")
    list_parser.add_argument("--repo", default="", help="Filter by repo path.")
    list_parser.add_argument("--ui-group-id", default="", help="Filter by mesh UI group id.")
    list_parser.add_argument("--output", default="", help="Write output to this file instead of stdout.")

    close_parser = sub.add_parser("close", help="Close mesh-marked tabs for a repo.")
    close_parser.add_argument("--repo", required=True, help="Exact mesh repo path.")
    close_parser.add_argument("--ui-group-id", default="", help="Close only tabs in this mesh UI group id.")

    smoke_parser = sub.add_parser("two-cli-smoke", help="Run a bidirectional live smoke between boss and president panes.")
    smoke_parser.add_argument("--repo", required=True, help="Exact mesh repo path.")
    smoke_parser.add_argument("--ui-group-id", default="", help="Target a specific mesh UI group id.")
    smoke_parser.add_argument("--boss-role", default="boss", help="Source Gemini role.")
    smoke_parser.add_argument("--president-role", default="president", help="Source Codex role.")
    smoke_parser.add_argument("--gemini-model", default="", help="Optional model command sent to the boss pane before testing.")
    smoke_parser.add_argument("--run-id", default="", help="Optional marker suffix.")
    smoke_parser.add_argument("--response-timeout", type=float, default=120.0, help="Seconds to wait for each marker.")
    smoke_parser.add_argument("--poll-interval", type=float, default=3.0, help="Seconds between screen polls.")

    e2e_parser = sub.add_parser("two-cli-e2e", help="Open, verify, and optionally close a local Gemini/Codex layout.")
    e2e_parser.add_argument("--repo", required=True, help="Exact repo path.")
    e2e_parser.add_argument("--boss-cmd", default=os.environ.get("MESH_TWO_CLI_BOSS_CMD", "gemini"))
    e2e_parser.add_argument("--president-cmd", default=os.environ.get("MESH_TWO_CLI_PRESIDENT_CMD", "codex"))
    e2e_parser.add_argument("--boss-role", default="boss")
    e2e_parser.add_argument("--president-role", default="president")
    e2e_parser.add_argument("--gemini-model", default="", help="Optional model command sent to the boss pane before testing.")
    e2e_parser.add_argument("--ui-group-id", default="", help="Optional mesh UI group id.")
    e2e_parser.add_argument("--startup-wait", type=float, default=12.0, help="Seconds to wait for CLIs after launch.")
    e2e_parser.add_argument("--startup-timeout", type=float, default=90.0, help="Seconds to wait for CLI prompts.")
    e2e_parser.add_argument("--response-timeout", type=float, default=120.0, help="Seconds to wait for each marker.")
    e2e_parser.add_argument("--poll-interval", type=float, default=3.0, help="Seconds between screen polls.")
    e2e_parser.add_argument("--keep-open", action="store_true", help="Leave the test layout open after completion.")

    team_parser = sub.add_parser("team-e2e", help="Open and verify a local boss/president/worker CLI chain.")
    team_parser.add_argument("--repo", required=True, help="Exact repo path.")
    team_parser.add_argument("--boss-cmd", default=os.environ.get("MESH_TEAM_BOSS_CMD", "claude"))
    team_parser.add_argument("--president-cmd", default=os.environ.get("MESH_TEAM_PRESIDENT_CMD", "codex"))
    team_parser.add_argument("--worker-cmd", default=os.environ.get("MESH_TEAM_WORKER_CMD", "gemini"))
    team_parser.add_argument("--boss-role", default="boss")
    team_parser.add_argument("--president-role", default="president")
    team_parser.add_argument("--worker-role", default="worker-gemini")
    team_parser.add_argument("--ui-group-id", default="", help="Optional mesh UI group id.")
    team_parser.add_argument("--startup-wait", type=float, default=12.0, help="Seconds to wait after launching panes.")
    team_parser.add_argument("--startup-timeout", type=float, default=120.0, help="Seconds to wait for CLI prompts.")
    team_parser.add_argument("--response-timeout", type=float, default=120.0, help="Seconds to wait for each marker.")
    team_parser.add_argument("--poll-interval", type=float, default=3.0, help="Seconds between screen polls.")
    team_parser.add_argument("--keep-open", action="store_true", help="Leave the test layout open after completion.")

    for name in ("focus", "dump", "send-text", "send-line", "send-key"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--repo", required=True, help="Exact mesh repo path.")
        cmd.add_argument("--role", required=True, help="Exact mesh role.")
        cmd.add_argument("--ui-group-id", default="", help="Target a specific mesh UI group id.")
        if name == "dump":
            cmd.add_argument("--lines", type=int, default=20, help="Trailing non-empty lines to print.")
            cmd.add_argument("--output", default="", help="Write output to this file instead of stdout.")
        elif name in {"send-text", "send-line"}:
            cmd.add_argument("text", help="Text to send verbatim.")
        elif name == "send-key":
            cmd.add_argument("key", help="Logical key: enter/up/down/left/right/esc/tab/backspace/ctrl-c.")

    return parser.parse_args()


async def _mesh_sessions(app, repo_filter: str = "", ui_group_filter: str = "") -> list[MeshPane]:
    panes: list[MeshPane] = []
    repo_filter = str(repo_filter or "").strip()
    ui_group_filter = str(ui_group_filter or "").strip()
    for wi, window in enumerate(getattr(app, "windows", []), 1):
        for ti, tab in enumerate(getattr(window, "tabs", []), 1):
            for si, session in enumerate(getattr(tab, "sessions", []), 1):
                try:
                    marker = await session.async_get_variable("user.mesh_ui_tab")
                    repo = str(await session.async_get_variable("user.mesh_repo") or "").strip()
                    role = str(await session.async_get_variable("user.mesh_role") or "").strip()
                    ui_group_id = str(await session.async_get_variable("user.mesh_ui_group_id") or "").strip()
                except Exception:
                    continue
                if str(marker) != "1" or not repo or not role:
                    continue
                if repo_filter and repo != repo_filter:
                    continue
                if ui_group_filter and ui_group_id != ui_group_filter:
                    continue
                panes.append(
                    MeshPane(
                        window_index=wi,
                        tab_index=ti,
                        session_index=si,
                        repo=repo,
                        role=role,
                        ui_group_id=ui_group_id,
                        tab=tab,
                        session=session,
                    )
                )
    return panes


async def _find_mesh_pane(app, repo: str, role: str, ui_group_id: str = "") -> MeshPane:
    repo = str(repo or "").strip()
    role = str(role or "").strip()
    ui_group_id = str(ui_group_id or "").strip()
    matches = [pane for pane in await _mesh_sessions(app, repo, ui_group_id) if pane.role == role]
    if not matches:
        raise RuntimeError(f"no pane matched repo={repo!r} role={role!r} ui_group_id={ui_group_id!r}")
    if len(matches) > 1:
        raise RuntimeError(f"multiple panes matched repo={repo!r} role={role!r} ui_group_id={ui_group_id!r}")
    return matches[0]


def _key_text(key: str) -> str:
    normalized = str(key or "").strip().lower()
    mapping = {
        "enter": "\r",
        "return": "\r",
        "up": "\x1b[A",
        "down": "\x1b[B",
        "right": "\x1b[C",
        "left": "\x1b[D",
        "esc": "\x1b",
        "escape": "\x1b",
        "tab": "\t",
        "backspace": "\x7f",
        "ctrl-c": "\x03",
        "interrupt": "\x03",
    }
    text = mapping.get(normalized)
    if text is None:
        raise ValueError(f"unsupported key: {key}")
    return text


def _iterm_retry_enabled() -> bool:
    return str(os.environ.get("MESH_ITERM_RETRY", "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _emit(text: str, output_path: str = "") -> None:
    if output_path:
        with open(output_path, "w", encoding="utf-8") as handle:
            handle.write(text)
            if not text.endswith("\n"):
                handle.write("\n")
        return
    print(text)


def _repo_name(repo: str) -> str:
    return os.path.basename(str(repo or "").rstrip("/")) or "repo"


def _ui_command_env_key(role: str) -> str:
    return "MESH_UI_CMD_" + str(role or "").upper().replace("-", "_")


def _role_launch_command(repo: str, command_text: str) -> str:
    return f"cd {shlex.quote(str(repo or ''))} && exec {command_text}"


def _load_mesh_iterm_ui():
    script_path = Path(__file__).resolve().with_name("mesh_iterm_ui.py")
    spec = importlib.util.spec_from_file_location("mesh_iterm_ui_for_control", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load mesh UI module at {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _ensure_command(command_text: str) -> None:
    executable = str(command_text or "").strip().split(" ", 1)[0]
    if not executable:
        raise RuntimeError("empty CLI command")
    if shutil.which(executable) is None:
        raise RuntimeError(f"required command not found in PATH: {executable}")


def _set_env_temporarily(values: dict[str, str]) -> dict[str, str | None]:
    previous: dict[str, str | None] = {}
    for key, value in values.items():
        previous[key] = os.environ.get(key)
        os.environ[key] = value
    return previous


def _restore_env(previous: dict[str, str | None]) -> None:
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


async def _launch_role_layout(
    connection: Any,
    *,
    repo: str,
    roles: list[str],
    commands: dict[str, str],
    ui_group_id: str,
) -> None:
    ui = _load_mesh_iterm_ui()
    repo_name = _repo_name(repo)
    env_values = {
        "MESH_UI_ROLES": ",".join(roles),
        "MESH_UI_MAX_PANES_PER_TAB": str(max(1, len(roles))),
    }
    for role in roles:
        env_values[_ui_command_env_key(role)] = _role_launch_command(repo, commands[role])
    env_previous = _set_env_temporarily(env_values)
    try:
        cfg = ui.UiConfig(
            repo=repo,
            repo_name=repo_name,
            roles=roles,
            max_panes_per_tab=max(1, len(roles)),
            single_tab=False,
            replace_tabs=False,
            preset="auto",
            attach_live=False,
            ui_group_id=ui_group_id,
        )
        await ui._launch_layout(connection, cfg)
    finally:
        _restore_env(env_previous)


async def _screen_tail(session: Any, lines: int = 20) -> str:
    screen = await session.async_get_screen_contents()
    collected: list[str] = []
    for idx in range(getattr(screen, "number_of_lines", 0)):
        raw = str(screen.line(idx).string or "")
        line = raw.replace("\x00", "").rstrip()
        if line.strip():
            collected.append(line)
    return "\n".join(collected[-max(1, int(lines)) :])


async def _send_line(session: Any, text: str) -> None:
    await session.async_activate()
    await asyncio.sleep(0.25)
    await session.async_send_text(text)
    await asyncio.sleep(0.08)
    await session.async_send_text("\r")


async def _wait_for_screen_marker(
    session: Any,
    *,
    role: str,
    marker: str,
    timeout: float,
    poll_interval: float,
) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(1.0, float(timeout))
    last_dump = ""
    while loop.time() < deadline:
        last_dump = await _screen_tail(session, lines=360)
        if marker in last_dump:
            return
        await asyncio.sleep(max(0.5, float(poll_interval)))
    print(f"--- last dump for {role} ---")
    print(last_dump)
    raise RuntimeError(f"timed out waiting for marker {marker!r} in role={role}")


async def _wait_for_screen_any(
    session: Any,
    *,
    role: str,
    markers: tuple[str, ...],
    timeout: float,
    poll_interval: float,
    description: str,
) -> str:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(1.0, float(timeout))
    last_dump = ""
    while loop.time() < deadline:
        last_dump = await _screen_tail(session, lines=360)
        for marker in markers:
            if marker and marker in last_dump:
                return marker
        await asyncio.sleep(max(0.5, float(poll_interval)))
    print(f"--- last dump for {role} ---")
    print(last_dump)
    raise RuntimeError(f"timed out waiting for {description} in role={role}")


async def _wait_for_cli_ready(session: Any, *, role: str, command_text: str, timeout: float, poll_interval: float) -> None:
    command_name = str(command_text or "").strip().split(" ", 1)[0]
    if command_name == "gemini":
        await _wait_for_screen_any(
            session,
            role=role,
            markers=("Type your message",),
            timeout=timeout,
            poll_interval=poll_interval,
            description="Gemini prompt",
        )
    elif command_name == "codex":
        await _wait_for_screen_any(
            session,
            role=role,
            markers=("Write tests for", "›"),
            timeout=timeout,
            poll_interval=poll_interval,
            description="Codex prompt",
        )
    elif command_name == "claude":
        await _wait_for_screen_any(
            session,
            role=role,
            markers=("cwd:", "cwd", "Claude", ">"),
            timeout=timeout,
            poll_interval=poll_interval,
            description="Claude prompt",
        )


async def _close_mesh_tabs(app: Any, repo: str, ui_group_id: str = "") -> int:
    tabs: dict[int, Any] = {}
    for pane in await _mesh_sessions(app, repo, ui_group_id):
        tabs[id(pane.tab)] = pane.tab
    for tab in tabs.values():
        close_fn = getattr(tab, "async_close", None)
        if close_fn is None:
            continue
        try:
            await close_fn(force=True)
        except TypeError:
            await close_fn()
    return len(tabs)


async def _run_two_cli_smoke(app: Any, args: argparse.Namespace) -> int:
    ui_group_id = str(getattr(args, "ui_group_id", "") or "").strip()
    boss = await _find_mesh_pane(app, args.repo, args.boss_role, ui_group_id)
    president = await _find_mesh_pane(app, args.repo, args.president_role, ui_group_id)

    run_id = str(args.run_id or uuid.uuid4().hex[:8]).upper().replace("-", "_")
    gemini_marker = f"GEMINI_TO_CODEX_{run_id}"
    codex_ack = f"CODEX_SAW_GEMINI_{run_id}"
    codex_marker = f"CODEX_TO_GEMINI_{run_id}"
    gemini_ack = f"GEMINI_SAW_CODEX_{run_id}"

    print(
        f"panes: boss=W{boss.window_index} T{boss.tab_index} S{boss.session_index} "
        f"president=W{president.window_index} T{president.tab_index} S{president.session_index}"
    )
    print(f"run_id: {run_id}")

    if str(args.gemini_model or "").strip():
        print(f"selecting boss model: {args.gemini_model}")
        await _send_line(boss.session, f"/model {args.gemini_model}")
        await asyncio.sleep(2)

    print("1. Gemini emits a marker")
    await _send_line(
        boss.session,
        f'Rispondi solo con la concatenazione esatta di "GEMINI_TO_CODEX_" e "{run_id}".',
    )
    await _wait_for_screen_marker(
        boss.session,
        role=args.boss_role,
        marker=gemini_marker,
        timeout=args.response_timeout,
        poll_interval=args.poll_interval,
    )

    print("2. Relay Gemini marker to Codex and verify Codex acknowledgement")
    await _send_line(
        president.session,
        (
            f"Messaggio ricevuto da boss: {gemini_marker}. "
            f'Rispondi solo con la concatenazione esatta di "CODEX_SAW_GEMINI_" e "{run_id}".'
        ),
    )
    await _wait_for_screen_marker(
        president.session,
        role=args.president_role,
        marker=codex_ack,
        timeout=args.response_timeout,
        poll_interval=args.poll_interval,
    )

    print("3. Codex emits a marker")
    await _send_line(
        president.session,
        f'Rispondi solo con la concatenazione esatta di "CODEX_TO_GEMINI_" e "{run_id}".',
    )
    await _wait_for_screen_marker(
        president.session,
        role=args.president_role,
        marker=codex_marker,
        timeout=args.response_timeout,
        poll_interval=args.poll_interval,
    )

    print("4. Relay Codex marker to Gemini and verify Gemini acknowledgement")
    await _send_line(
        boss.session,
        (
            f"Messaggio ricevuto da president: {codex_marker}. "
            f'Rispondi solo con la concatenazione esatta di "GEMINI_SAW_CODEX_" e "{run_id}".'
        ),
    )
    await _wait_for_screen_marker(
        boss.session,
        role=args.boss_role,
        marker=gemini_ack,
        timeout=args.response_timeout,
        poll_interval=args.poll_interval,
    )

    print("success:")
    print(f"  {gemini_marker}")
    print(f"  {codex_ack}")
    print(f"  {codex_marker}")
    print(f"  {gemini_ack}")
    return 0


async def _run_two_cli_e2e(connection: Any, app: Any, args: argparse.Namespace) -> int:
    _ensure_command(args.boss_cmd)
    _ensure_command(args.president_cmd)

    repo = str(args.repo or "").strip()
    repo_name = _repo_name(repo)
    ui_group_id = str(args.ui_group_id or f"{repo_name}-two-cli-{uuid.uuid4().hex[:8]}").strip()
    roles = [args.boss_role, args.president_role]
    commands = {
        args.boss_role: args.boss_cmd,
        args.president_role: args.president_cmd,
    }
    print(f"opening two-cli test layout group={ui_group_id}")
    try:
        await _launch_role_layout(connection, repo=repo, roles=roles, commands=commands, ui_group_id=ui_group_id)
        await asyncio.sleep(max(0.0, float(args.startup_wait)))
        boss = await _find_mesh_pane(app, repo, args.boss_role, ui_group_id)
        president = await _find_mesh_pane(app, repo, args.president_role, ui_group_id)
        print("waiting for CLI prompts")
        await _wait_for_cli_ready(
            boss.session,
            role=args.boss_role,
            command_text=args.boss_cmd,
            timeout=args.startup_timeout,
            poll_interval=args.poll_interval,
        )
        await _wait_for_cli_ready(
            president.session,
            role=args.president_role,
            command_text=args.president_cmd,
            timeout=args.startup_timeout,
            poll_interval=args.poll_interval,
        )
        smoke_args = argparse.Namespace(
            repo=repo,
            ui_group_id=ui_group_id,
            boss_role=args.boss_role,
            president_role=args.president_role,
            gemini_model=args.gemini_model,
            run_id="",
            response_timeout=args.response_timeout,
            poll_interval=args.poll_interval,
        )
        return await _run_two_cli_smoke(app, smoke_args)
    finally:
        if not args.keep_open:
            closed = await _close_mesh_tabs(app, repo, ui_group_id)
            print(f"closed {closed} test tab(s) group={ui_group_id}")


async def _run_team_smoke(app: Any, args: argparse.Namespace) -> int:
    ui_group_id = str(getattr(args, "ui_group_id", "") or "").strip()
    boss = await _find_mesh_pane(app, args.repo, args.boss_role, ui_group_id)
    president = await _find_mesh_pane(app, args.repo, args.president_role, ui_group_id)
    worker = await _find_mesh_pane(app, args.repo, args.worker_role, ui_group_id)

    run_id = str(args.run_id or uuid.uuid4().hex[:8]).upper().replace("-", "_")
    boss_task = f"BOSS_TASK_{run_id}"
    president_to_worker = f"PRESIDENT_TO_WORKER_{run_id}"
    worker_result = f"WORKER_RESULT_{run_id}"
    president_ack = f"PRESIDENT_SAW_WORKER_{run_id}"
    boss_done = f"BOSS_SAW_PRESIDENT_{run_id}"

    print(
        f"panes: boss=W{boss.window_index} T{boss.tab_index} S{boss.session_index} "
        f"president=W{president.window_index} T{president.tab_index} S{president.session_index} "
        f"worker=W{worker.window_index} T{worker.tab_index} S{worker.session_index}"
    )
    print(f"run_id: {run_id}")

    print("1. Boss emits a task marker")
    await _send_line(
        boss.session,
        f'Rispondi solo con la concatenazione esatta di "BOSS_TASK_" e "{run_id}".',
    )
    await _wait_for_screen_marker(
        boss.session,
        role=args.boss_role,
        marker=boss_task,
        timeout=args.response_timeout,
        poll_interval=args.poll_interval,
    )

    print("2. President receives boss task and emits worker handoff")
    await _send_line(
        president.session,
        (
            f"Task ricevuto da boss: {boss_task}. "
            f'Rispondi solo con la concatenazione esatta di "PRESIDENT_TO_WORKER_" e "{run_id}".'
        ),
    )
    await _wait_for_screen_marker(
        president.session,
        role=args.president_role,
        marker=president_to_worker,
        timeout=args.response_timeout,
        poll_interval=args.poll_interval,
    )

    print("3. Worker receives president handoff and emits result")
    await _send_line(
        worker.session,
        (
            f"Task ricevuto da president: {president_to_worker}. "
            f'Rispondi solo con la concatenazione esatta di "WORKER_RESULT_" e "{run_id}".'
        ),
    )
    await _wait_for_screen_marker(
        worker.session,
        role=args.worker_role,
        marker=worker_result,
        timeout=args.response_timeout,
        poll_interval=args.poll_interval,
    )

    print("4. President receives worker result and acknowledges")
    await _send_line(
        president.session,
        (
            f"Risultato ricevuto da worker: {worker_result}. "
            f'Rispondi solo con la concatenazione esatta di "PRESIDENT_SAW_WORKER_" e "{run_id}".'
        ),
    )
    await _wait_for_screen_marker(
        president.session,
        role=args.president_role,
        marker=president_ack,
        timeout=args.response_timeout,
        poll_interval=args.poll_interval,
    )

    print("5. Boss receives president acknowledgement and closes loop")
    await _send_line(
        boss.session,
        (
            f"Ack ricevuto da president: {president_ack}. "
            f'Rispondi solo con la concatenazione esatta di "BOSS_SAW_PRESIDENT_" e "{run_id}".'
        ),
    )
    await _wait_for_screen_marker(
        boss.session,
        role=args.boss_role,
        marker=boss_done,
        timeout=args.response_timeout,
        poll_interval=args.poll_interval,
    )

    print("success:")
    print(f"  {boss_task}")
    print(f"  {president_to_worker}")
    print(f"  {worker_result}")
    print(f"  {president_ack}")
    print(f"  {boss_done}")
    return 0


async def _run_team_e2e(connection: Any, app: Any, args: argparse.Namespace) -> int:
    _ensure_command(args.boss_cmd)
    _ensure_command(args.president_cmd)
    _ensure_command(args.worker_cmd)

    repo = str(args.repo or "").strip()
    repo_name = _repo_name(repo)
    ui_group_id = str(args.ui_group_id or f"{repo_name}-team-{uuid.uuid4().hex[:8]}").strip()
    roles = [args.boss_role, args.president_role, args.worker_role]
    commands = {
        args.boss_role: args.boss_cmd,
        args.president_role: args.president_cmd,
        args.worker_role: args.worker_cmd,
    }
    print(f"opening team test layout group={ui_group_id}")
    try:
        await _launch_role_layout(connection, repo=repo, roles=roles, commands=commands, ui_group_id=ui_group_id)
        await asyncio.sleep(max(0.0, float(args.startup_wait)))
        panes = {
            args.boss_role: await _find_mesh_pane(app, repo, args.boss_role, ui_group_id),
            args.president_role: await _find_mesh_pane(app, repo, args.president_role, ui_group_id),
            args.worker_role: await _find_mesh_pane(app, repo, args.worker_role, ui_group_id),
        }
        print("waiting for CLI prompts")
        for role, command in commands.items():
            await _wait_for_cli_ready(
                panes[role].session,
                role=role,
                command_text=command,
                timeout=args.startup_timeout,
                poll_interval=args.poll_interval,
            )
        smoke_args = argparse.Namespace(
            repo=repo,
            ui_group_id=ui_group_id,
            boss_role=args.boss_role,
            president_role=args.president_role,
            worker_role=args.worker_role,
            run_id="",
            response_timeout=args.response_timeout,
            poll_interval=args.poll_interval,
        )
        return await _run_team_smoke(app, smoke_args)
    finally:
        if not args.keep_open:
            closed = await _close_mesh_tabs(app, repo, ui_group_id)
            print(f"closed {closed} test tab(s) group={ui_group_id}")


async def _run(connection, args: argparse.Namespace) -> int:
    import iterm2

    app = await iterm2.async_get_app(connection)
    if app is None:
        raise RuntimeError("iTerm2 app not available")

    if args.cmd == "list":
        panes = await _mesh_sessions(app, args.repo, getattr(args, "ui_group_id", ""))
        lines = []
        for pane in panes:
            lines.append(
                f"W{pane.window_index} T{pane.tab_index} S{pane.session_index} "
                f"role={pane.role} repo={pane.repo}"
            )
        _emit("\n".join(lines), args.output)
        return 0

    if args.cmd == "close":
        ui_group_id = getattr(args, "ui_group_id", "")
        closed = await _close_mesh_tabs(app, args.repo, ui_group_id)
        suffix = f" ui_group_id={ui_group_id}" if ui_group_id else ""
        print(f"closed {closed} mesh tab(s) for repo={args.repo}{suffix}")
        return 0

    if args.cmd == "two-cli-smoke":
        return await _run_two_cli_smoke(app, args)

    if args.cmd == "two-cli-e2e":
        return await _run_two_cli_e2e(connection, app, args)

    if args.cmd == "team-e2e":
        return await _run_team_e2e(connection, app, args)

    pane = await _find_mesh_pane(app, args.repo, args.role, getattr(args, "ui_group_id", ""))

    if args.cmd == "focus":
        await pane.session.async_activate()
        print(
            f"focused W{pane.window_index} T{pane.tab_index} S{pane.session_index} "
            f"role={pane.role} repo={pane.repo}"
        )
        return 0
    if args.cmd == "send-text":
        await pane.session.async_activate()
        await pane.session.async_send_text(args.text)
        print(f"sent text to role={pane.role} repo={pane.repo}")
        return 0
    if args.cmd == "send-line":
        await pane.session.async_activate()
        await pane.session.async_send_text(args.text)
        await asyncio.sleep(0.08)
        await pane.session.async_send_text("\r")
        print(f"sent line to role={pane.role} repo={pane.repo}")
        return 0
    if args.cmd == "send-key":
        await pane.session.async_activate()
        await pane.session.async_send_text(_key_text(args.key))
        print(f"sent key {args.key} to role={pane.role} repo={pane.repo}")
        return 0
    if args.cmd == "dump":
        _emit(await _screen_tail(pane.session, lines=args.lines), args.output)
        return 0
    raise RuntimeError(f"unsupported command: {args.cmd}")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    args = _parse_args()
    try:
        import iterm2
    except ImportError:
        raise SystemExit(
            "Error: Python package 'iterm2' not found. Use: uv run --with iterm2 -- python scripts/mesh_iterm_control.py ..."
        )

    try:
        iterm2.run_until_complete(lambda conn: _run(conn, args), retry=_iterm_retry_enabled())
    except Exception as exc:
        raise SystemExit(f"Error: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
