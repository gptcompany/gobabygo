from pathlib import Path

from scripts.mesh_lite.jsonl import extract_last_assistant_msg, resolve_best_candidate


def test_extract_last_assistant_msg_reads_latest_message(tmp_path: Path) -> None:
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(
        "\n".join(
            [
                '{"type":"user","message":{"role":"user","content":[{"type":"text","text":"hi"}]}}',
                '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"hello"}]}}',
                '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"final reply"}]}}',
            ]
        ),
        encoding="utf-8",
    )

    assert extract_last_assistant_msg(transcript) == "final reply"


def test_extract_last_assistant_msg_ignores_invalid_lines(tmp_path: Path) -> None:
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(
        "\n".join(
            [
                "not-json",
                '{"message":{"role":"assistant","content":[{"type":"text","text":"usable"}]}}',
            ]
        ),
        encoding="utf-8",
    )

    assert extract_last_assistant_msg(transcript) == "usable"


def test_resolve_best_candidate_prefers_exact_cwd_and_recent_file(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    slug_dir = tmp_path / "-tmp-repo"
    slug_dir.mkdir()
    monkeypatch.setattr("scripts.mesh_lite.jsonl.CLAUDE_PROJECTS_DIR", tmp_path)
    monkeypatch.setattr("scripts.mesh_lite.jsonl._project_slug", lambda _project: "-tmp-repo")

    older = slug_dir / "older.jsonl"
    older.write_text(
        '{"cwd":"/elsewhere","message":{"role":"assistant","content":[{"type":"text","text":"old"}]}}',
        encoding="utf-8",
    )
    newer = slug_dir / "newer.jsonl"
    newer.write_text(
        '{"cwd":"' + str(project) + '","message":{"role":"assistant","content":[{"type":"text","text":"new"}]}}',
        encoding="utf-8",
    )

    candidate = resolve_best_candidate(str(project), active_within_seconds=999999)
    assert candidate is not None
    assert candidate.path == newer
