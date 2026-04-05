#!/usr/bin/env python3
"""Archived Claude Code UserPromptSubmit hook for boss-to-president relay."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

_HOOK_NOOP = {
    "continue": True,
    "suppressOutput": False,
    "hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": "archived boss hook; runtime relay now uses mesh_prompt_relay_proxy.py",
    },
}


def _mesh_script() -> Path:
    mesh_home = os.environ.get("MESH_HOME", "").strip()
    if mesh_home:
        return Path(mesh_home) / "scripts" / "mesh"
    return Path(__file__).resolve().parents[2] / "scripts" / "mesh"


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        print(json.dumps(_HOOK_NOOP))
        return 0

    prompt = str(payload.get("prompt") or "").strip()
    if not prompt or prompt.startswith("/"):
        print(json.dumps(_HOOK_NOOP))
        return 0

    command = [_mesh_script().as_posix(), "send", "president"]
    ui_group_id = os.environ.get("MESH_UI_GROUP_ID", "").strip()
    if ui_group_id:
        command.extend(["--ui-group-id", ui_group_id])
    command.append(f"[boss relay] {prompt}")
    try:
        subprocess.run(command, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass

    print(json.dumps(_HOOK_NOOP))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
