#!/usr/bin/env python3
"""Resolve the default interactive mesh launcher action."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from dataclasses import asdict, dataclass
from typing import Callable

from mesh_session_cli import detect_repo_context


@dataclass(frozen=True)
class MenuAction:
    key: str
    title: str
    summary: str
    argv: tuple[str, ...]


def build_default_actions(repo_name: str) -> list[MenuAction]:
    target = repo_name or "current repo"
    return [
        MenuAction("attach", f"Attach live session ({target})", "Open the live session picker for this repo.", ("attach",)),
        MenuAction("sessions", f"List live sessions ({target})", "Show active router-backed sessions for this repo.", ("sessions",)),
        MenuAction("ui", f"Open operator UI ({target})", "Launch the iTerm2 operator layout for this repo.", ("ui",)),
        MenuAction("start", f"Start new thread ({target})", "Create a new pipeline run with an auto-generated label.", ("start",)),
        MenuAction("attach_all", "Attach live session (all repos)", "Cross-repo live session picker.", ("attach", "--all")),
        MenuAction("quit", "Quit", "Exit without running a mesh subcommand.", ()),
    ]


def _action_label(action: MenuAction) -> str:
    return f"{action.title} | {action.summary}"


def _questionary_select_action(actions: list[MenuAction]) -> MenuAction | None:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise RuntimeError("interactive selector unavailable")
    try:
        import questionary
    except ImportError as exc:  # pragma: no cover - depends on local environment
        raise RuntimeError("questionary unavailable") from exc

    return questionary.select(
        "Choose mesh action:",
        choices=[
            questionary.Choice(
                title=_action_label(action),
                value=action,
            )
            for action in actions
        ],
        use_shortcuts=True,
        use_indicator=True,
    ).ask()


def select_action(
    actions: list[MenuAction],
    *,
    prompt_fn: Callable[[str], str] = input,
    interactive: bool = True,
) -> MenuAction:
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


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Select a default mesh action.")
    parser.add_argument(
        "--output",
        default="",
        help="Optional file path for the resolved JSON payload.",
    )
    return parser.parse_args()


def _emit_payload(payload: dict[str, object], output_path: str) -> None:
    encoded = json.dumps(payload)
    if output_path:
        Path(output_path).write_text(encoded, encoding="utf-8")
        return
    print(encoded)


def main() -> int:
    args = _parse_args()
    _, repo_name = detect_repo_context()
    actions = build_default_actions(repo_name)
    try:
        selected = select_action(actions, interactive=sys.stdin.isatty())
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    _emit_payload({"argv": list(selected.argv), "action": asdict(selected)}, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
