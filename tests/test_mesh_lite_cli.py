from pathlib import Path

from scripts.mesh_lite.cli import _parse_title_metadata, _select_jsonl_path
from scripts.mesh_lite.jsonl import TranscriptCandidate


def test_parse_title_metadata_extracts_provider_launch_and_upstream() -> None:
    provider, launch_mode, upstream = _parse_title_metadata("boss (split:claude:ABC123) | /tmp/repo")

    assert provider == "claude"
    assert launch_mode == "split"
    assert upstream == "ABC123"


def test_select_jsonl_path_prefers_existing_binding(tmp_path: Path) -> None:
    existing = tmp_path / "bound.jsonl"
    existing.write_text("", encoding="utf-8")

    selected = _select_jsonl_path(
        project_path="/tmp/repo",
        existing_jsonl_path=str(existing),
        upstream_session_id=None,
    )

    assert selected == str(existing)


def test_select_jsonl_path_prefers_upstream_session_match(monkeypatch, tmp_path: Path) -> None:
    matched = tmp_path / "matched.jsonl"
    fallback = tmp_path / "fallback.jsonl"
    matched.write_text("", encoding="utf-8")
    fallback.write_text("", encoding="utf-8")

    candidates = [
        TranscriptCandidate(
            path=fallback,
            session_id="OTHER",
            cwd="/tmp/repo",
            last_modified=1.0,
            assistant_text="fallback",
        ),
        TranscriptCandidate(
            path=matched,
            session_id="UP-1234567890ABCD",
            cwd="/tmp/repo",
            last_modified=2.0,
            assistant_text="matched",
        ),
    ]

    monkeypatch.setattr("scripts.mesh_lite.cli.transcript_candidates", lambda _project: candidates)

    selected = _select_jsonl_path(
        project_path="/tmp/repo",
        existing_jsonl_path="",
        upstream_session_id="UP-123",
    )

    assert selected == str(matched)


def test_select_jsonl_path_leaves_unmatched_role_unresolved(monkeypatch, tmp_path: Path) -> None:
    fallback = tmp_path / "fallback.jsonl"
    fallback.write_text("", encoding="utf-8")
    candidate = TranscriptCandidate(
        path=fallback,
        session_id="OTHER",
        cwd="/tmp/repo",
        last_modified=1.0,
        assistant_text="fallback",
    )

    monkeypatch.setattr("scripts.mesh_lite.cli.transcript_candidates", lambda _project: [candidate])

    selected = _select_jsonl_path(
        project_path="/tmp/repo",
        existing_jsonl_path="",
        upstream_session_id="UP-123",
    )

    assert selected == ""


def test_cmd_status_renders_binding_focused_tree(capsys, tmp_path: Path) -> None:
    from scripts.mesh_lite.cli import _cmd_status
    from scripts.mesh_lite.registry import MeshLiteRegistry, build_entry

    registry = MeshLiteRegistry(tmp_path / "registry.json")
    repo_path = str(tmp_path / "repo")
    
    bound_entry = build_entry(
        role="boss",
        team_id="team-1",
        session_id="S1",
        tty="/dev/ttys001",
        title="boss",
        badge="boss",
        jsonl_path="/tmp/boss.jsonl",
        project_path=repo_path,
        provider="claude",
        launch_mode="split",
        upstream_session_id="UP-123",
    )
    registry.upsert(bound_entry)

    unresolved_entry = build_entry(
        role="president",
        team_id="team-1",
        session_id="S2",
        tty="/dev/ttys002",
        title="president",
        badge="president",
        jsonl_path="",
        project_path=repo_path,
        provider="gemini",
        launch_mode="split",
        upstream_session_id="UP-456",
    )
    registry.upsert(unresolved_entry)

    _cmd_status(registry, repo_path)
    
    captured = capsys.readouterr()
    stdout = captured.out

    assert repo_path in stdout
    assert "team=team-1" in stdout
    
    assert "boss         [bound (relay ready)]" in stdout
    assert "├─ session_id:  S1" in stdout
    assert "├─ provider:    claude" in stdout
    assert "└─ jsonl_path:  /tmp/boss.jsonl" in stdout

    assert "president    [unresolved (relay disabled)]" in stdout
    assert "├─ session_id:  S2" in stdout
    assert "├─ provider:    gemini" in stdout
    assert "└─ jsonl_path:  (missing)" in stdout


def test_cmd_status_empty(capsys, tmp_path: Path) -> None:
    from scripts.mesh_lite.cli import _cmd_status
    from scripts.mesh_lite.registry import MeshLiteRegistry

    registry = MeshLiteRegistry(tmp_path / "registry.json")
    _cmd_status(registry, "")
    
    captured = capsys.readouterr()
    assert "No mesh-lite roles registered." in captured.out
