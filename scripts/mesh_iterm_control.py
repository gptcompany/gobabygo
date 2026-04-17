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
import json
import os
import shlex
import shutil
import subprocess
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

    speckit_parser = sub.add_parser("speckit-team-e2e", help="Open and verify a dry-run Speckit role-routing chain.")
    speckit_parser.add_argument("--repo", required=True, help="Exact repo path.")
    speckit_parser.add_argument("--feature", default=os.environ.get("MESH_SPECKIT_FEATURE", "snake-game-demo"))
    speckit_parser.add_argument("--boss-cmd", default=os.environ.get("MESH_TEAM_BOSS_CMD", "claude"))
    speckit_parser.add_argument("--president-cmd", default=os.environ.get("MESH_TEAM_PRESIDENT_CMD", "codex"))
    speckit_parser.add_argument("--worker-cmd", default=os.environ.get("MESH_TEAM_WORKER_CMD", "gemini"))
    speckit_parser.add_argument("--boss-role", default="boss")
    speckit_parser.add_argument("--president-role", default="president")
    speckit_parser.add_argument("--worker-role", default="worker-gemini")
    speckit_parser.add_argument("--ui-group-id", default="", help="Optional mesh UI group id.")
    speckit_parser.add_argument("--startup-wait", type=float, default=12.0, help="Seconds to wait after launching panes.")
    speckit_parser.add_argument("--startup-timeout", type=float, default=120.0, help="Seconds to wait for CLI prompts.")
    speckit_parser.add_argument("--response-timeout", type=float, default=120.0, help="Seconds to wait for each marker.")
    speckit_parser.add_argument("--poll-interval", type=float, default=3.0, help="Seconds between screen polls.")
    speckit_parser.add_argument("--keep-open", action="store_true", help="Leave the test layout open after completion.")

    speckit_run_parser = sub.add_parser("speckit-team-run", help="Run one controlled Speckit team cycle.")
    speckit_run_parser.add_argument("--repo", required=True, help="Exact repo path.")
    speckit_run_parser.add_argument("--feature", required=True, help="Feature or change request.")
    speckit_run_parser.add_argument("--task", default="", help="Optional narrower implementation task.")
    speckit_run_parser.add_argument("--boss-cmd", default=os.environ.get("MESH_TEAM_BOSS_CMD", "claude"))
    speckit_run_parser.add_argument("--president-cmd", default=os.environ.get("MESH_TEAM_PRESIDENT_CMD", "codex"))
    speckit_run_parser.add_argument("--worker-cmd", default=os.environ.get("MESH_TEAM_WORKER_CMD", "gemini"))
    speckit_run_parser.add_argument("--boss-role", default="boss")
    speckit_run_parser.add_argument("--president-role", default="president")
    speckit_run_parser.add_argument("--worker-role", default="worker-gemini")
    speckit_run_parser.add_argument("--ui-group-id", default="", help="Optional mesh UI group id.")
    speckit_run_parser.add_argument("--allow-write", action="store_true", help="Allow the worker to edit files.")
    speckit_run_parser.add_argument("--allow-dirty", action="store_true", help="Run even if the repo is already dirty.")
    speckit_run_parser.add_argument(
        "--handoff-dir",
        default=".mesh/runs",
        help="Repo-relative directory for persistent Speckit handoff JSON files.",
    )
    speckit_run_parser.add_argument("--no-handoff", action="store_true", help="Do not write persistent handoff JSON files.")
    speckit_run_parser.add_argument(
        "--auto-approve-prompts",
        action="store_true",
        help="Auto-answer known CLI trust/write prompts during this run; requires --allow-write.",
    )
    speckit_run_parser.add_argument(
        "--auto-approve-edit-path",
        action="append",
        default=[],
        help="When auto-approving edit prompts, allow only this repo-relative path. Repeatable.",
    )
    speckit_run_parser.add_argument("--test-command", default="", help="Optional local test command to run after worker returns.")
    speckit_run_parser.add_argument("--test-timeout", type=float, default=180.0, help="Seconds for the optional test command.")
    speckit_run_parser.add_argument("--max-turns", type=int, default=1, help="Maximum response turns per role in this controlled cycle.")
    speckit_run_parser.add_argument("--startup-wait", type=float, default=12.0, help="Seconds to wait after launching panes.")
    speckit_run_parser.add_argument("--startup-timeout", type=float, default=120.0, help="Seconds to wait for CLI prompts.")
    speckit_run_parser.add_argument("--response-timeout", type=float, default=300.0, help="Seconds to wait for each role response.")
    speckit_run_parser.add_argument("--poll-interval", type=float, default=3.0, help="Seconds between screen polls.")
    speckit_run_parser.add_argument("--keep-open", action="store_true", help="Leave the run layout open after completion.")

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
    zsh = shutil.which("zsh") or "/bin/zsh"
    inner = f"source ~/.zshrc >/dev/null 2>&1; cd {shlex.quote(str(repo or ''))} && exec {command_text}"
    return f"exec {shlex.quote(zsh)} -lc {shlex.quote(inner)}"


