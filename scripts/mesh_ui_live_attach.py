#!/usr/bin/env python3
"""Resolve a live tmux attach command for a mesh UI role on the WS host."""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path


def _load_mesh_iterm_ui():
    script_path = Path(__file__).resolve().with_name("mesh_iterm_ui.py")
    spec = importlib.util.spec_from_file_location("mesh_iterm_ui", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Resolve live mesh UI attach command.")
    parser.add_argument("role")
    parser.add_argument("repo")
    parser.add_argument("repo_name")
    parser.add_argument("roles_csv", nargs="?", default="")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    module = _load_mesh_iterm_ui()
    roles = [role.strip() for role in args.roles_csv.split(",") if role.strip()] or [args.role]
    cfg = module.UiConfig(
        repo=args.repo,
        repo_name=args.repo_name,
        roles=roles,
        max_panes_per_tab=1,
        single_tab=True,
        replace_tabs=False,
        preset="auto",
        attach_live=True,
    )
    print(module._discover_live_remote_inits(cfg).get(args.role, ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
