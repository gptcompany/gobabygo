#!/usr/bin/env python3
"""Slice 0 spike for Mesh Lite.

Validate the hard path only:
- identify live iTerm2 sessions
- resolve a Claude transcript for a project
- extract the last assistant message
- inject that message into another live iTerm2 session
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.mesh_lite.iterm import dump_screen, list_sessions, send_line
from scripts.mesh_lite.jsonl import extract_last_assistant_msg, resolve_best_candidate, transcript_candidates


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


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mesh Lite Slice 0 spike.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    ls_cmd = sub.add_parser("list", help="List visible iTerm2 sessions.")
    ls_cmd.add_argument("--tty", action="store_true", help="Show tty values.")

    relay_cmd = sub.add_parser("relay", help="Relay latest assistant reply from a project into a target session.")
    relay_cmd.add_argument("--project", required=True, help="Absolute repo path whose Claude transcript should be scanned.")
    relay_cmd.add_argument("--to", required=True, help="Target iTerm2 session id.")
    relay_cmd.add_argument("--jsonl", default="", help="Explicit transcript path override.")
    relay_cmd.add_argument("--dry-run", action="store_true", help="Print the reply without injecting it.")

    probe_cmd = sub.add_parser("probe", help="Show transcript candidates for a project.")
    probe_cmd.add_argument("--project", required=True, help="Absolute repo path whose Claude transcript should be scanned.")

    dump_cmd = sub.add_parser("dump", help="Dump visible screen content for one live session.")
    dump_cmd.add_argument("--session", required=True, help="Target iTerm2 session id.")
    dump_cmd.add_argument("--lines", type=int, default=20, help="Trailing non-empty lines to print.")

    return parser.parse_args()


def _tty_basename(tty: str) -> str:
    return os.path.basename(tty or "")


def _foreground_command(tty: str) -> str:
    tty_name = _tty_basename(tty)
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


def _ensure_safe_target(tty: str) -> tuple[bool, str]:
    command = _foreground_command(tty)
    if not command:
        return False, "no foreground command detected"
    if command not in SAFE_FOREGROUND:
        return False, f"unsafe foreground command: {command}"
    return True, command


def _cmd_list(show_tty: bool) -> int:
    for session in list_sessions():
        extra = f" tty={session.tty}" if show_tty else ""
        print(
            f"{session.session_id} "
            f"W{session.window_index}T{session.tab_index}S{session.session_index}"
            f"{extra} title={session.title!r} badge={session.badge!r}"
        )
    return 0


def _cmd_relay(project: str, to_session_id: str, dry_run: bool) -> int:
    project_path = str(Path(project).expanduser().resolve())
    raise RuntimeError("use _cmd_relay_with_source")


def _cmd_relay_with_source(project: str, to_session_id: str, jsonl_override: str, dry_run: bool) -> int:
    project_path = str(Path(project).expanduser().resolve())
    chosen = None
    reply = None
    if jsonl_override:
        chosen = Path(jsonl_override).expanduser().resolve()
        reply = extract_last_assistant_msg(chosen)
    else:
        candidate = resolve_best_candidate(project_path)
        if candidate is not None:
            chosen = candidate.path
            reply = candidate.assistant_text

    if not chosen or not chosen.exists():
        raise SystemExit(f"No Claude transcript found for project: {project_path}")
    if not reply:
        raise SystemExit(f"No assistant reply found in transcript: {chosen}")

    sessions = {session.session_id: session for session in list_sessions()}
    target = sessions.get(to_session_id)
    if target is None:
        raise SystemExit(f"Target session not found: {to_session_id}")

    safe, reason = _ensure_safe_target(target.tty)
    if not safe:
        raise SystemExit(f"Refusing injection into target {to_session_id}: {reason}")

    print(f"Using transcript: {chosen}")
    print(f"Target tty: {target.tty} foreground={reason}")
    if dry_run:
        print("---")
        print(reply)
        return 0

    send_line(target.session_id, reply)
    print(f"Relayed {len(reply)} chars to {target.session_id}")
    return 0


def _cmd_probe(project: str) -> int:
    project_path = str(Path(project).expanduser().resolve())
    candidates = transcript_candidates(project_path)
    if not candidates:
        print(f"No Claude transcripts found for project: {project_path}")
        return 0

    for idx, candidate in enumerate(candidates, 1):
        preview = (candidate.assistant_text or "").replace("\n", " ")
        if len(preview) > 80:
            preview = preview[:77] + "..."
        print(
            f"{idx}. session_id={candidate.session_id} "
            f"mtime={candidate.last_modified_iso} "
            f"cwd={candidate.cwd or '-'} "
            f"path={candidate.path}"
        )
        print(f"   assistant={preview or '-'}")
    return 0


def _cmd_dump(session_id: str, lines: int) -> int:
    print(dump_screen(session_id, lines=lines))
    return 0


def main() -> int:
    args = _parse_args()
    if args.cmd == "list":
        return _cmd_list(show_tty=args.tty)
    if args.cmd == "probe":
        return _cmd_probe(project=args.project)
    if args.cmd == "dump":
        return _cmd_dump(session_id=args.session, lines=args.lines)
    if args.cmd == "relay":
        return _cmd_relay_with_source(
            project=args.project,
            to_session_id=args.to,
            jsonl_override=args.jsonl,
            dry_run=args.dry_run,
        )
    raise SystemExit(f"Unsupported command: {args.cmd}")


if __name__ == "__main__":
    raise SystemExit(main())
