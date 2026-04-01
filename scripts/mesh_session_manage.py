#!/usr/bin/env python3
"""Interactive session picker that offers attach-or-kill."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Any
from urllib.error import HTTPError, URLError

from mesh_session_cli import (
    _control_plane_timeout,
    build_attach_spec,
    build_session_choices,
    filter_active_session_choices,
    load_router_env,
    router_post_json,
    select_choice,
)


@dataclass(frozen=True)
class ManageAction:
    key: str
    title: str
    summary: str


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Select a live mesh session, then attach or kill it.")
    parser.add_argument("query", nargs="?", default="", help="Optional repo/session/role filter.")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Search sessions across all repos instead of defaulting to the current repo.",
    )
    parser.add_argument(
        "--state",
        default="open",
        choices=["open", "closed", "errored", "all"],
        help="Session state filter (default: open).",
    )
    parser.add_argument(
        "--ws-host",
        default=os.environ.get("MESH_WS_HOST", "sam@10.0.0.2"),
        help="WS SSH target for tmux fallback.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional file path for the resolved JSON payload.",
    )
    parser.add_argument(
        "--action",
        default="",
        choices=["attach", "kill", "quit"],
        help="Optional forced action. If omitted, an action picker is shown.",
    )
    return parser.parse_args()


def _emit_payload(payload: dict[str, Any], output_path: str) -> None:
    encoded = json.dumps(payload)
    if output_path:
        Path(output_path).write_text(encoded, encoding="utf-8")
        return
    print(encoded)


def _actions() -> list[ManageAction]:
    return [
        ManageAction("attach", "Attach", "Open the selected live session."),
        ManageAction("kill", "Kill", "Terminate and close the selected live session."),
        ManageAction("quit", "Quit", "Exit without doing anything."),
    ]


def action_by_key(key: str) -> ManageAction:
    for action in _actions():
        if action.key == key:
            return action
    raise ValueError(f"unsupported action '{key}'")


def _questionary_select_action(actions: list[ManageAction]) -> ManageAction | None:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise RuntimeError("interactive selector unavailable")
    try:
        import questionary
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("questionary unavailable") from exc

    return questionary.select(
        "Choose action:",
        choices=[
            questionary.Choice(
                title=f"{action.title} | {action.summary}",
                value=action,
            )
            for action in actions
        ],
        use_shortcuts=True,
        use_indicator=True,
    ).ask()


def select_action(
    actions: list[ManageAction],
    *,
    prompt_fn: Callable[[str], str] = input,
    interactive: bool = True,
) -> ManageAction:
    if not actions:
        raise ValueError("no actions available")
    if interactive:
        try:
            selected = _questionary_select_action(actions)
            if selected is None:
                raise ValueError("selection cancelled")
            return selected
        except RuntimeError:
            pass
    if not interactive:
        raise ValueError("interactive selector unavailable")

    for index, action in enumerate(actions, start=1):
        print(f"{index}. {action.title} - {action.summary}", file=sys.stderr)
    print(f"Select action [1-{len(actions)}]: ", end="", file=sys.stderr, flush=True)
    raw = prompt_fn("").strip()
    if not raw:
        raise ValueError("selection cancelled")
    if not raw.isdigit():
        raise ValueError("invalid selection")
    index = int(raw)
    if index < 1 or index > len(actions):
        raise ValueError("invalid selection")
    return actions[index - 1]


def _terminal_state(choice) -> str:
    status = str(choice.task_status or "").strip().lower()
    if status in {"failed", "timeout", "canceled"}:
        return "errored"
    return "closed"


def kill_choice(router_url: str, auth_token: str, choice) -> dict[str, Any]:
    failures: list[str] = []
    try:
        router_post_json(
            router_url,
            auth_token,
            "/sessions/signal",
            {"session_id": choice.session_id, "signal": "terminate"},
        )
    except HTTPError as exc:
        if exc.code not in {404, 409}:
            failures.append(f"/sessions/signal HTTP {exc.code}")
    except URLError as exc:
        failures.append(f"/sessions/signal {exc}")
    except (TimeoutError, OSError, json.JSONDecodeError) as exc:
        failures.append(f"/sessions/signal {exc}")

    state = _terminal_state(choice)
    try:
        router_post_json(
            router_url,
            auth_token,
            "/sessions/close",
            {"session_id": choice.session_id, "state": state},
        )
    except HTTPError as exc:
        if exc.code not in {404, 409}:
            failures.append(f"/sessions/close HTTP {exc.code}")
    except URLError as exc:
        failures.append(f"/sessions/close {exc}")
    except (TimeoutError, OSError, json.JSONDecodeError) as exc:
        failures.append(f"/sessions/close {exc}")

    return {
        "session_id": choice.session_id,
        "repo": choice.repo,
        "repo_name": choice.repo_name,
        "role": choice.role,
        "cli_type": choice.cli_type,
        "ui_group_id": choice.ui_group_id,
        "state": state,
        "failures": failures,
    }


def main() -> int:
    args = _parse_args()
    router_url, auth_token = load_router_env()
    if not router_url or not auth_token:
        print("Error: mesh router env not configured", file=sys.stderr)
        return 1

    try:
        choices = build_session_choices(router_url, auth_token, state=args.state)
    except HTTPError as exc:
        print(f"Error: /sessions returned HTTP {exc.code}", file=sys.stderr)
        return 1
    except URLError as exc:
        print(f"Error: cannot connect to mesh router at {router_url}: {exc}", file=sys.stderr)
        return 1
    except (TimeoutError, OSError, json.JSONDecodeError) as exc:
        print(f"Error: failed to query mesh router: {exc}", file=sys.stderr)
        return 1

    selectable = filter_active_session_choices(choices) if args.state == "open" else choices
    try:
        selected = select_choice(selectable, query=args.query, interactive=sys.stdin.isatty())
        action = action_by_key(args.action) if args.action else select_action(_actions(), interactive=sys.stdin.isatty())
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if action.key == "quit":
        _emit_payload({"action": "quit", "selection": asdict(selected)}, args.output)
        return 0

    if action.key == "kill":
        result = kill_choice(router_url, auth_token, selected)
        payload = {"action": "kill", "selection": asdict(selected), "result": result}
        _emit_payload(payload, args.output)
        return 1 if result.get("failures") else 0

    payload = {
        "action": "attach",
        "selection": asdict(selected),
        "attach": build_attach_spec(selected, args.ws_host),
    }
    _emit_payload(payload, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