def _clean_one_line(value: object) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ")
    return " ".join(text.split())


def _format_mesh_msg(**fields: object) -> str:
    parts = ["MESH_MSG"]
    for key, value in fields.items():
        parts.append(f"{key}={shlex.quote(_clean_one_line(value))}")
    parts.append("END_MESH_MSG")
    return " ".join(parts)


def _repo_relative_path(repo: str, path: Path) -> str:
    repo_path = Path(repo).resolve()
    try:
        return str(path.resolve().relative_to(repo_path))
    except ValueError:
        return str(path)


def _handoff_run_dir(repo: str, handoff_dir: str, run_id: str) -> Path:
    base = Path(str(handoff_dir or ".mesh/runs"))
    if not base.is_absolute():
        base = Path(repo) / base
    return base / run_id


def _write_handoff_json(
    repo: str,
    handoff_dir: str,
    run_id: str,
    filename: str,
    payload: dict[str, object],
    *,
    enabled: bool = True,
) -> str:
    if not enabled:
        return ""
    run_dir = _handoff_run_dir(repo, handoff_dir, run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    target = run_dir / filename
    data = {
        "schema": "mesh.speckit.handoff.v1",
        "run_id": run_id,
        **payload,
    }
    target.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return _repo_relative_path(repo, target)


def _turn_limit_text(max_turns: int) -> str:
    turns = max(1, int(max_turns or 1))
    return (
        f"Turn budget: massimo {turns} risposta/e per questo ruolo in questo ciclo. "
        "Non aprire sotto-dialoghi; chiudi la tua risposta con il marker richiesto."
    )


def _run_local_capture(args: list[str], *, cwd: str, timeout: float = 30.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
        timeout=max(1.0, float(timeout)),
    )


def _git_status_short(repo: str) -> str:
    proc = _run_local_capture(["git", "status", "--short"], cwd=repo)
    if proc.returncode != 0:
        return f"[git status failed]\n{proc.stderr.strip() or proc.stdout.strip()}"
    return proc.stdout.strip()


def _git_diff_stat(repo: str) -> str:
    proc = _run_local_capture(["git", "diff", "--stat"], cwd=repo)
    if proc.returncode != 0:
        return f"[git diff --stat failed]\n{proc.stderr.strip() or proc.stdout.strip()}"
    return proc.stdout.strip()


def _run_optional_test_command(repo: str, command_text: str, timeout: float) -> tuple[str, str]:
    command = str(command_text or "").strip()
    if not command:
        return "skipped", ""
    try:
        proc = _run_local_capture(shlex.split(command), cwd=repo, timeout=timeout)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return "failed", str(exc)
    output = "\n".join(part for part in (proc.stdout.strip(), proc.stderr.strip()) if part)
    return ("passed" if proc.returncode == 0 else "failed"), output[-4000:]


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


def _auto_approval_choice(screen_text: str) -> tuple[str, str]:
    lower = screen_text.lower()
    compact = "".join(ch for ch in lower if ch.isalnum())
    if "apply this change?" in lower:
        return "1", "apply change once"
    if "allow execution of" in lower:
        return "2", "allow command for session"
    if (
        (
            "do you trust" in lower
            or "trust this folder" in lower
            or "trust the files" in lower
            or "doyoutrust" in compact
            or "trustthecontentsofthisdirectory" in compact
        )
        and ("1." in screen_text or "1 " in screen_text)
    ):
        return "1", "trust folder"
    return "", ""


def _auto_approval_edit_path(screen_text: str) -> str:
    for line in screen_text.splitlines():
        lowered = line.lower()
        idx = lowered.find("edit ")
        if idx < 0 or ":" not in line[idx:]:
            continue
        value = line[idx + len("edit ") :].split(":", 1)[0]
        return value.strip().strip("'\"`")
    return ""


def _normalize_edit_path(path: str) -> str:
    value = str(path or "").strip().strip("'\"`")
    while value.startswith("./"):
        value = value[2:]
    return value


def _edit_path_allowed(path: str, allowed_paths: tuple[str, ...]) -> bool:
    if not allowed_paths:
        return True
    normalized = _normalize_edit_path(path)
    for allowed in allowed_paths:
        item = _normalize_edit_path(allowed)
        if not item:
            continue
        if normalized == item:
            return True
        if item.endswith("/") and normalized.startswith(item):
            return True
    return False


def _auto_approval_signature(screen_text: str, choice: str, reason: str) -> str:
    if reason == "trust folder":
        return f"{choice}:{reason}"
    if reason.startswith("reject edit outside allowlist:"):
        return f"{choice}:{reason}"
    if reason == "apply change once":
        edit_path = _auto_approval_edit_path(screen_text)
        if edit_path:
            prompt_tail = "\n".join(screen_text.splitlines()[-80:])
            return f"{choice}:{reason}:{_normalize_edit_path(edit_path).lower()}:{prompt_tail}"
    if reason == "allow command for session":
        for line in reversed(screen_text.splitlines()):
            if "allow execution of" in line.lower():
                return f"{choice}:{reason}:{line.strip().lower()}"
        return f"{choice}:{reason}"
    return f"{choice}:{reason}:{chr(10).join(screen_text.splitlines()[-12:])}"


async def _maybe_auto_approve_prompt(
    session: Any,
    screen_text: str,
    *,
    role: str,
    enabled: bool,
    seen: set[str],
    allowed_edit_paths: tuple[str, ...] = (),
) -> bool:
    if not enabled:
        return False
    choice, reason = _auto_approval_choice(screen_text)
    if not choice:
        return False
    if reason == "apply change once":
        edit_path = _auto_approval_edit_path(screen_text)
        if edit_path and not _edit_path_allowed(edit_path, allowed_edit_paths):
            choice = "4"
            reason = f"reject edit outside allowlist: {_normalize_edit_path(edit_path)}"
    signature = _auto_approval_signature(screen_text, choice, reason)
    if signature in seen:
        return False
    seen.add(signature)
    print(f"auto-approve {role}: {reason} -> {choice}")
    await _send_line(session, choice)
    await asyncio.sleep(1.0)
    return True


async def _wait_for_screen_marker(
    session: Any,
    *,
    role: str,
    marker: str,
    timeout: float,
    poll_interval: float,
    auto_approve_prompts: bool = False,
    allowed_edit_paths: tuple[str, ...] = (),
) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(1.0, float(timeout))
    last_dump = ""
    seen_auto_approvals: set[str] = set()
    while loop.time() < deadline:
        last_dump = await _screen_tail(session, lines=360)
        if marker in last_dump:
            return
        if await _maybe_auto_approve_prompt(
            session,
            last_dump,
            role=role,
            enabled=auto_approve_prompts,
            seen=seen_auto_approvals,
            allowed_edit_paths=allowed_edit_paths,
        ):
            continue
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
    auto_approve_prompts: bool = False,
    allowed_edit_paths: tuple[str, ...] = (),
) -> str:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(1.0, float(timeout))
    last_dump = ""
    seen_auto_approvals: set[str] = set()
    while loop.time() < deadline:
        last_dump = await _screen_tail(session, lines=360)
        if auto_approve_prompts and _auto_approval_choice(last_dump)[0]:
            await _maybe_auto_approve_prompt(
                session,
                last_dump,
                role=role,
                enabled=True,
                seen=seen_auto_approvals,
                allowed_edit_paths=allowed_edit_paths,
            )
            await asyncio.sleep(max(0.5, float(poll_interval)))
            continue
        for marker in markers:
            if marker and marker in last_dump:
                return marker
        await asyncio.sleep(max(0.5, float(poll_interval)))
    print(f"--- last dump for {role} ---")
    print(last_dump)
    raise RuntimeError(f"timed out waiting for {description} in role={role}")


