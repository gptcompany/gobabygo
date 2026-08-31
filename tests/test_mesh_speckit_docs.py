from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_primary_docs_define_one_way_development_ledger() -> None:
    documents = {
        name: (ROOT / name).read_text(encoding="utf-8")
        for name in ("README.md", "QUICKSTART.md", "MESH_LIVE.md", "ARCHITECTURE.md")
    }

    assert "mesh speckit github init|plan|check|install-caller" in documents["README.md"]
    for command in (
        "mesh speckit github init specs/001-feature --apply",
        "mesh speckit github plan specs/001-feature",
        "mesh speckit github check specs/001-feature",
        "mesh speckit github install-caller /path/to/repo --runtime-ref <40-char-sha>",
    ):
        assert command in documents["QUICKSTART.md"]
    quickstart = " ".join(documents["QUICKSTART.md"].split())
    assert "planning PR contains specification" in quickstart
    assert "bare `Tnnn` deduplication can collide" in documents["QUICKSTART.md"]
    assert "does not delegate source implementation" in documents["MESH_LIVE.md"]
    assert "Worker prose, idle state, router history" in documents["MESH_LIVE.md"]
    assert "Task identity and completion | `tasks.md`" in documents["ARCHITECTURE.md"]
    assert "GitHub Issues are derived one-way" in documents["ARCHITECTURE.md"]
    assert "stages a missing managed caller and feature binding automatically" in " ".join(
        documents["README.md"].split()
    )
    assert "Workers do not own onboarding or issue mutation" in " ".join(
        documents["QUICKSTART.md"].split()
    )
    assert "workers never become ledger writers" in documents["ARCHITECTURE.md"]
    assert "Planned review scope, rounds, and release gate" in documents["ARCHITECTURE.md"]


def test_speckit_docs_define_transactional_review_authority() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    quickstart = (ROOT / "QUICKSTART.md").read_text(encoding="utf-8")
    runbook = (ROOT / "MESH_LIVE.md").read_text(encoding="utf-8")

    for document in (readme, quickstart, runbook):
        normalized = " ".join(document.split())
        assert "review-ledger.json" in normalized
        assert "deploy" in normalized
    assert "global revision compare-and-swap" in " ".join(quickstart.split())
    assert "RELEASE_PASSED" in quickstart
    assert "does not parse review prose" in " ".join(runbook.split())


def test_docs_keep_runtime_rollout_pinned_and_non_vendored() -> None:
    quickstart = (ROOT / "QUICKSTART.md").read_text(encoding="utf-8")
    tasks = (
        ROOT / "specs/001-development-orchestration/tasks.md"
    ).read_text(encoding="utf-8")

    assert "must not copy the Python reconciler or reference a mutable branch" in " ".join(
        quickstart.split()
    )
    assert "pinned to an immutable reviewed Gobabygo commit" in tasks
    assert "must not vendor the Python reconciler" in tasks
    assert "`--accept-pin-update`" in quickstart
