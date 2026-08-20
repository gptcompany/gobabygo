from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_readme_documents_probity_as_repo_opt_in() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "mesh probity install --apply" in text
    assert "exactly one `probity.config.ts|mts|js|mjs`" in text
    assert "Repositories without a config remain unaffected" in text
    assert "not Antigravity through its CLI" in text
    assert "Do not stack Probity and TDD Guard" in text


def test_mesh_live_documents_runtime_boundaries() -> None:
    text = (ROOT / "MESH_LIVE.md").read_text(encoding="utf-8")

    assert "dispatcher returns immediately" in text
    assert "Existing Codex sessions do not reload hook configuration" in text
    assert "Mesh Live never kills them during installation" in text
    assert "YOLO permissions are unchanged" in text