async def _wait_for_cli_ready(
    session: Any,
    *,
    role: str,
    command_text: str,
    timeout: float,
    poll_interval: float,
    auto_approve_prompts: bool = False,
    allowed_edit_paths: tuple[str, ...] = (),
) -> None:
    command_name = str(command_text or "").strip().split(" ", 1)[0]
    if command_name == "gemini":
        await _wait_for_screen_any(
            session,
            role=role,
            markers=("Type your message",),
            timeout=timeout,
            poll_interval=poll_interval,
            description="Gemini prompt",
            auto_approve_prompts=auto_approve_prompts,
            allowed_edit_paths=allowed_edit_paths,
        )
    elif command_name == "codex":
        await _wait_for_screen_any(
            session,
            role=role,
            markers=("Write tests for", "›"),
            timeout=timeout,
            poll_interval=poll_interval,
            description="Codex prompt",
            auto_approve_prompts=auto_approve_prompts,
            allowed_edit_paths=allowed_edit_paths,
        )
    elif command_name == "claude":
        await _wait_for_screen_any(
            session,
            role=role,
            markers=("cwd:", "cwd", "Claude", ">"),
            timeout=timeout,
            poll_interval=poll_interval,
            description="Claude prompt",
            auto_approve_prompts=auto_approve_prompts,
            allowed_edit_paths=allowed_edit_paths,
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


async def _run_speckit_team_smoke(app: Any, args: argparse.Namespace) -> int:
    ui_group_id = str(getattr(args, "ui_group_id", "") or "").strip()
    boss = await _find_mesh_pane(app, args.repo, args.boss_role, ui_group_id)
    president = await _find_mesh_pane(app, args.repo, args.president_role, ui_group_id)
    worker = await _find_mesh_pane(app, args.repo, args.worker_role, ui_group_id)

    run_id = str(args.run_id or uuid.uuid4().hex[:8]).upper().replace("-", "_")
    feature = str(getattr(args, "feature", "") or "feature").strip()
    discuss = f"SPECKIT_DISCUSS_TO_PRESIDENT_{run_id}"
    analyze = f"SPECKIT_ANALYZE_TO_WORKER_{run_id}"
    implement = f"SPECKIT_IMPLEMENT_RESULT_{run_id}"
    adjudicate = f"SPECKIT_PRESIDENT_READY_{run_id}"
    done = f"SPECKIT_BOSS_DONE_{run_id}"

    print(
        f"panes: boss=W{boss.window_index} T{boss.tab_index} S{boss.session_index} "
        f"president=W{president.window_index} T{president.tab_index} S{president.session_index} "
        f"worker=W{worker.window_index} T{worker.tab_index} S{worker.session_index}"
    )
    print(f"feature: {feature}")
    print(f"run_id: {run_id}")

    print("1. Boss maps /speckit.discuss to president handoff")
    await _send_line(
        boss.session,
        (
            "Dry-run routing smoke only: do not inspect files and do not edit files. "
            f"For feature '{feature}', treat this as /speckit.discuss. "
            "Your role is boss. If the next role should be president for analysis coordination, "
            f'rispondi solo con la concatenazione esatta di "SPECKIT_DISCUSS_TO_PRESIDENT_" e "{run_id}".'
        ),
    )
    await _wait_for_screen_marker(
        boss.session,
        role=args.boss_role,
        marker=discuss,
        timeout=args.response_timeout,
        poll_interval=args.poll_interval,
    )

    print("2. President maps speckit.analyze to worker handoff")
    await _send_line(
        president.session,
        (
            "Dry-run routing smoke only: do not inspect files and do not edit files. "
            f"Boss handoff received for feature '{feature}': {discuss}. "
            "Your role is president. Treat this as speckit.analyze and hand work to worker-gemini. "
            f'Rispondi solo con la concatenazione esatta di "SPECKIT_ANALYZE_TO_WORKER_" e "{run_id}".'
        ),
    )
    await _wait_for_screen_marker(
        president.session,
        role=args.president_role,
        marker=analyze,
        timeout=args.response_timeout,
        poll_interval=args.poll_interval,
    )

    print("3. Worker maps speckit.implement to implementation result")
    await _send_line(
        worker.session,
        (
            "Dry-run routing smoke only: do not inspect files and do not edit files. "
            f"President handoff received for feature '{feature}': {analyze}. "
            "Your role is worker-gemini. Treat this as speckit.implement dry-run execution. "
            f'Rispondi solo con la concatenazione esatta di "SPECKIT_IMPLEMENT_RESULT_" e "{run_id}".'
        ),
    )
    await _wait_for_screen_marker(
        worker.session,
        role=args.worker_role,
        marker=implement,
        timeout=args.response_timeout,
        poll_interval=args.poll_interval,
    )

    print("4. President adjudicates worker result")
    await _send_line(
        president.session,
        (
            "Dry-run routing smoke only. "
            f"Worker result received for feature '{feature}': {implement}. "
            "Your role is president. Treat this as Speckit readiness adjudication. "
            f'Rispondi solo con la concatenazione esatta di "SPECKIT_PRESIDENT_READY_" e "{run_id}".'
        ),
    )
    await _wait_for_screen_marker(
        president.session,
        role=args.president_role,
        marker=adjudicate,
        timeout=args.response_timeout,
        poll_interval=args.poll_interval,
    )

    print("5. Boss closes Speckit routing loop")
    await _send_line(
        boss.session,
        (
            "Dry-run routing smoke only. "
            f"President readiness received for feature '{feature}': {adjudicate}. "
            "Your role is boss. Close the Speckit routing loop. "
            f'Rispondi solo con la concatenazione esatta di "SPECKIT_BOSS_DONE_" e "{run_id}".'
        ),
    )
    await _wait_for_screen_marker(
        boss.session,
        role=args.boss_role,
        marker=done,
        timeout=args.response_timeout,
        poll_interval=args.poll_interval,
    )

    print("success:")
    print(f"  {discuss}")
    print(f"  {analyze}")
    print(f"  {implement}")
    print(f"  {adjudicate}")
    print(f"  {done}")
    return 0


async def _run_speckit_team_e2e(connection: Any, app: Any, args: argparse.Namespace) -> int:
    _ensure_command(args.boss_cmd)
    _ensure_command(args.president_cmd)
    _ensure_command(args.worker_cmd)

    repo = str(args.repo or "").strip()
    repo_name = _repo_name(repo)
    ui_group_id = str(args.ui_group_id or f"{repo_name}-speckit-{uuid.uuid4().hex[:8]}").strip()
    roles = [args.boss_role, args.president_role, args.worker_role]
    commands = {
        args.boss_role: args.boss_cmd,
        args.president_role: args.president_cmd,
        args.worker_role: args.worker_cmd,
    }
    print(f"opening speckit team test layout group={ui_group_id}")
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
            feature=args.feature,
            run_id="",
            response_timeout=args.response_timeout,
            poll_interval=args.poll_interval,
        )
        return await _run_speckit_team_smoke(app, smoke_args)
    finally:
        if not args.keep_open:
            closed = await _close_mesh_tabs(app, repo, ui_group_id)
            print(f"closed {closed} test tab(s) group={ui_group_id}")


