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
from dataclasses import dataclass
from typing import Any


@dataclass
class MeshPane:
    window_index: int
    tab_index: int
    session_index: int
    repo: str
    role: str
    tab: Any
    session: Any


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Control mesh-marked iTerm2 panes.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    list_parser = sub.add_parser("list", help="List mesh-marked panes.")
    list_parser.add_argument("--repo", default="", help="Filter by repo path.")

    close_parser = sub.add_parser("close", help="Close mesh-marked tabs for a repo.")
    close_parser.add_argument("--repo", required=True, help="Exact mesh repo path.")

    for name in ("focus", "dump", "send-text", "send-line", "send-key"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--repo", required=True, help="Exact mesh repo path.")
        cmd.add_argument("--role", required=True, help="Exact mesh role.")
        if name == "dump":
            cmd.add_argument("--lines", type=int, default=20, help="Trailing non-empty lines to print.")
        elif name in {"send-text", "send-line"}:
            cmd.add_argument("text", help="Text to send verbatim.")
        elif name == "send-key":
            cmd.add_argument("key", help="Logical key: enter/up/down/left/right/esc/tab/backspace/ctrl-c.")

    return parser.parse_args()


async def _mesh_sessions(app, repo_filter: str = "") -> list[MeshPane]:
    panes: list[MeshPane] = []
    repo_filter = str(repo_filter or "").strip()
    for wi, window in enumerate(getattr(app, "windows", []), 1):
        for ti, tab in enumerate(getattr(window, "tabs", []), 1):
            for si, session in enumerate(getattr(tab, "sessions", []), 1):
                try:
                    marker = await session.async_get_variable("user.mesh_ui_tab")
                    repo = str(await session.async_get_variable("user.mesh_repo") or "").strip()
                    role = str(await session.async_get_variable("user.mesh_role") or "").strip()
                except Exception:
                    continue
                if str(marker) != "1" or not repo or not role:
                    continue
                if repo_filter and repo != repo_filter:
                    continue
                panes.append(
                    MeshPane(
                        window_index=wi,
                        tab_index=ti,
                        session_index=si,
                        repo=repo,
                        role=role,
                        tab=tab,
                        session=session,
                    )
                )
    return panes


async def _find_mesh_pane(app, repo: str, role: str) -> MeshPane:
    repo = str(repo or "").strip()
    role = str(role or "").strip()
    matches = [pane for pane in await _mesh_sessions(app, repo) if pane.role == role]
    if not matches:
        raise RuntimeError(f"no pane matched repo={repo!r} role={role!r}")
    if len(matches) > 1:
        raise RuntimeError(f"multiple panes matched repo={repo!r} role={role!r}")
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


async def _screen_tail(session: Any, lines: int = 20) -> str:
    screen = await session.async_get_screen_contents()
    collected: list[str] = []
    for idx in range(getattr(screen, "number_of_lines", 0)):
        raw = str(screen.line(idx).string or "")
        line = raw.replace("\x00", "").rstrip()
        if line.strip():
            collected.append(line)
    return "\n".join(collected[-max(1, int(lines)) :])


async def _run(connection, args: argparse.Namespace) -> int:
    import iterm2

    app = await iterm2.async_get_app(connection)
    if app is None:
        raise RuntimeError("iTerm2 app not available")

    if args.cmd == "list":
        panes = await _mesh_sessions(app, args.repo)
        for pane in panes:
            print(
                f"W{pane.window_index} T{pane.tab_index} S{pane.session_index} "
                f"role={pane.role} repo={pane.repo}"
            )
        return 0

    if args.cmd == "close":
        panes = await _mesh_sessions(app, args.repo)
        tabs: dict[int, Any] = {}
        for pane in panes:
            tabs[id(pane.tab)] = pane.tab
        for tab in tabs.values():
            close_fn = getattr(tab, "async_close", None)
            if close_fn is None:
                continue
            try:
                await close_fn(force=True)
            except TypeError:
                await close_fn()
        print(f"closed {len(tabs)} mesh tab(s) for repo={args.repo}")
        return 0

    pane = await _find_mesh_pane(app, args.repo, args.role)

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
        print(await _screen_tail(pane.session, lines=args.lines))
        return 0
    raise RuntimeError(f"unsupported command: {args.cmd}")


def main() -> int:
    args = _parse_args()
    try:
        import iterm2
    except ImportError:
        raise SystemExit(
            "Error: Python package 'iterm2' not found. Use: uv run --with iterm2 -- python scripts/mesh_iterm_control.py ..."
        )

    try:
        iterm2.run_until_complete(lambda conn: _run(conn, args), retry=False)
    except Exception as exc:
        raise SystemExit(f"Error: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
