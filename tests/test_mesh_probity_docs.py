from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def test_repo_probity_keeps_enough_history_for_red_green_evidence() -> None:
    config = (ROOT / "probity.config.mjs").read_text(encoding="utf-8")

    assert re.search(r"enforceTdd\(\{\s*maxEvents:\s*30\s*\}\)", config)


def test_readme_documents_probity_as_repo_opt_in() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "mesh probity install --apply" in text
    assert "exactly one `probity.config.ts|mts|js|mjs`" in text
    assert "Repositories without a config remain unaffected" in text
    assert "but not Antigravity" in text
    assert "legacy global Claude TDD Guard" in text
    assert "use `/hooks` to review and trust" in text
    assert "--replace-tdd-guard" in text
    assert "*.mesh-probity.bak" in text


def test_mesh_live_documents_runtime_boundaries() -> None:
    text = (ROOT / "MESH_LIVE.md").read_text(encoding="utf-8")

    assert "dispatcher returns immediately" in text
    assert "Existing Claude and Codex sessions do not reload" in text
    assert "Mesh Live never kills them during installation" in text
    assert "YOLO permissions are unchanged" in text
    assert "Until then Codex reports the hook as" in text
    assert "Do not make routine workers use" in text
    assert "manual-unknown" in text
