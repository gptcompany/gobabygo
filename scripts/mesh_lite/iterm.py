#!/usr/bin/env python3
"""Small iTerm2 helpers for Mesh Lite work.

This module is intentionally narrow:
- enumerate visible iTerm2 sessions
- read session metadata such as tty and title
- send text or a submitted line to a specific live session

It does not contain routing or workflow logic.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from dataclasses import dataclass
from typing import Any


@dataclass
class SessionInfo:
    session_id: str
    window_index: int
    tab_index: int
    session_index: int
    tty: str
    title: str
    badge: str
    command: str


SAFE_FOREGROUND = {
    "bash",
    "zsh",
    "fish",
    "sh",
    "claude",
    "codex",
    "gemini",
    "ccs",
}


def _clean(text: Any) -> str:
    return str(text or "").replace("\x00", "").strip()


def _osascript(script: str) -> str:
    script_lines = [line for line in str(script).splitlines() if line.strip()]
    args = ["osascript"]
    if script_lines:
        for line in script_lines:
            args.extend(["-e", line])
    else:
        args.extend(["-e", str(script)])
    proc = subprocess.run(
        args,
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return ""
    return _clean(proc.stdout)


def _apple_quote(text: str) -> str:
    return str(text or "").replace("\\", "\\\\").replace('"', '\\"')


def _apple_string_expr(text: str) -> str:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    parts = normalized.split("\n")
    if not parts:
        return '""'
    return " & linefeed & ".join(f'"{_apple_quote(part)}"' for part in parts)


def _tty_via_osascript(session_id: str) -> str:
    script = f'''
tell application "iTerm2"
  repeat with aWindow in windows
    repeat with aTab in tabs of aWindow
      repeat with aSession in sessions of aTab
        if id of aSession is "{session_id}" then
          return tty of aSession
        end if
      end repeat
    end repeat
  end repeat
end tell
'''
    return _osascript(script)


def _send_line_via_osascript(session_id: str, text: str) -> None:
    safe_text = _apple_string_expr(text)
    script = f'''
tell application "iTerm2"
  repeat with aWindow in windows
    repeat with aTab in tabs of aWindow
      repeat with aSession in sessions of aTab
        if id of aSession is "{session_id}" then
          tell aSession to write text {safe_text}
          return
        end if
      end repeat
    end repeat
  end repeat
end tell
'''
    _osascript(script)


async def _list_sessions(connection) -> list[SessionInfo]:
    import iterm2

    app = await iterm2.async_get_app(connection)
    if app is None:
        raise RuntimeError("iTerm2 app not available")

    sessions: list[SessionInfo] = []
    for wi, window in enumerate(getattr(app, "windows", []), 1):
        for ti, tab in enumerate(getattr(window, "tabs", []), 1):
            for si, session in enumerate(getattr(tab, "sessions", []), 1):
                title = ""
                badge = ""
                tty = ""
                try:
                    title = _clean(await session.async_get_variable("session.name"))
                except Exception:
                    pass
                try:
                    badge = _clean(await session.async_get_variable("session.badge"))
                except Exception:
                    pass
                try:
                    tty = _clean(session.tty)
                except Exception:
                    tty = ""
                if not tty:
                    tty = _tty_via_osascript(session.session_id)
                sessions.append(
                    SessionInfo(
                        session_id=session.session_id,
                        window_index=wi,
                        tab_index=ti,
                        session_index=si,
                        tty=tty,
                        title=title,
                        badge=badge,
                        command="",
                    )
                )
    return sessions


def list_sessions() -> list[SessionInfo]:
    import iterm2

    result: list[SessionInfo] = []

    async def _run(connection):
        nonlocal result
        result = await _list_sessions(connection)

    iterm2.run_until_complete(_run, retry=False)
    return result


def get_session(session_id: str) -> SessionInfo | None:
    for session in list_sessions():
        if session.session_id == session_id:
            return session
    return None


async def _send_line(connection, session_id: str, text: str) -> None:
    import iterm2

    app = await iterm2.async_get_app(connection)
    if app is None:
        raise RuntimeError("iTerm2 app not available")

    target = None
    for window in getattr(app, "windows", []):
        for tab in getattr(window, "tabs", []):
            for session in getattr(tab, "sessions", []):
                if session.session_id == session_id:
                    target = session
                    break
            if target is not None:
                break
        if target is not None:
            break

    if target is None:
        raise RuntimeError(f"session not found: {session_id}")

    await target.async_activate()
    await target.async_send_text(text)
    await asyncio.sleep(0.08)
    await target.async_send_text("\r")


def send_line(session_id: str, text: str) -> None:
    try:
        import iterm2

        iterm2.run_until_complete(lambda conn: _send_line(conn, session_id, text), retry=False)
    except BaseException:
        _send_line_via_osascript(session_id, text)


def tty_basename(tty: str) -> str:
    return os.path.basename(tty or "")


def foreground_command(tty: str) -> str:
    tty_name = tty_basename(tty)
    if not tty_name:
        return ""
    proc = subprocess.run(
        ["ps", "-axo", "tty=,comm="],
        check=False,
        capture_output=True,
        text=True,
    )
    matches: list[str] = []
    for raw in proc.stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        tty_col, cmd = parts
        if tty_col == tty_name:
            matches.append(os.path.basename(cmd.strip()))
    return matches[-1] if matches else ""


def ensure_safe_target(tty: str) -> tuple[bool, str]:
    command = foreground_command(tty)
    if not command:
        return False, "no foreground command detected"
    if command not in SAFE_FOREGROUND:
        return False, f"unsafe foreground command: {command}"
    return True, command


async def _dump_screen(connection, session_id: str, lines: int) -> str:
    import iterm2

    app = await iterm2.async_get_app(connection)
    if app is None:
        raise RuntimeError("iTerm2 app not available")

    target = None
    for window in getattr(app, "windows", []):
        for tab in getattr(window, "tabs", []):
            for session in getattr(tab, "sessions", []):
                if session.session_id == session_id:
                    target = session
                    break
            if target is not None:
                break
        if target is not None:
            break

    if target is None:
        raise RuntimeError(f"session not found: {session_id}")

    screen = await target.async_get_screen_contents()
    collected: list[str] = []
    for idx in range(getattr(screen, "number_of_lines", 0)):
        raw = _clean(screen.line(idx).string)
        if raw:
            collected.append(raw)
    return "\n".join(collected[-max(1, int(lines)):])


def dump_screen(session_id: str, lines: int = 20) -> str:
    import iterm2

    result = ""

    async def _run(connection):
        nonlocal result
        result = await _dump_screen(connection, session_id, lines)

    iterm2.run_until_complete(_run, retry=False)
    return result
