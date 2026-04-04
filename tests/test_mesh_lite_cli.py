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


def test_select_jsonl_path_falls_back_to_unique_exact_cwd_candidate(monkeypatch, tmp_path: Path) -> None:
    unique = tmp_path / "unique.jsonl"
    unique.write_text("", encoding="utf-8")
    candidate = TranscriptCandidate(
        path=unique,
        session_id="OTHER",
        cwd="/tmp/repo",
        last_modified=1.0,
        assistant_text="usable",
    )

    monkeypatch.setattr("scripts.mesh_lite.cli.transcript_candidates", lambda _project: [candidate])

    selected = _select_jsonl_path(
        project_path="/tmp/repo",
        existing_jsonl_path="",
        upstream_session_id=None,
    )

    assert selected == str(unique)


def test_select_jsonl_path_leaves_multiple_candidates_with_single_reply_unresolved(monkeypatch, tmp_path: Path) -> None:
    replied = tmp_path / "replied.jsonl"
    pending = tmp_path / "pending.jsonl"
    replied.write_text("", encoding="utf-8")
    pending.write_text("", encoding="utf-8")

    candidates = [
        TranscriptCandidate(
            path=replied,
            session_id="ONE",
            cwd="/tmp/repo",
            last_modified=2.0,
            assistant_text="reply one",
        ),
        TranscriptCandidate(
            path=pending,
            session_id="TWO",
            cwd="/tmp/repo",
            last_modified=1.0,
            assistant_text=None,
        ),
    ]

    monkeypatch.setattr("scripts.mesh_lite.cli.transcript_candidates", lambda _project: candidates)

    selected = _select_jsonl_path(
        project_path="/tmp/repo",
        existing_jsonl_path="",
        upstream_session_id=None,
    )

    assert selected == ""


def test_select_jsonl_path_leaves_ambiguous_project_candidates_unresolved(monkeypatch, tmp_path: Path) -> None:
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    first.write_text("", encoding="utf-8")
    second.write_text("", encoding="utf-8")

    candidates = [
        TranscriptCandidate(
            path=first,
            session_id="ONE",
            cwd="/tmp/repo",
            last_modified=1.0,
            assistant_text="reply one",
        ),
        TranscriptCandidate(
            path=second,
            session_id="TWO",
            cwd="/tmp/repo",
            last_modified=2.0,
            assistant_text="reply two",
        ),
    ]

    monkeypatch.setattr("scripts.mesh_lite.cli.transcript_candidates", lambda _project: candidates)

    selected = _select_jsonl_path(
        project_path="/tmp/repo",
        existing_jsonl_path="",
        upstream_session_id=None,
    )

    assert selected == ""


