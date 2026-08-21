from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def module():
    script = Path(__file__).resolve().parents[1] / "scripts" / "mesh_speckit_github.py"
    spec = importlib.util.spec_from_file_location("mesh_speckit_github", script)
    assert spec and spec.loader
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


def make_feature(tmp_path: Path, *, binding: dict | None = None, tasks: str = "") -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    feature = repo / "specs" / "001-example"
    feature.mkdir(parents=True)
    (repo / ".git").mkdir()
    payload = binding or {
        "schema": "mesh.speckit.github-ledger.v1",
        "feature_id": "example-a1b2c3d4",
        "repository": "owner/repo",
        "enabled": True,
    }
    (feature / "github-ledger.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    (feature / "tasks.md").write_text(tasks, encoding="utf-8")
    return repo, feature


def test_load_feature_parses_official_task_markers(module, tmp_path: Path) -> None:
    repo, feature = make_feature(
        tmp_path,
        tasks="""# Tasks

- [ ] T001 [P] [US1] Build the parser in `scripts/tool.py`
- [X] T002 [US2] Close the completed task
""",
    )

    loaded = module.load_feature(repo, feature)

    assert loaded.binding.feature_id == "example-a1b2c3d4"
    assert loaded.binding.repository == "owner/repo"
    assert loaded.display_name == "001-example"
    assert loaded.tasks_file == repo / "specs/001-example/tasks.md"
    assert [task.task_id for task in loaded.tasks] == ["T001", "T002"]
    assert loaded.tasks[0].parallel is True
    assert loaded.tasks[0].story == "US1"
    assert loaded.tasks[0].completed is False
    assert loaded.tasks[1].completed is True


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"schema": "wrong"}, "unsupported ledger schema"),
        ({"feature_id": "short"}, "invalid feature_id"),
        ({"repository": "Owner/Repo"}, "canonical lowercase"),
        ({"repository": "not-a-repository"}, "invalid repository"),
        ({"enabled": False}, "not enabled"),
        ({"extra": "field"}, "unknown binding fields"),
    ],
)
def test_load_feature_rejects_invalid_bindings(
    module, tmp_path: Path, change: dict, message: str
) -> None:
    binding = {
        "schema": "mesh.speckit.github-ledger.v1",
        "feature_id": "example-a1b2c3d4",
        "repository": "owner/repo",
        "enabled": True,
    }
    binding.update(change)
    repo, feature = make_feature(
        tmp_path, binding=binding, tasks="- [ ] T001 Valid task\n"
    )

    with pytest.raises(module.LedgerError, match=message):
        module.load_feature(repo, feature)


@pytest.mark.parametrize(
    ("tasks", "message"),
    [
        ("- [ ] T001 First\n- [x] T001 Duplicate\n", "duplicate task ID T001"),
        ("- [~] T001 In progress\n", "malformed task line"),
        ("- [ ] T01 Too short\n", "malformed task line"),
        ("- [ ] T001 [UNKNOWN] Bad marker\n", "unsupported task marker"),
        ("- [ ] T001\n", "malformed task line"),
        ("# No tasks\n", "contains no Spec Kit tasks"),
    ],
)
def test_load_feature_rejects_ambiguous_task_files(
    module, tmp_path: Path, tasks: str, message: str
) -> None:
    repo, feature = make_feature(tmp_path, tasks=tasks)

    with pytest.raises(module.LedgerError, match=message):
        module.load_feature(repo, feature)


def test_load_feature_rejects_paths_outside_repo(module, tmp_path: Path) -> None:
    repo, _ = make_feature(tmp_path, tasks="- [ ] T001 Valid\n")
    _, outside = make_feature(tmp_path / "other", tasks="- [ ] T001 Outside\n")

    with pytest.raises(module.LedgerError, match="inside the exact repository root"):
        module.load_feature(repo, outside)


def test_load_feature_rejects_symlinked_binding(module, tmp_path: Path) -> None:
    repo, feature = make_feature(tmp_path, tasks="- [ ] T001 Valid\n")
    binding = feature / "github-ledger.json"
    external = tmp_path / "external.json"
    external.write_text(binding.read_text(encoding="utf-8"), encoding="utf-8")
    binding.unlink()
    binding.symlink_to(external)

    with pytest.raises(module.LedgerError, match="must not be a symlink"):
        module.load_feature(repo, feature)


def test_task_identity_and_issue_rendering_are_canonical(module, tmp_path: Path) -> None:
    repo, feature = make_feature(
        tmp_path,
        tasks="- [ ] T001 [US1]   Build   a deterministic ledger   \n",
    )
    loaded = module.load_feature(repo, feature)
    task = loaded.tasks[0]

    assert module.task_key(loaded.binding, task) == (
        "owner/repo:example-a1b2c3d4:T001"
    )
    rendered = module.render_issue(loaded, task)
    assert rendered.title == "[001-example] T001: Build a deterministic ledger"
    assert rendered.labels == (
        "speckit-task",
        "speckit:example-a1b2c3d4",
    )
    assert rendered.body.startswith(
        "<!-- mesh-speckit-task:v1 repo=owner/repo "
        "feature=example-a1b2c3d4 task=T001 -->\n"
    )
    assert "`owner/repo:example-a1b2c3d4:T001`" in rendered.body
    assert "`specs/001-example/tasks.md`" in rendered.body
    assert rendered.body.endswith("\n")


def test_feature_rename_changes_display_not_identity(module, tmp_path: Path) -> None:
    repo, feature = make_feature(tmp_path, tasks="- [ ] T001 Stable task\n")
    before = module.load_feature(repo, feature)
    renamed = feature.with_name("renamed-feature")
    feature.rename(renamed)
    after = module.load_feature(repo, renamed)

    assert module.task_key(before.binding, before.tasks[0]) == module.task_key(
        after.binding, after.tasks[0]
    )
    assert module.render_issue(before, before.tasks[0]).title != module.render_issue(
        after, after.tasks[0]
    ).title


def test_long_title_is_bounded_without_truncating_body(module, tmp_path: Path) -> None:
    description = "Implement " + ("carefully " * 50)
    repo, feature = make_feature(
        tmp_path, tasks=f"- [ ] T001 {description}\n"
    )
    loaded = module.load_feature(repo, feature)
    rendered = module.render_issue(loaded, loaded.tasks[0])

    assert len(rendered.title) <= 256
    assert rendered.title.endswith("...")
    assert description.strip() in rendered.body
