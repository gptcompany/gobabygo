#!/usr/bin/env python3
"""Relay role updates between live mesh UI panes with narrow local heuristics."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.mesh_iterm_control import _mesh_sessions
from scripts.mesh_lite.iterm import _codex_screen_shows_activity, dump_screen, send_line
from scripts.mesh_lite.jsonl import resolve_best_candidate


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Auto-relay local mesh UI updates into a live pane.")
    parser.add_argument("--project", required=True, help="Project path.")
    parser.add_argument(
        "--source-mode",
        choices=("transcript", "screen"),
        default="transcript",
        help="Where to watch for source updates.",
    )
    parser.add_argument("--source-role", default="boss", help="Mesh UI source role for screen-mode relays.")
    parser.add_argument("--target-role", required=True, help="Mesh UI target role.")
    parser.add_argument("--ui-group-id", default="", help="Optional mesh UI group id filter.")
    parser.add_argument(
        "--require-prefix",
        default="",
        help="Optional summary line prefix required in screen-mode before relay.",
    )
    parser.add_argument(
        "--require-activity",
        action="store_true",
        help="In screen-mode, relay only after visible tool/activity output exists.",
    )
    parser.add_argument(
        "--screen-lines",
        type=int,
        default=120,
        help="How many trailing screen lines to inspect in screen-mode.",
    )
    parser.add_argument(
        "--summary-max-chars",
        type=int,
        default=400,
        help="Maximum relayed summary length.",
    )
    parser.add_argument("--poll-interval", type=float, default=2.0, help="Seconds between transcript polls.")
    parser.add_argument(
        "--max-missing-polls",
        type=int,
        default=30,
        help="Exit after this many consecutive target-missing polls.",
    )
    return parser.parse_args()


def _project_aliases(raw: str) -> list[str]:
    aliases: list[str] = []

    def _add(value: str) -> None:
        candidate = str(value or "").strip()
        if candidate and candidate not in aliases:
            aliases.append(candidate)

    expanded = Path(raw).expanduser()
    _add(str(expanded))
    _add(str(expanded.resolve()))

    for value in tuple(aliases):
        if value.startswith("/private/tmp/"):
            _add(value.removeprefix("/private"))
        elif value.startswith("/tmp/"):
            _add(f"/private{value}")

    return aliases


def _role_session_id(project_aliases: list[str], role: str, ui_group_id: str) -> str:
    import iterm2

    result = ""

    async def _run(connection) -> None:
        nonlocal result
        app = await iterm2.async_get_app(connection)
        if app is None:
            return
        seen: set[str] = set()
        for alias in project_aliases:
            panes = await _mesh_sessions(app, alias, ui_group_id)
            for pane in panes:
                session_id = str(getattr(pane.session, "session_id", "") or "")
                if not session_id or session_id in seen:
                    continue
                seen.add(session_id)
                if pane.role == role:
                    result = session_id
                    return

    try:
        iterm2.run_until_complete(_run, retry=False)
    except Exception:
        return ""
    return result


def _normalize_summary_text(text: str, *, max_chars: int) -> str:
    compact = " ".join(str(text or "").split()).strip()
    if not compact:
        return ""
    return compact[: max(1, int(max_chars))]


def _looks_ready_prompt(text: str) -> bool:
    lines = str(text or "").splitlines()
    for raw in reversed(lines):
        line = raw.strip()
        if not line:
            continue
        if line.startswith(">") and "Type your message" in line:
            return True
        if line.startswith("› ") and "@" in line:
            return True
        return line == "❯"
    return False


def _target_screen_accepts_input(screen_text: str) -> bool:
    body = str(screen_text or "")
    if not body:
        return False
    return _looks_ready_prompt(body)


def _extract_screen_summary(
    screen_text: str,
    *,
    require_prefix: str,
    require_activity: bool,
    max_chars: int,
) -> str:
    body = str(screen_text or "")
    if not body or not _looks_ready_prompt(body):
        return ""
    if require_activity and not _codex_screen_shows_activity(body):
        return ""

    prefix = str(require_prefix or "").strip()
    if prefix:
        for raw in reversed(body.splitlines()):
            line = raw.strip()
            if not line or line == "❯":
                continue
            if line.startswith(prefix):
                return _normalize_summary_text(line[len(prefix) :].strip(), max_chars=max_chars)
        return ""

    filtered: list[str] = []
    for raw in body.splitlines():
        line = raw.replace("\xa0", " ").strip()
        if not line or line == "❯":
            continue
        if line.startswith(("✻", "⎿", "Stop says:", "›")):
            continue
        filtered.append(line)
    return _normalize_summary_text(" ".join(filtered), max_chars=max_chars)


def _baseline_fingerprint(
    *,
    project_path: str,
    aliases: list[str],
    source_mode: str,
    source_role: str,
    ui_group_id: str,
    require_prefix: str,
    require_activity: bool,
    screen_lines: int,
    summary_max_chars: int,
) -> str:
    if source_mode == "screen":
        source_session_id = _role_session_id(aliases, source_role, ui_group_id)
        if not source_session_id:
            return ""
        try:
            screen_text = dump_screen(source_session_id, lines=screen_lines)
        except Exception:
            return ""
        summary = _extract_screen_summary(
            screen_text,
            require_prefix=require_prefix,
            require_activity=require_activity,
            max_chars=summary_max_chars,
        )
        return f"{source_session_id}:{summary}" if summary else ""

    baseline = resolve_best_candidate(project_path)
    if baseline and baseline.assistant_text:
        return f"{baseline.path}:{baseline.last_modified}:{baseline.assistant_text}"
    return ""


def main() -> int:
    args = _parse_args()
    project_path = str(Path(args.project).expanduser().resolve())
    aliases = _project_aliases(project_path)
    poll_interval = max(0.25, float(args.poll_interval))
    max_missing_polls = max(1, int(args.max_missing_polls))
    source_mode = str(args.source_mode or "transcript").strip() or "transcript"

    last_fingerprint = _baseline_fingerprint(
        project_path=project_path,
        aliases=aliases,
        source_mode=source_mode,
        source_role=str(args.source_role or "").strip() or "boss",
        ui_group_id=str(args.ui_group_id or "").strip(),
        require_prefix=str(args.require_prefix or "").strip(),
        require_activity=bool(args.require_activity),
        screen_lines=max(20, int(args.screen_lines)),
        summary_max_chars=max(40, int(args.summary_max_chars)),
    )

    missing_polls = 0
    while True:
        ui_group_id = str(args.ui_group_id or "").strip()
        source_session_id = ""
        if source_mode == "screen":
            source_session_id = _role_session_id(aliases, str(args.source_role or "").strip() or "boss", ui_group_id)
        target_session_id = _role_session_id(aliases, args.target_role, ui_group_id)
        if not target_session_id or (source_mode == "screen" and not source_session_id):
            missing_polls += 1
            if missing_polls >= max_missing_polls:
                return 0
            time.sleep(poll_interval)
            continue

        missing_polls = 0
        if source_mode == "screen":
            try:
                screen_text = dump_screen(source_session_id, lines=max(20, int(args.screen_lines)))
            except Exception:
                time.sleep(poll_interval)
                continue
            summary = _extract_screen_summary(
                screen_text,
                require_prefix=str(args.require_prefix or "").strip(),
                require_activity=bool(args.require_activity),
                max_chars=max(40, int(args.summary_max_chars)),
            )
            fingerprint = f"{source_session_id}:{summary}" if summary else ""
            payload = summary
        else:
            candidate = resolve_best_candidate(project_path)
            if not candidate or not candidate.assistant_text:
                time.sleep(poll_interval)
                continue
            fingerprint = f"{candidate.path}:{candidate.last_modified}:{candidate.assistant_text}"
            payload = candidate.assistant_text

        if not fingerprint or not payload:
            time.sleep(poll_interval)
            continue
        try:
            target_screen = dump_screen(target_session_id, lines=max(20, int(args.screen_lines)))
        except Exception:
            time.sleep(poll_interval)
            continue
        if not _target_screen_accepts_input(target_screen):
            time.sleep(poll_interval)
            continue
        if fingerprint != last_fingerprint:
            send_line(target_session_id, payload)
            last_fingerprint = fingerprint
        time.sleep(poll_interval)


if __name__ == "__main__":
    raise SystemExit(main())
