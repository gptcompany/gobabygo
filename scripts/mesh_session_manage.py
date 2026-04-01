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
    filter_session_choices,
    load_router_env,
    router_post_json,
)


@dataclass(frozen=True)
class ManageAction:
    key: str
    title: str
    summary: str


@dataclass(frozen=True)
class ManageTarget:
    target_kind: str
    session_id: str = ""
    worker_id: str = ""
    cli_type: str = ""
    account_profile: str = ""
    state: str = ""
    task_id: str = ""
    task_status: str = ""
    thread_id: str = ""
    thread_name: str = ""
    thread_status: str = ""
    repo: str = ""
    repo_name: str = ""
    role: str = ""
    title: str = ""
    updated_at: str = ""
    tmux_session: str = ""
    attach_kind: str = ""
    attach_target: str = ""
    attach_owner: str = ""
    ui_group_id: str = ""
    child_roles: tuple[str, ...] = ()
    child_session_ids: tuple[str, ...] = ()


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
    return [ManageAction("quit", "Quit", "Exit without doing anything.")]


def available_actions(target: ManageTarget) -> list[ManageAction]:
    if target.target_kind == "layout":
        return [
            ManageAction("attach", "Attach Layout", "Reattach the full iTerm2 layout for this session group."),
            ManageAction("kill", "Kill Layout", "Terminate and close every panel in this session group."),
            *_actions(),
        ]
    return [
        ManageAction("attach", "Attach Panel", "Open the selected live panel only."),
        ManageAction("kill", "Kill Panel", "Terminate and close the selected live panel."),
        *_actions(),
    ]


def action_by_key(key: str, actions: list[ManageAction] | None = None) -> ManageAction:
    candidates = actions or [
        ManageAction("attach", "Attach", ""),
        ManageAction("kill", "Kill", ""),
        *_actions(),
    ]
    for action in candidates:
        if action.key == key:
            return action
    raise ValueError(f"unsupported action '{key}'")


_ROLE_ORDER = {
    "boss": 0,
    "president": 1,
    "lead": 2,
    "worker-claude": 3,
    "worker-codex": 4,
    "worker-gemini": 5,
    "verifier": 6,
}


def _role_sort_key(role: str) -> tuple[int, str]:
    normalized = str(role or "").strip()
    return (_ROLE_ORDER.get(normalized, 100), normalized)


def _target_from_choice(choice) -> ManageTarget:
    payload = asdict(choice)
    payload["target_kind"] = "session"
    return ManageTarget(**payload)


def _layout_target(choices: list) -> ManageTarget:
    representative = sorted(choices, key=lambda item: (item.updated_at, item.session_id), reverse=True)[0]
    ordered_roles = tuple(
        choice.role or "-"
        for choice in sorted(choices, key=lambda item: (_role_sort_key(item.role), item.session_id))
    )
    return ManageTarget(
        target_kind="layout",
        repo=representative.repo,
        repo_name=representative.repo_name,
        thread_id=representative.thread_id,
        thread_name=representative.thread_name,
        thread_status=representative.thread_status,
        title=representative.title,
        updated_at=representative.updated_at,
        ui_group_id=representative.ui_group_id,
        child_roles=ordered_roles,
        child_session_ids=tuple(choice.session_id for choice in choices),
    )


def build_manage_targets(choices: list, *, query: str = "") -> list[ManageTarget]:
    filtered = filter_session_choices(choices, query)
    if not filtered:
        return []

    grouped: dict[tuple[str, str, str], list] = {}
    standalone: list = []
    for choice in filtered:
        ui_group_id = str(choice.ui_group_id or "").strip()
        if ui_group_id:
            key = (choice.repo, choice.repo_name, ui_group_id)
            grouped.setdefault(key, []).append(choice)
        else:
            standalone.append(choice)

    targets: list[ManageTarget] = []
    grouped_items = sorted(
        grouped.values(),
        key=lambda items: max((choice.updated_at, choice.session_id) for choice in items),
        reverse=True,
    )
    for items in grouped_items:
        ordered_items = sorted(items, key=lambda item: (_role_sort_key(item.role), item.session_id))
        targets.append(_layout_target(ordered_items))
        targets.extend(_target_from_choice(choice) for choice in ordered_items)

    standalone_sorted = sorted(
        standalone,
        key=lambda item: (item.updated_at, item.session_id),
        reverse=True,
    )
    targets.extend(_target_from_choice(choice) for choice in standalone_sorted)
    return targets