async def _run_speckit_team_cycle(app: Any, args: argparse.Namespace) -> int:
    ui_group_id = str(getattr(args, "ui_group_id", "") or "").strip()
    boss = await _find_mesh_pane(app, args.repo, args.boss_role, ui_group_id)
    president = await _find_mesh_pane(app, args.repo, args.president_role, ui_group_id)
    worker = await _find_mesh_pane(app, args.repo, args.worker_role, ui_group_id)

    run_id = str(args.run_id or uuid.uuid4().hex[:8]).upper().replace("-", "_")
    feature = _clean_one_line(args.feature)
    task = _clean_one_line(args.task) or feature
    turn_limit = _turn_limit_text(args.max_turns)
    write_allowed = "true" if bool(args.allow_write) else "false"
    auto_approve_prompts = bool(getattr(args, "auto_approve_prompts", False))
    allowed_edit_paths = tuple(str(item) for item in (getattr(args, "auto_approve_edit_path", None) or []))
    handoff_enabled = not bool(getattr(args, "no_handoff", False))
    handoff_dir = str(getattr(args, "handoff_dir", ".mesh/runs") or ".mesh/runs")

    boss_delegated = f"SPECKIT_RUN_BOSS_DELEGATED_{run_id}"
    president_assigned = f"SPECKIT_RUN_PRESIDENT_ASSIGNED_{run_id}"
    worker_done = f"SPECKIT_RUN_WORKER_DONE_{run_id}"
    president_reviewed = f"SPECKIT_RUN_PRESIDENT_REVIEWED_{run_id}"
    boss_reported = f"SPECKIT_RUN_BOSS_REPORTED_{run_id}"
    handoff_files = {
        "operator": "00-operator.json",
        "discuss": "01-discuss.json",
        "analyze": "02-analyze.json",
        "implement": "03-implement.json",
        "verify": "04-verify.json",
        "report": "05-report.json",
    }

    print(
        f"panes: boss=W{boss.window_index} T{boss.tab_index} S{boss.session_index} "
        f"president=W{president.window_index} T{president.tab_index} S{president.session_index} "
        f"worker=W{worker.window_index} T{worker.tab_index} S{worker.session_index}"
    )
    print(f"feature: {feature}")
    print(f"task: {task}")
    print(f"run_id: {run_id}")
    print(f"write_allowed: {write_allowed}")
    if handoff_enabled:
        print(f"handoff_dir: {_repo_relative_path(args.repo, _handoff_run_dir(args.repo, handoff_dir, run_id))}")

    operator_handoff = _write_handoff_json(
        args.repo,
        handoff_dir,
        run_id,
        handoff_files["operator"],
        {
            "phase": "speckit.request",
            "from_role": "operator",
            "to_role": args.boss_role,
            "feature": feature,
            "task": task,
            "write_allowed": bool(args.allow_write),
            "test_command": args.test_command,
            "max_turns": max(1, int(args.max_turns or 1)),
            "roles": {
                "boss": args.boss_role,
                "president": args.president_role,
                "worker": args.worker_role,
            },
            "next_handoff": str(Path(handoff_dir) / run_id / handoff_files["discuss"]),
        },
        enabled=handoff_enabled,
    )

    operator_msg = _format_mesh_msg(
        id=f"operator-{run_id}",
        from_role="operator",
        to_role=args.boss_role,
        phase="speckit.discuss",
        feature=feature,
        task=task,
        write_allowed=write_allowed,
        handoff_in=operator_handoff,
        handoff_out=str(Path(handoff_dir) / run_id / handoff_files["discuss"]) if handoff_enabled else "",
        done_criteria="one controlled Speckit cycle; no commit",
    )

    print("1. Boss discusses and delegates to president")
    await _send_line(
        boss.session,
        (
            f"{turn_limit} You are boss for a controlled Speckit run. "
            f"Input: {operator_msg}. "
            "Produce a concise routing decision for president. "
            f'End with only the concatenation of "SPECKIT_RUN_BOSS_DELEGATED_" and "{run_id}" on its own final line.'
        ),
    )
    await _wait_for_screen_marker(
        boss.session,
        role=args.boss_role,
        marker=boss_delegated,
        timeout=args.response_timeout,
        poll_interval=args.poll_interval,
        auto_approve_prompts=auto_approve_prompts,
        allowed_edit_paths=allowed_edit_paths,
    )
    boss_tail = await _screen_tail(boss.session, lines=120)
    discuss_handoff = _write_handoff_json(
        args.repo,
        handoff_dir,
        run_id,
        handoff_files["discuss"],
        {
            "phase": "speckit.discuss",
            "from_role": args.boss_role,
            "to_role": args.president_role,
            "feature": feature,
            "task": task,
            "marker": boss_delegated,
            "handoff_in": operator_handoff,
            "next_handoff": str(Path(handoff_dir) / run_id / handoff_files["analyze"]),
            "screen_tail": boss_tail,
        },
        enabled=handoff_enabled,
    )

    president_msg = _format_mesh_msg(
        id=f"boss-{run_id}",
        from_role=args.boss_role,
        to_role=args.president_role,
        phase="speckit.analyze",
        feature=feature,
        task=task,
        write_allowed=write_allowed,
        upstream_marker=boss_delegated,
        handoff_in=discuss_handoff,
        handoff_out=str(Path(handoff_dir) / run_id / handoff_files["analyze"]) if handoff_enabled else "",
        done_criteria="assign exactly one worker task",
    )

    print("2. President analyzes and assigns worker")
    await _send_line(
        president.session,
        (
            f"{turn_limit} You are president for a controlled Speckit run. "
            f"Input: {president_msg}. "
            "Analyze the request and assign exactly one scoped task to worker-gemini. "
            f'End with only the concatenation of "SPECKIT_RUN_PRESIDENT_ASSIGNED_" and "{run_id}" on its own final line.'
        ),
    )
    await _wait_for_screen_marker(
        president.session,
        role=args.president_role,
        marker=president_assigned,
        timeout=args.response_timeout,
        poll_interval=args.poll_interval,
        auto_approve_prompts=auto_approve_prompts,
        allowed_edit_paths=allowed_edit_paths,
    )
    president_tail = await _screen_tail(president.session, lines=120)
    analyze_handoff = _write_handoff_json(
        args.repo,
        handoff_dir,
        run_id,
        handoff_files["analyze"],
        {
            "phase": "speckit.analyze",
            "from_role": args.president_role,
            "to_role": args.worker_role,
            "feature": feature,
            "task": task,
            "marker": president_assigned,
            "upstream_marker": boss_delegated,
            "handoff_in": discuss_handoff,
            "next_handoff": str(Path(handoff_dir) / run_id / handoff_files["implement"]),
            "screen_tail": president_tail,
        },
        enabled=handoff_enabled,
    )

    worker_msg = _format_mesh_msg(
        id=f"president-{run_id}",
        from_role=args.president_role,
        to_role=args.worker_role,
        phase="speckit.implement",
        feature=feature,
        task=task,
        write_allowed=write_allowed,
        upstream_marker=president_assigned,
        handoff_in=analyze_handoff,
        handoff_out=str(Path(handoff_dir) / run_id / handoff_files["implement"]) if handoff_enabled else "",
        done_criteria="single implementation pass; summarize files/tests/risks; no commit",
    )
    write_policy = (
        "File edits are allowed for this one task. Do not commit."
        if args.allow_write
        else "Do not edit files. Produce an implementation plan and risk note only."
    )

    print("3. Worker executes one bounded implementation pass")
    await _send_line(
        worker.session,
        (
            f"{turn_limit} You are worker-gemini for a controlled Speckit run. "
            f"{write_policy} Input: {worker_msg}. "
            "When done, summarize changed files or planned files, tests run or skipped, and residual risks. "
            f'End with only the concatenation of "SPECKIT_RUN_WORKER_DONE_" and "{run_id}" on its own final line.'
        ),
    )
    await _wait_for_screen_marker(
        worker.session,
        role=args.worker_role,
        marker=worker_done,
        timeout=args.response_timeout,
        poll_interval=args.poll_interval,
        auto_approve_prompts=auto_approve_prompts,
        allowed_edit_paths=allowed_edit_paths,
    )

    status_after_worker = _git_status_short(args.repo)
    diff_stat_after_worker = _git_diff_stat(args.repo)
    test_status, test_output = _run_optional_test_command(args.repo, args.test_command, args.test_timeout)
    worker_tail = await _screen_tail(worker.session, lines=120)
    implement_handoff = _write_handoff_json(
        args.repo,
        handoff_dir,
        run_id,
        handoff_files["implement"],
        {
            "phase": "speckit.implement",
            "from_role": args.worker_role,
            "to_role": args.president_role,
            "feature": feature,
            "task": task,
            "write_allowed": bool(args.allow_write),
            "marker": worker_done,
            "upstream_marker": president_assigned,
            "handoff_in": analyze_handoff,
            "next_handoff": str(Path(handoff_dir) / run_id / handoff_files["verify"]),
            "git_status": status_after_worker or "clean",
            "diff_stat": diff_stat_after_worker or "empty",
            "test_status": test_status,
            "test_output_tail": test_output,
            "screen_tail": worker_tail,
        },
        enabled=handoff_enabled,
    )

    review_msg = _format_mesh_msg(
        id=f"worker-{run_id}",
        from_role=args.worker_role,
        to_role=args.president_role,
        phase="speckit.verify-work",
        feature=feature,
        task=task,
        write_allowed=write_allowed,
        upstream_marker=worker_done,
        git_status=status_after_worker or "clean",
        diff_stat=diff_stat_after_worker or "empty",
        test_status=test_status,
        handoff_in=implement_handoff,
        handoff_out=str(Path(handoff_dir) / run_id / handoff_files["verify"]) if handoff_enabled else "",
        done_criteria="adjudicate ready_or_blocked",
    )

    print("4. President reviews worker result")
    await _send_line(
        president.session,
        (
            f"{turn_limit} You are president reviewing a controlled Speckit run. "
            f"Input: {review_msg}. "
            "Adjudicate whether the cycle is ready or blocked based only on the provided status. "
            f'End with only the concatenation of "SPECKIT_RUN_PRESIDENT_REVIEWED_" and "{run_id}" on its own final line.'
        ),
    )
    await _wait_for_screen_marker(
        president.session,
        role=args.president_role,
        marker=president_reviewed,
        timeout=args.response_timeout,
        poll_interval=args.poll_interval,
        auto_approve_prompts=auto_approve_prompts,
        allowed_edit_paths=allowed_edit_paths,
    )
    review_tail = await _screen_tail(president.session, lines=120)
    verify_handoff = _write_handoff_json(
        args.repo,
        handoff_dir,
        run_id,
        handoff_files["verify"],
        {
            "phase": "speckit.verify-work",
            "from_role": args.president_role,
            "to_role": args.boss_role,
            "feature": feature,
            "task": task,
            "marker": president_reviewed,
            "upstream_marker": worker_done,
            "handoff_in": implement_handoff,
            "next_handoff": str(Path(handoff_dir) / run_id / handoff_files["report"]),
            "git_status": status_after_worker or "clean",
            "diff_stat": diff_stat_after_worker or "empty",
            "test_status": test_status,
            "screen_tail": review_tail,
        },
        enabled=handoff_enabled,
    )

    final_msg = _format_mesh_msg(
        id=f"president-review-{run_id}",
        from_role=args.president_role,
        to_role=args.boss_role,
        phase="speckit.report",
        feature=feature,
        task=task,
        write_allowed=write_allowed,
        upstream_marker=president_reviewed,
        git_status=status_after_worker or "clean",
        diff_stat=diff_stat_after_worker or "empty",
        test_status=test_status,
        handoff_in=verify_handoff,
        handoff_out=str(Path(handoff_dir) / run_id / handoff_files["report"]) if handoff_enabled else "",
        done_criteria="operator-facing summary",
    )

    print("5. Boss reports final controlled-cycle status")
    await _send_line(
        boss.session,
        (
            f"{turn_limit} You are boss closing a controlled Speckit run. "
            f"Input: {final_msg}. "
            "Give a concise operator-facing summary. Do not claim a commit was made. "
            f'End with only the concatenation of "SPECKIT_RUN_BOSS_REPORTED_" and "{run_id}" on its own final line.'
        ),
    )
    await _wait_for_screen_marker(
        boss.session,
        role=args.boss_role,
        marker=boss_reported,
        timeout=args.response_timeout,
        poll_interval=args.poll_interval,
        auto_approve_prompts=auto_approve_prompts,
        allowed_edit_paths=allowed_edit_paths,
    )
    report_tail = await _screen_tail(boss.session, lines=120)
    report_handoff = _write_handoff_json(
        args.repo,
        handoff_dir,
        run_id,
        handoff_files["report"],
        {
            "phase": "speckit.report",
            "from_role": args.boss_role,
            "to_role": "operator",
            "feature": feature,
            "task": task,
            "marker": boss_reported,
            "upstream_marker": president_reviewed,
            "handoff_in": verify_handoff,
            "git_status": status_after_worker or "clean",
            "diff_stat": diff_stat_after_worker or "empty",
            "test_status": test_status,
            "screen_tail": report_tail,
        },
        enabled=handoff_enabled,
    )

    print("success:")
    print(f"  {boss_delegated}")
    print(f"  {president_assigned}")
    print(f"  {worker_done}")
    print(f"  {president_reviewed}")
    print(f"  {boss_reported}")
    print("git_status_after:")
    print(status_after_worker or "clean")
    print("diff_stat_after:")
    print(diff_stat_after_worker or "empty")
    print(f"test_status: {test_status}")
    if report_handoff:
        print(f"handoff_report: {report_handoff}")
    if test_output:
        print("test_output_tail:")
        print(test_output)
    return 0


