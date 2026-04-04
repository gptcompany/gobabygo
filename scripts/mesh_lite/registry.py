#!/usr/bin/env python3
"""Minimal on-disk registry for Mesh Lite live sessions."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from pathlib import Path


class MeshLiteRegistryError(RuntimeError):
    """Raised when the on-disk registry cannot be read safely."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class RoleSession:
    team_id: str
    role: str
    session_id: str
    tty: str
    title: str
    badge: str
    jsonl_path: str
    project_path: str
    updated_at: str
    # Optional future-compatible fields
    backend_id: str | None = None
    provider: str | None = None
    launch_mode: str | None = None
    upstream_session_id: str | None = None


class MeshLiteRegistry:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (Path.home() / ".mesh-lite" / "registry.json")

    def load(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "projects": {}}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise MeshLiteRegistryError(f"Unable to load registry: {self.path}") from exc

    def save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def upsert(self, entry: RoleSession) -> None:
        data = self.load()
        projects = data.setdefault("projects", {})
        project = projects.setdefault(
            entry.project_path,
            {
                "team_id": entry.team_id,
                "project_path": entry.project_path,
                "project_name": Path(entry.project_path).name,
                "updated_at": entry.updated_at,
                "roles": {},
            },
        )
        project["team_id"] = entry.team_id
        project["updated_at"] = entry.updated_at
        roles = project.setdefault("roles", {})
        existing_role = roles.get(entry.role)
        merged_role = dict(existing_role) if isinstance(existing_role, dict) else {}
        merged_role.update(asdict(entry))
        roles[entry.role] = merged_role
        self.save(data)

    def replace_project_roles(self, project_path: str, entries: list[RoleSession], *, team_id: str = "") -> None:
        data = self.load()
        projects = data.setdefault("projects", {})

        if not entries:
            projects.pop(project_path, None)
            self.save(data)
            return

        existing_project = projects.get(project_path)
        project = dict(existing_project) if isinstance(existing_project, dict) else {}
        existing_roles = project.get("roles")
        if not isinstance(existing_roles, dict):
            existing_roles = {}

        project_name = Path(project_path).name
        updated_at = max((entry.updated_at for entry in entries), default=_now_iso())
        resolved_team_id = team_id or str(project.get("team_id") or "").strip()

        project["team_id"] = resolved_team_id
        project["project_path"] = project_path
        project["project_name"] = project_name
        project["updated_at"] = updated_at

        synced_roles: dict[str, dict] = {}
        for entry in entries:
            existing_role = existing_roles.get(entry.role)
            merged_role = dict(existing_role) if isinstance(existing_role, dict) else {}
            merged_role.update(asdict(entry))
            synced_roles[entry.role] = merged_role

        project["roles"] = synced_roles
        projects[project_path] = project
        self.save(data)

    def project_roles(self, project_path: str) -> dict[str, RoleSession]:
        data = self.load()
        project = (data.get("projects") or {}).get(project_path) or {}
        project_team_id = str(project.get("team_id") or "").strip()
        roles = project.get("roles") or {}
        result: dict[str, RoleSession] = {}
        
        valid_keys = {f.name for f in fields(RoleSession)}
        
        for role, payload in roles.items():
            try:
                normalized = {k: v for k, v in payload.items() if k in valid_keys}
                normalized.setdefault("team_id", project_team_id)
                result[role] = RoleSession(**normalized)
            except TypeError:
                continue
        return result

    def get(self, project_path: str, role: str) -> RoleSession | None:
        return self.project_roles(project_path).get(role)


def build_entry(
    *,
    role: str,
    team_id: str,
    session_id: str,
    tty: str,
    title: str,
    badge: str,
    jsonl_path: str,
    project_path: str,
    backend_id: str | None = None,
    provider: str | None = None,
    launch_mode: str | None = None,
    upstream_session_id: str | None = None,
) -> RoleSession:
    return RoleSession(
        team_id=team_id,
        role=role,
        session_id=session_id,
        tty=tty,
        title=title,
        badge=badge,
        jsonl_path=jsonl_path,
        project_path=project_path,
        updated_at=_now_iso(),
        backend_id=backend_id,
        provider=provider,
        launch_mode=launch_mode,
        upstream_session_id=upstream_session_id,
    )
