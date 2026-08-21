from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_primary_docs_define_one_way_development_ledger() -> None:
    documents = {
        name: (ROOT / name).read_text(encoding="utf-8")
        for name in ("README.md", "QUICKSTART.md", "MESH_LIVE.md", "ARCHITECTURE.md")
    }

    assert "mesh speckit github init|plan|check" in documents["README.md"]
    for command in (
        "mesh speckit github init specs/001-feature --apply",
        "mesh speckit github plan specs/001-feature",
        "mesh speckit github check specs/001-feature",
    ):
        assert command in documents["QUICKSTART.md"]
    quickstart = " ".join(documents["QUICKSTART.md"].split())
    assert "planning PR contains specification" in quickstart
    assert "bare `Tnnn` deduplication can collide" in documents["QUICKSTART.md"]
    assert "does not delegate source implementation" in documents["MESH_LIVE.md"]
    assert "Worker prose, idle state, router history" in documents["MESH_LIVE.md"]
    assert "Task identity and completion | `tasks.md`" in documents["ARCHITECTURE.md"]
    assert "GitHub Issues are derived one-way" in documents["ARCHITECTURE.md"]


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
