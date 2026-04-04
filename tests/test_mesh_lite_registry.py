from pathlib import Path

from scripts.mesh_lite.registry import MeshLiteRegistry, build_entry


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
