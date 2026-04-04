from pathlib import Path

import pytest

from scripts.mesh_lite.registry import MeshLiteRegistry, MeshLiteRegistryError, build_entry


def test_registry_upsert_and_get_round_trip(tmp_path: Path) -> None:
    registry = MeshLiteRegistry(tmp_path / "registry.json")
    entry = build_entry(
        role="boss",
        team_id="team-1",
        session_id="SESSION-1",
        tty="/dev/ttys001",
        title="boss",
        badge="boss",
        jsonl_path="/tmp/boss.jsonl",
        project_path="/tmp/repo",
        backend_id="iterm",
        provider="claude",
        launch_mode="split",
        upstream_session_id="UP-123",
    )

    registry.upsert(entry)
    loaded = registry.get("/tmp/repo", "boss")

    assert loaded is not None
    assert loaded.session_id == "SESSION-1"
    assert loaded.jsonl_path == "/tmp/boss.jsonl"
    assert loaded.backend_id == "iterm"
    assert loaded.provider == "claude"
    assert loaded.launch_mode == "split"
    assert loaded.upstream_session_id == "UP-123"


def test_registry_ignores_unknown_future_fields(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    path.write_text(
        """
{
  "version": 1,
  "projects": {
    "/tmp/repo": {
      "team_id": "team-1",
      "roles": {
        "boss": {
          "role": "boss",
          "session_id": "S1",
          "tty": "/dev/ttys001",
          "title": "boss",
          "badge": "boss",
          "jsonl_path": "",
          "project_path": "/tmp/repo",
          "updated_at": "2026-04-03T00:00:00+00:00",
          "some_future_field": "hello"
        }
      }
    }
  }
}
""".strip(),
        encoding="utf-8",
    )
    registry = MeshLiteRegistry(path)

    role = registry.get("/tmp/repo", "boss")
    assert role is not None
    assert role.session_id == "S1"
    assert getattr(role, "some_future_field", None) is None


def test_registry_keeps_multiple_roles_per_project(tmp_path: Path) -> None:
    registry = MeshLiteRegistry(tmp_path / "registry.json")
    registry.upsert(
        build_entry(
            role="boss",
            team_id="team-1",
            session_id="S1",
            tty="/dev/ttys001",
            title="boss",
            badge="boss",
            jsonl_path="/tmp/boss.jsonl",
            project_path="/tmp/repo",
        )
    )
    registry.upsert(
        build_entry(
            role="president",
            team_id="team-1",
            session_id="S2",
            tty="/dev/ttys002",
            title="president",
            badge="president",
            jsonl_path="/tmp/president.jsonl",
            project_path="/tmp/repo",
        )
    )

    roles = registry.project_roles("/tmp/repo")
    assert set(roles) == {"boss", "president"}


def test_registry_backfills_team_id_from_project_payload(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    path.write_text(
        """
{
  "version": 1,
  "projects": {
    "/tmp/repo": {
      "team_id": "team-legacy",
      "roles": {
        "boss": {
          "role": "boss",
          "session_id": "S1",
          "tty": "/dev/ttys001",
          "title": "boss",
          "badge": "boss",
          "jsonl_path": "",
          "project_path": "/tmp/repo",
          "updated_at": "2026-04-03T00:00:00+00:00"
        }
      }
    }
  }
}
""".strip(),
        encoding="utf-8",
    )
    registry = MeshLiteRegistry(path)

    role = registry.get("/tmp/repo", "boss")
    assert role is not None
    assert role.team_id == "team-legacy"


def test_registry_upsert_preserves_unknown_role_fields(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    path.write_text(
        """
{
  "version": 1,
  "projects": {
    "/tmp/repo": {
      "team_id": "team-1",
      "project_path": "/tmp/repo",
      "project_name": "repo",
      "updated_at": "2026-04-03T00:00:00+00:00",
      "roles": {
        "boss": {
          "role": "boss",
          "session_id": "OLD",
          "tty": "/dev/ttys001",
          "title": "boss",
          "badge": "boss",
          "jsonl_path": "/tmp/old.jsonl",
          "project_path": "/tmp/repo",
          "updated_at": "2026-04-03T00:00:00+00:00",
          "provider": "claude",
          "future_marker": "keep-me"
        }
      }
    }
  }
}
""".strip(),
        encoding="utf-8",
    )
    registry = MeshLiteRegistry(path)

    registry.upsert(
        build_entry(
            role="boss",
            team_id="team-1",
            session_id="NEW",
            tty="/dev/ttys009",
            title="boss",
            badge="boss",
            jsonl_path="/tmp/new.jsonl",
            project_path="/tmp/repo",
        )
    )

    payload = registry.load()["projects"]["/tmp/repo"]["roles"]["boss"]
    assert payload["session_id"] == "NEW"
    assert payload["future_marker"] == "keep-me"


def test_registry_upsert_raises_on_malformed_registry_instead_of_wiping(tmp_path: Path) -> None:
    path = tmp_path / "registry.json"
    original = '{"version": 1, "projects": '
    path.write_text(original, encoding="utf-8")
    registry = MeshLiteRegistry(path)

    with pytest.raises(MeshLiteRegistryError):
        registry.upsert(
            build_entry(
                role="boss",
                team_id="team-1",
                session_id="SESSION-1",
                tty="/dev/ttys001",
                title="boss",
                badge="boss",
                jsonl_path="/tmp/boss.jsonl",
                project_path="/tmp/repo",
            )
        )

    assert path.read_text(encoding="utf-8") == original


def test_registry_replace_project_roles_prunes_stale_roles(tmp_path: Path) -> None:
    registry = MeshLiteRegistry(tmp_path / "registry.json")
    registry.upsert(
        build_entry(
            role="boss",
            team_id="team-1",
            session_id="S1",
            tty="/dev/ttys001",
            title="boss",
            badge="boss",
            jsonl_path="/tmp/boss.jsonl",
            project_path="/tmp/repo",
        )
    )
    registry.upsert(
        build_entry(
            role="president",
            team_id="team-1",
            session_id="S2",
            tty="/dev/ttys002",
            title="president",
            badge="president",
            jsonl_path="/tmp/president.jsonl",
            project_path="/tmp/repo",
        )
    )

    registry.replace_project_roles(
        "/tmp/repo",
        [
            build_entry(
                role="boss",
                team_id="team-1",
                session_id="S3",
                tty="/dev/ttys003",
                title="boss",
                badge="boss",
                jsonl_path="/tmp/boss-new.jsonl",
                project_path="/tmp/repo",
            )
        ],
        team_id="team-1",
    )

    roles = registry.project_roles("/tmp/repo")
    assert set(roles) == {"boss"}
    assert roles["boss"].session_id == "S3"


def test_registry_replace_project_roles_removes_empty_project(tmp_path: Path) -> None:
    registry = MeshLiteRegistry(tmp_path / "registry.json")
    registry.upsert(
        build_entry(
            role="boss",
            team_id="team-1",
            session_id="S1",
            tty="/dev/ttys001",
            title="boss",
            badge="boss",
            jsonl_path="/tmp/boss.jsonl",
            project_path="/tmp/repo",
        )
    )

    registry.replace_project_roles("/tmp/repo", [], team_id="team-1")

    assert registry.project_roles("/tmp/repo") == {}
    assert "/tmp/repo" not in (registry.load().get("projects") or {})
