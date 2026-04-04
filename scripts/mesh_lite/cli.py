#!/usr/bin/env python3
"""Mesh Lite CLI skeleton built on the validated Slice 0 primitives."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.mesh_lite.iterm import ensure_safe_target, get_session
from scripts.mesh_lite.jsonl import extract_last_assistant_msg, transcript_candidates
from scripts.mesh_lite.registry import MeshLiteRegistry, MeshLiteRegistryError, build_entry


def _project_path(raw: str) -> str:
    return str(Path(raw).expanduser().resolve())


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mesh Lite iTerm2-first control plane.")
    parser.add_argument(
        "--registry",
        default="",
        help="Override registry path. Default: ~/.mesh-lite/registry.json",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    discover = sub.add_parser("discover", help="Discover live mesh UI panes and register them.")
    discover.add_argument("--project", required=True, help="Absolute repo path.")

    status = sub.add_parser("status", help="Show registered live role mappings.")
    status.add_argument("--project", default="", help="Optional repo path filter.")

    probe = sub.add_parser("probe", help="Show transcript candidates for a project.")
    probe.add_argument("--project", required=True, help="Absolute repo path.")

    relay = sub.add_parser("relay-last", help="Relay last assistant reply from one role to another.")
    relay.add_argument("--project", required=True, help="Absolute repo path.")
    relay.add_argument("source_role", help="Source role registered in the registry.")
    relay.add_argument("target_role", help="Target role registered in the registry.")
    relay.add_argument("--dry-run", action="store_true", help="Print chosen payload without injecting it.")

    return parser.parse_args()


def _registry(path_override: str) -> MeshLiteRegistry:
    return MeshLiteRegistry(Path(path_override).expanduser()) if path_override else MeshLiteRegistry()


def _parse_title_metadata(title: str) -> tuple[str | None, str | None, str | None]:
    if not title or " (" not in title or ") | " not in title:
        return None, None, None
    parts = title.split(" (", 1)[1].split(") | ", 1)[0]
    if not parts or parts == "operator":
        return None, None, None
    subparts = parts.split(":")
    launch_mode = subparts[0] if len(subparts) >= 1 and subparts[0] else None
    provider = subparts[1] if len(subparts) >= 2 and subparts[1] else None
    upstream_session_id = subparts[2] if len(subparts) >= 3 and subparts[2] else None
    return provider, launch_mode, upstream_session_id


def _select_jsonl_path(
    *,
    candidates: list,
    existing_jsonl_path: str,
    upstream_session_id: str | None,
) -> str:
    existing_path = str(existing_jsonl_path or "").strip()
    if existing_path:
        return existing_path

    if upstream_session_id:
        session_prefix = upstream_session_id.strip()
        matched = []
        for candidate in candidates:
            candidate_session_id = str(candidate.session_id or "").strip()
            if candidate_session_id and candidate_session_id.startswith(session_prefix):
                matched.append(candidate)
        if len(matched) == 1:
            return str(matched[0].path)
        return ""

    return ""


def _select_fallback_jsonl_path(
    *,
    project_path: str,
    candidates: list,
    claimed_paths: set[str],
) -> str:
    available = [candidate for candidate in candidates if str(candidate.path) not in claimed_paths]
    if not available:
        return ""

    exact_cwd_candidates = [candidate for candidate in available if candidate.cwd == project_path]
    if len(exact_cwd_candidates) == 1 and exact_cwd_candidates[0].assistant_text:
        return str(exact_cwd_candidates[0].path)

    replied_candidates = [candidate for candidate in available if candidate.assistant_text]
    if len(replied_candidates) == 1 and len(available) == 1:
        return str(replied_candidates[0].path)

    return ""


def _apply_fallback_binding(
    *,
    project_path: str,
    discovered_entries: list,
    pending_fallback_indices: list[int],
    candidates: list,
    claimed_paths: set[str],
) -> None:
    if len(pending_fallback_indices) != 1:
        return

    fallback_path = _select_fallback_jsonl_path(
        project_path=project_path,
        candidates=candidates,
        claimed_paths=claimed_paths,
    )
    if not fallback_path:
        return

    pending_index = pending_fallback_indices[0]
    pending_entry = discovered_entries[pending_index]
    discovered_entries[pending_index] = build_entry(
        role=pending_entry.role,
        team_id=pending_entry.team_id,
        session_id=pending_entry.session_id,
        tty=pending_entry.tty,
        title=pending_entry.title,
        badge=pending_entry.badge,
        jsonl_path=fallback_path,
        project_path=pending_entry.project_path,
        backend_id=pending_entry.backend_id,
        provider=pending_entry.provider,
        launch_mode=pending_entry.launch_mode,
        upstream_session_id=pending_entry.upstream_session_id,
    )


def _cmd_discover(registry: MeshLiteRegistry, project: str) -> int:
    project_path = _project_path(project)
    
    try:
        import iterm2
    except ImportError:
        raise SystemExit("Error: Python package 'iterm2' not found. Ensure you are running within the correct environment.")

    from scripts.mesh_iterm_control import _mesh_sessions

    async def _run(connection):
        app = await iterm2.async_get_app(connection)
        if app is None:
            raise RuntimeError("iTerm2 app not available")

        panes = await _mesh_sessions(app, project_path)
        candidates = transcript_candidates(project_path)
        existing_payload = ((registry.load().get("projects") or {}).get(project_path) or {})
        team_id = str(existing_payload.get("team_id") or "").strip()

        if not panes:
            registry.replace_project_roles(project_path, [], team_id=team_id)
            print(f"No live mesh UI panes found for project: {project_path}")
            return

        discovered_entries = []
        unresolved_indices: list[int] = []
        claimed_paths: set[str] = set()

        for pane in panes:
            title = ""
            try:
                title = str(await pane.session.async_get_variable("session.name") or "")
            except Exception:
                pass

            provider, launch_mode, upstream_session_id = _parse_title_metadata(title)

            try:
                tty = str(pane.session.tty or "")
            except Exception:
                tty = ""
                
            badge = ""
            try:
                badge = str(await pane.session.async_get_variable("session.badge") or "")
            except Exception:
                pass

            existing = registry.get(project_path, pane.role)
            jsonl_path = _select_jsonl_path(
                candidates=candidates,
                existing_jsonl_path=existing.jsonl_path if existing else "",
                upstream_session_id=upstream_session_id,
            )

            entry = build_entry(
                role=pane.role,
                team_id=team_id,
                session_id=pane.session.session_id,
                tty=tty,
                title=title,
                badge=badge,
                jsonl_path=jsonl_path,
                project_path=project_path,
                provider=provider,
                launch_mode=launch_mode,
                upstream_session_id=upstream_session_id,
            )
            discovered_entries.append(entry)
            if jsonl_path:
                claimed_paths.add(jsonl_path)
            else:
                unresolved_indices.append(len(discovered_entries) - 1)
            print(f"Discovered role={pane.role} session={pane.session.session_id} tty={tty} provider={provider or '-'} launch={launch_mode or '-'}")

        _apply_fallback_binding(
            project_path=project_path,
            discovered_entries=discovered_entries,
            pending_fallback_indices=unresolved_indices,
            candidates=candidates,
            claimed_paths=claimed_paths,
        )

        registry.replace_project_roles(project_path, discovered_entries, team_id=team_id)

    try:
        iterm2.run_until_complete(_run, retry=False)
    except Exception as exc:
        raise SystemExit(f"Error discovering panes: {exc}")

    return 0


def _cmd_status(registry: MeshLiteRegistry, project: str) -> int:
    data = registry.load()
    projects = data.get("projects") or {}
    if project:
        project_path = _project_path(project)
        projects = {project_path: projects.get(project_path)} if projects.get(project_path) else {}

    if not projects:
        print("No mesh-lite roles registered.")
        return 0

    for project_path, payload in projects.items():
        if not payload:
            continue
        print(f"{project_path}")
        team_id = payload.get("team_id") or "-"
        print(f"  team={team_id}")
        roles = payload.get("roles") or {}
        for role in sorted(roles):
            item = roles[role]
            
            session_id = item.get("session_id") or "-"
            tty = item.get("tty") or "-"
            jsonl_path = item.get("jsonl_path")
            provider = item.get("provider") or "-"
            launch_mode = item.get("launch_mode") or "-"
            upstream_session_id = item.get("upstream_session_id") or "-"
            
            status_text = "bound (relay ready)" if jsonl_path else "unresolved (relay disabled)"
            
            print(f"  {role:<12} [{status_text}]")
            print(f"    ├─ session_id:  {session_id}")
            print(f"    ├─ tty:         {tty}")
            print(f"    ├─ provider:    {provider}")
            print(f"    ├─ launch_mode: {launch_mode}")
            print(f"    ├─ upstream_id: {upstream_session_id}")
            print(f"    └─ jsonl_path:  {jsonl_path or '(missing)'}")
    return 0


def _cmd_probe(project: str) -> int:
    project_path = _project_path(project)
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


def _cmd_relay_last(
    registry: MeshLiteRegistry,
    project: str,
    source_role: str,
    target_role: str,
    dry_run: bool,
) -> int:
    project_path = _project_path(project)
    source = registry.get(project_path, source_role)
    if source is None:
        raise SystemExit(f"Error: Source role '{source_role}' not registered for project: {project_path}")
    target = registry.get(project_path, target_role)
    if target is None:
        raise SystemExit(f"Error: Target role '{target_role}' not registered for project: {project_path}")

    if not source.jsonl_path:
        raise SystemExit(f"Error: Source role '{source_role}' has no valid transcript binding.")

    reply = extract_last_assistant_msg(Path(source.jsonl_path))
    if not reply:
        raise SystemExit(f"Error: No assistant reply found in transcript: {source.jsonl_path}")

    live_target = get_session(target.session_id)
    if live_target is None:
        raise SystemExit(f"Error: Target live session not found in iTerm2: {target.session_id}")

    safe, reason = ensure_safe_target(live_target.tty)
    if not safe:
        raise SystemExit(f"Error: Refusing injection into target {target.session_id} ({live_target.tty}): {reason}")

    print(f"Source transcript: {source.jsonl_path}")
    print(f"Target tty: {live_target.tty} foreground={reason}")
    if dry_run:
        print("---")
        print(reply)
        return 0

    from scripts.mesh_lite.iterm import send_line

    send_line(live_target.session_id, reply)
    print(f"Relayed {len(reply)} chars from {source_role} to {target_role}")
    return 0


def main() -> int:
    args = _parse_args()
    registry = _registry(args.registry)

    try:
        if args.cmd == "discover":
            return _cmd_discover(
                registry,
                project=args.project,
            )
        if args.cmd == "status":
            return _cmd_status(registry, project=args.project)
        if args.cmd == "probe":
            return _cmd_probe(project=args.project)
        if args.cmd == "relay-last":
            return _cmd_relay_last(
                registry,
                project=args.project,
                source_role=args.source_role,
                target_role=args.target_role,
                dry_run=args.dry_run,
            )
    except MeshLiteRegistryError as exc:
        raise SystemExit(f"Error: {exc}") from exc
    raise SystemExit(f"Unsupported command: {args.cmd}")


if __name__ == "__main__":
    raise SystemExit(main())