def test_select_jsonl_path_leaves_ambiguous_prefix_matches_unresolved(monkeypatch, tmp_path: Path) -> None:
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    first.write_text("", encoding="utf-8")
    second.write_text("", encoding="utf-8")

    candidates = [
        TranscriptCandidate(
            path=first,
            session_id="UP-1234567890AAAA",
            cwd="/tmp/repo",
            last_modified=1.0,
            assistant_text="reply one",
        ),
        TranscriptCandidate(
            path=second,
            session_id="UP-1234567890BBBB",
            cwd="/tmp/repo",
            last_modified=2.0,
            assistant_text="reply two",
        ),
    ]

    monkeypatch.setattr("scripts.mesh_lite.cli.transcript_candidates", lambda _project: candidates)

    selected = _select_jsonl_path(
        project_path="/tmp/repo",
        existing_jsonl_path="",
        upstream_session_id="UP-1234567890",
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


import pytest

def test_cmd_relay_last_fails_on_missing_source_role(tmp_path: Path) -> None:
    from scripts.mesh_lite.cli import _cmd_relay_last, _project_path
    from scripts.mesh_lite.registry import MeshLiteRegistry

    registry = MeshLiteRegistry(tmp_path / "registry.json")
    repo_path = _project_path("/tmp/repo")
    
    with pytest.raises(SystemExit, match=f"Error: Source role 'missing' not registered for project: {repo_path}"):
        _cmd_relay_last(registry, "/tmp/repo", "missing", "target", False)


def test_cmd_relay_last_fails_on_missing_target_role(tmp_path: Path) -> None:
    from scripts.mesh_lite.cli import _cmd_relay_last, _project_path
    from scripts.mesh_lite.registry import MeshLiteRegistry, build_entry

    registry = MeshLiteRegistry(tmp_path / "registry.json")
    repo_path = _project_path("/tmp/repo")
    registry.upsert(build_entry(role="source", team_id="1", session_id="S1", tty="T1", title="", badge="", jsonl_path="/path", project_path=repo_path))
    
    with pytest.raises(SystemExit, match=f"Error: Target role 'missing' not registered for project: {repo_path}"):
        _cmd_relay_last(registry, "/tmp/repo", "source", "missing", False)


def test_cmd_relay_last_fails_on_unresolved_transcript_binding(tmp_path: Path) -> None:
    from scripts.mesh_lite.cli import _cmd_relay_last, _project_path
    from scripts.mesh_lite.registry import MeshLiteRegistry, build_entry

    registry = MeshLiteRegistry(tmp_path / "registry.json")
    repo_path = _project_path("/tmp/repo")
    registry.upsert(build_entry(role="source", team_id="1", session_id="S1", tty="T1", title="", badge="", jsonl_path="", project_path=repo_path))
    registry.upsert(build_entry(role="target", team_id="1", session_id="S2", tty="T2", title="", badge="", jsonl_path="", project_path=repo_path))

    with pytest.raises(SystemExit, match="Error: Source role 'source' has no valid transcript binding."):
        _cmd_relay_last(registry, "/tmp/repo", "source", "target", False)


def test_cmd_relay_last_fails_on_missing_assistant_reply(monkeypatch, tmp_path: Path) -> None:
    from scripts.mesh_lite.cli import _cmd_relay_last, _project_path
    from scripts.mesh_lite.registry import MeshLiteRegistry, build_entry

    registry = MeshLiteRegistry(tmp_path / "registry.json")
    repo_path = _project_path("/tmp/repo")
    registry.upsert(build_entry(role="source", team_id="1", session_id="S1", tty="T1", title="", badge="", jsonl_path="/path.jsonl", project_path=repo_path))
    registry.upsert(build_entry(role="target", team_id="1", session_id="S2", tty="T2", title="", badge="", jsonl_path="", project_path=repo_path))

    monkeypatch.setattr("scripts.mesh_lite.cli.extract_last_assistant_msg", lambda _: "")

    with pytest.raises(SystemExit, match="Error: No assistant reply found in transcript: /path.jsonl"):
        _cmd_relay_last(registry, "/tmp/repo", "source", "target", False)


def test_cmd_relay_last_fails_on_missing_live_target(monkeypatch, tmp_path: Path) -> None:
    from scripts.mesh_lite.cli import _cmd_relay_last, _project_path
    from scripts.mesh_lite.registry import MeshLiteRegistry, build_entry

    registry = MeshLiteRegistry(tmp_path / "registry.json")
    repo_path = _project_path("/tmp/repo")
    registry.upsert(build_entry(role="source", team_id="1", session_id="S1", tty="T1", title="", badge="", jsonl_path="/path.jsonl", project_path=repo_path))
    registry.upsert(build_entry(role="target", team_id="1", session_id="S2", tty="T2", title="", badge="", jsonl_path="", project_path=repo_path))

    monkeypatch.setattr("scripts.mesh_lite.cli.extract_last_assistant_msg", lambda _: "hello")
    monkeypatch.setattr("scripts.mesh_lite.cli.get_session", lambda _: None)

    with pytest.raises(SystemExit, match="Error: Target live session not found in iTerm2: S2"):
        _cmd_relay_last(registry, "/tmp/repo", "source", "target", False)


def test_cmd_relay_last_fails_on_unsafe_target(monkeypatch, tmp_path: Path) -> None:
    from scripts.mesh_lite.cli import _cmd_relay_last, _project_path
    from scripts.mesh_lite.registry import MeshLiteRegistry, build_entry
    from scripts.mesh_lite.iterm import SessionInfo

    registry = MeshLiteRegistry(tmp_path / "registry.json")
    repo_path = _project_path("/tmp/repo")
    registry.upsert(build_entry(role="source", team_id="1", session_id="S1", tty="T1", title="", badge="", jsonl_path="/path.jsonl", project_path=repo_path))
    registry.upsert(build_entry(role="target", team_id="1", session_id="S2", tty="T2", title="", badge="", jsonl_path="", project_path=repo_path))

    monkeypatch.setattr("scripts.mesh_lite.cli.extract_last_assistant_msg", lambda _: "hello")
    monkeypatch.setattr("scripts.mesh_lite.cli.get_session", lambda _: SessionInfo(session_id="S2", window_index=1, tab_index=1, session_index=1, tty="T2", title="", badge="", command=""))
    monkeypatch.setattr("scripts.mesh_lite.cli.ensure_safe_target", lambda _: (False, "unsafe command: vim"))

    with pytest.raises(SystemExit, match=r"Error: Refusing injection into target S2 \(T2\): unsafe command: vim"):
        _cmd_relay_last(registry, "/tmp/repo", "source", "target", False)


def test_cmd_relay_last_dry_run_does_not_inject(capsys, monkeypatch, tmp_path: Path) -> None:
    from scripts.mesh_lite.cli import _cmd_relay_last, _project_path
    from scripts.mesh_lite.registry import MeshLiteRegistry, build_entry
    from scripts.mesh_lite.iterm import SessionInfo

    registry = MeshLiteRegistry(tmp_path / "registry.json")
    repo_path = _project_path("/tmp/repo")
    registry.upsert(build_entry(role="source", team_id="1", session_id="S1", tty="T1", title="", badge="", jsonl_path="/path.jsonl", project_path=repo_path))
    registry.upsert(build_entry(role="target", team_id="1", session_id="S2", tty="T2", title="", badge="", jsonl_path="", project_path=repo_path))

    monkeypatch.setattr("scripts.mesh_lite.cli.extract_last_assistant_msg", lambda _: "hello world")
    monkeypatch.setattr("scripts.mesh_lite.cli.get_session", lambda _: SessionInfo(session_id="S2", window_index=1, tab_index=1, session_index=1, tty="T2", title="", badge="", command=""))
    monkeypatch.setattr("scripts.mesh_lite.cli.ensure_safe_target", lambda _: (True, "bash"))
    
    injected = []
    monkeypatch.setattr("scripts.mesh_lite.iterm.send_line", lambda s, t: injected.append((s, t)))

    _cmd_relay_last(registry, "/tmp/repo", "source", "target", True)

    assert not injected
    captured = capsys.readouterr()
    assert "hello world" in captured.out


def test_cmd_relay_last_success(capsys, monkeypatch, tmp_path: Path) -> None:
    from scripts.mesh_lite.cli import _cmd_relay_last, _project_path
    from scripts.mesh_lite.registry import MeshLiteRegistry, build_entry
    from scripts.mesh_lite.iterm import SessionInfo

    registry = MeshLiteRegistry(tmp_path / "registry.json")
    repo_path = _project_path("/tmp/repo")
    registry.upsert(build_entry(role="source", team_id="1", session_id="S1", tty="T1", title="", badge="", jsonl_path="/path.jsonl", project_path=repo_path))
    registry.upsert(build_entry(role="target", team_id="1", session_id="S2", tty="T2", title="", badge="", jsonl_path="", project_path=repo_path))

    monkeypatch.setattr("scripts.mesh_lite.cli.extract_last_assistant_msg", lambda _: "hello world")
    monkeypatch.setattr("scripts.mesh_lite.cli.get_session", lambda _: SessionInfo(session_id="S2", window_index=1, tab_index=1, session_index=1, tty="T2", title="", badge="", command=""))
    monkeypatch.setattr("scripts.mesh_lite.cli.ensure_safe_target", lambda _: (True, "bash"))
    
    injected = []
    monkeypatch.setattr("scripts.mesh_lite.iterm.send_line", lambda s, t: injected.append((s, t)))

    _cmd_relay_last(registry, "/tmp/repo", "source", "target", False)

    assert injected == [("S2", "hello world")]
    captured = capsys.readouterr()
    assert "Relayed 11 chars from source to target" in captured.out