def _target_label(target: ManageTarget) -> str:
    summary = target.thread_name or target.title or "-"
    if target.target_kind == "layout":
        roles = ", ".join(target.child_roles) or "-"
        return " | ".join(
            [
                target.repo_name or target.repo or "-",
                "layout",
                f"roles={roles}",
                f"ui_group={str(target.ui_group_id or '')[:12] or '-'}",
                summary,
            ]
        )
    parts = [
        target.role or "-",
        "panel",
        target.cli_type or "-",
        str(target.session_id or "")[:12] or "-",
        summary,
    ]
    if target.ui_group_id:
        return "  " + " | ".join(parts)
    return " | ".join([target.repo_name or target.repo or "-", *parts])


def render_targets(targets: list[ManageTarget]) -> str:
    rows = []
    for index, target in enumerate(targets, start=1):
        rows.append(f"{index}. {_target_label(target)}")
    return "\n".join(rows)


def _questionary_select_target(targets: list[ManageTarget]) -> ManageTarget | None:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise RuntimeError("interactive selector unavailable")
    try:
        import questionary
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("questionary unavailable") from exc

    questionary_choices = []
    previous_was_layout = False
    for target in targets:
        if target.target_kind == "layout" and questionary_choices:
            questionary_choices.append(questionary.Separator())
        elif previous_was_layout and target.target_kind != "layout":
            previous_was_layout = False
        questionary_choices.append(
            questionary.Choice(
                title=_target_label(target),
                value=target,
            )
        )
        previous_was_layout = target.target_kind == "layout"
    return questionary.select(
        "Select layout or panel:",
        choices=questionary_choices,
        use_shortcuts=True,
        use_indicator=True,
    ).ask()


def select_target(
    targets: list[ManageTarget],
    *,
    prompt_fn: Callable[[str], str] = input,
    interactive: bool = True,
) -> ManageTarget:
    if not targets:
        raise ValueError("no sessions matched")
    if len(targets) == 1:
        return targets[0]
    if interactive:
        try:
            selected = _questionary_select_target(targets)
            if selected is None:
                raise ValueError("selection cancelled")
            return selected
        except RuntimeError:
            pass
    if not interactive:
        raise ValueError("multiple sessions matched; refine the query")

    print(render_targets(targets), file=sys.stderr)
    print(f"Select target [1-{len(targets)}]: ", end="", file=sys.stderr, flush=True)
    raw = prompt_fn("").strip()
    if not raw:
        raise ValueError("selection cancelled")
    if not raw.isdigit():
        raise ValueError("invalid selection")
    index = int(raw)
    if index < 1 or index > len(targets):
        raise ValueError("invalid selection")
    return targets[index - 1]


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


def kill_choice(router_url: str, auth_token: str, choice: ManageTarget) -> dict[str, Any]:
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
        targets = build_manage_targets(selectable, query=args.query)
        selected = select_target(targets, interactive=sys.stdin.isatty())
        actions = available_actions(selected)
        action = action_by_key(args.action, actions) if args.action else select_action(actions, interactive=sys.stdin.isatty())
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if action.key == "quit":
        _emit_payload({"action": "quit", "selection": asdict(selected)}, args.output)
        return 0

    if action.key == "kill":
        if selected.target_kind == "layout":
            payload = {
                "action": "kill",
                "selection": asdict(selected),
                "ui": {
                    "repo": selected.repo,
                    "repo_name": selected.repo_name,
                    "ui_group_id": selected.ui_group_id,
                },
            }
            _emit_payload(payload, args.output)
            return 0
        result = kill_choice(router_url, auth_token, selected)
        payload = {"action": "kill", "selection": asdict(selected), "result": result}
        _emit_payload(payload, args.output)
        return 1 if result.get("failures") else 0

    if selected.target_kind == "layout":
        payload = {
            "action": "attach",
            "selection": asdict(selected),
            "ui": {
                "repo": selected.repo,
                "repo_name": selected.repo_name,
                "ui_group_id": selected.ui_group_id,
            },
        }
        _emit_payload(payload, args.output)
        return 0

    payload = {
        "action": "attach",
        "selection": asdict(selected),
        "attach": build_attach_spec(selected, args.ws_host),
    }
    _emit_payload(payload, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
