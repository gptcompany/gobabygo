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


def remote_issue(module, rendered, *, number: int = 1, **changes):
    values = {
        "number": number,
        "title": rendered.title,
        "body": rendered.body,
        "state": rendered.desired_state,
        "labels": rendered.labels,
    }
    values.update(changes)
    return module.RemoteIssue(**values)


def test_plan_creates_missing_tasks_in_numeric_order(module, tmp_path: Path) -> None:
    repo, feature = make_feature(
        tmp_path,
        tasks="- [ ] T010 Later\n- [x] T002 Earlier and complete\n",
    )
    loaded = module.load_feature(repo, feature)

    plan = module.build_plan(loaded, [])

    assert plan.aligned is False
    assert plan.blocking == ()
    assert [(item.task_id, item.operation, item.state) for item in plan.actions] == [
        ("T002", "create", "closed"),
        ("T010", "create", "open"),
    ]


def test_plan_treats_normalized_remote_content_as_aligned(module, tmp_path: Path) -> None:
    repo, feature = make_feature(tmp_path, tasks="- [ ] T001 Stable task\n")
    loaded = module.load_feature(repo, feature)
    rendered = module.render_issue(loaded, loaded.tasks[0])
    body = rendered.body.replace("\n", "\r\n").replace("\r\n", "  \r\n")
    issue = remote_issue(
        module,
        rendered,
        title="  [001-example]   T001: Stable task  ",
        body=body,
        labels=("human-priority", *reversed(rendered.labels)),
    )

    plan = module.build_plan(loaded, [issue])

    assert plan.aligned is True
    assert len(plan.actions) == 1
    assert plan.actions[0].operation == "noop"
    assert plan.actions[0].reasons == ()


def test_plan_updates_machine_fields_and_authoritative_state(module, tmp_path: Path) -> None:
    repo, feature = make_feature(tmp_path, tasks="- [ ] T001 New description\n")
    loaded = module.load_feature(repo, feature)
    rendered = module.render_issue(loaded, loaded.tasks[0])
    old = remote_issue(
        module,
        rendered,
        title="Old title",
        body=rendered.body.replace("New description", "Old description"),
        state="closed",
        labels=("speckit-task",),
    )

    plan = module.build_plan(loaded, [old])

    action = plan.actions[0]
    assert action.operation == "update"
    assert action.issue_number == 1
    assert action.state == "open"
    assert action.add_labels == ("speckit:example-a1b2c3d4",)
    assert action.reasons == ("title", "body", "labels", "state")


def test_plan_blocks_duplicate_managed_issues(module, tmp_path: Path) -> None:
    repo, feature = make_feature(tmp_path, tasks="- [ ] T001 One task\n")
    loaded = module.load_feature(repo, feature)
    rendered = module.render_issue(loaded, loaded.tasks[0])

    plan = module.build_plan(
        loaded,
        [
            remote_issue(module, rendered, number=11),
            remote_issue(module, rendered, number=12),
        ],
    )

    assert plan.aligned is False
    assert [item.code for item in plan.blocking] == ["duplicate_task_key"]
    assert "#11, #12" in plan.blocking[0].message


def test_plan_blocks_orphaned_published_task(module, tmp_path: Path) -> None:
    repo, feature = make_feature(tmp_path, tasks="- [ ] T001 Current\n")
    loaded = module.load_feature(repo, feature)
    orphan_task = module.SpecTask("T099", "Removed", True, False, None, 99)
    orphan = module.render_issue(loaded, orphan_task)

    plan = module.build_plan(loaded, [remote_issue(module, orphan, number=99)])

    assert [item.code for item in plan.blocking] == ["orphan_task"]
    assert "restore T099" in plan.blocking[0].message


@pytest.mark.parametrize(
    ("title", "body", "labels", "code"),
    [
        ("T001: Legacy", "No marker", (), "legacy_task_issue"),
        (
            "[001-example] T001: Broken",
            "<!-- mesh-speckit-task:v2 feature=bad -->",
            ("speckit:example-a1b2c3d4",),
            "malformed_marker",
        ),
    ],
)
def test_plan_blocks_legacy_or_malformed_feature_issues(
    module,
    tmp_path: Path,
    title: str,
    body: str,
    labels: tuple[str, ...],
    code: str,
) -> None:
    repo, feature = make_feature(tmp_path, tasks="- [ ] T001 Current\n")
    loaded = module.load_feature(repo, feature)
    issue = module.RemoteIssue(7, title, body, "open", labels)

    plan = module.build_plan(loaded, [issue])

    assert [item.code for item in plan.blocking] == [code]


def test_plan_ignores_same_task_id_from_another_feature(module, tmp_path: Path) -> None:
    repo, feature = make_feature(tmp_path, tasks="- [ ] T001 Current\n")
    loaded = module.load_feature(repo, feature)
    body = (
        "<!-- mesh-speckit-task:v1 repo=owner/repo "
        "feature=another-a1b2c3d4 task=T001 -->\n"
    )
    unrelated = module.RemoteIssue(
        5,
        "[another] T001: Other",
        body,
        "open",
        ("speckit-task", "speckit:another-a1b2c3d4"),
    )

    plan = module.build_plan(loaded, [unrelated])

    assert plan.blocking == ()
    assert [(item.task_id, item.operation) for item in plan.actions] == [
        ("T001", "create")
    ]


def test_plan_blocks_marker_repository_mismatch(module, tmp_path: Path) -> None:
    repo, feature = make_feature(tmp_path, tasks="- [ ] T001 Current\n")
    loaded = module.load_feature(repo, feature)
    body = (
        "<!-- mesh-speckit-task:v1 repo=other/repo "
        "feature=example-a1b2c3d4 task=T001 -->\n"
    )
    issue = module.RemoteIssue(
        8,
        "[001-example] T001: Current",
        body,
        "open",
        ("speckit-task", "speckit:example-a1b2c3d4"),
    )

    plan = module.build_plan(loaded, [issue])

    assert [item.code for item in plan.blocking] == ["repository_mismatch"]


def test_plan_dict_is_stable_and_contains_no_remote_body(module, tmp_path: Path) -> None:
    repo, feature = make_feature(tmp_path, tasks="- [ ] T001 Current\n")
    loaded = module.load_feature(repo, feature)
    issue = module.RemoteIssue(2, "T001: Legacy", "SECRET=remote", "open", ())

    payload = module.plan_to_dict(module.build_plan(loaded, [issue]))
    encoded = json.dumps(payload, sort_keys=True)

    assert payload["schema"] == "mesh.speckit.github-plan.v1"
    assert payload["repository"] == "owner/repo"
    assert payload["aligned"] is False
    assert "SECRET=remote" not in encoded