async def _run_speckit_team_run(connection: Any, app: Any, args: argparse.Namespace) -> int:
    _ensure_command(args.boss_cmd)
    _ensure_command(args.president_cmd)
    _ensure_command(args.worker_cmd)
    if args.auto_approve_prompts and not args.allow_write:
        raise RuntimeError("--auto-approve-prompts requires --allow-write")

    repo = str(args.repo or "").strip()
    repo_path = Path(repo)
    if not repo_path.is_dir():
        raise RuntimeError(f"repo path does not exist or is not a directory: {repo}")
    status_before = _git_status_short(repo)
    if status_before and not args.allow_dirty:
        raise RuntimeError(
            "target repo is dirty; commit/stash changes or pass --allow-dirty\n"
            f"{status_before}"
        )

    repo_name = _repo_name(repo)
    ui_group_id = str(args.ui_group_id or f"{repo_name}-speckit-run-{uuid.uuid4().hex[:8]}").strip()
    roles = [args.boss_role, args.president_role, args.worker_role]
    commands = {
        args.boss_role: args.boss_cmd,
        args.president_role: args.president_cmd,
        args.worker_role: args.worker_cmd,
    }
    print(f"opening speckit team run layout group={ui_group_id}")
    print("git_status_before:")
    print(status_before or "clean")
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
                auto_approve_prompts=args.auto_approve_prompts,
                allowed_edit_paths=tuple(str(item) for item in (args.auto_approve_edit_path or [])),
            )
        cycle_args = argparse.Namespace(
            repo=repo,
            ui_group_id=ui_group_id,
            boss_role=args.boss_role,
            president_role=args.president_role,
            worker_role=args.worker_role,
            feature=args.feature,
            task=args.task,
            allow_write=args.allow_write,
            test_command=args.test_command,
            test_timeout=args.test_timeout,
            max_turns=args.max_turns,
            handoff_dir=args.handoff_dir,
            no_handoff=args.no_handoff,
            run_id="",
            response_timeout=args.response_timeout,
            poll_interval=args.poll_interval,
            auto_approve_prompts=args.auto_approve_prompts,
            auto_approve_edit_path=args.auto_approve_edit_path,
        )
        return await _run_speckit_team_cycle(app, cycle_args)
    finally:
        if not args.keep_open:
            closed = await _close_mesh_tabs(app, repo, ui_group_id)
            print(f"closed {closed} run tab(s) group={ui_group_id}")


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

    if args.cmd == "speckit-team-e2e":
        return await _run_speckit_team_e2e(connection, app, args)

    if args.cmd == "speckit-team-run":
        return await _run_speckit_team_run(connection, app, args)

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
