from __future__ import annotations

import importlib.util
import json
import subprocess
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
        (
            "- [ ] T001 Test <!-- mesh-speckit-task:v1 repo=owner/repo -->\n",
            "reserved mesh-speckit-task marker namespace",
        ),
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


def test_batch_blocks_marker_from_removed_or_rebound_feature(module, tmp_path: Path) -> None:
    repo, feature = make_feature(tmp_path, tasks="- [ ] T001 Current\n")
    loaded = module.load_feature(repo, feature)
    stale = module.RemoteIssue(
        17,
        "[old-feature] T001: Stale",
        "<!-- mesh-speckit-task:v1 repo=owner/repo feature=removed-a1b2c3d4 task=T001 -->\n",
        "open",
        ("speckit-task", "speckit:removed-a1b2c3d4"),
    )

    blocking = module.inspect_batch_markers((loaded,), (stale,))

    assert [item.code for item in blocking] == ["unknown_feature"]
    assert blocking[0].issue_number == 17
    assert "restore its immutable binding" in blocking[0].message


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


@pytest.mark.parametrize(
    ("remote", "expected"),
    [
        ("https://github.com/Owner/Repo.git", "owner/repo"),
        ("git@github.com:Owner/Repo.git", "owner/repo"),
        ("ssh://git@github.com/Owner/Repo.git", "owner/repo"),
    ],
)
def test_parse_github_remote_supports_https_and_ssh(
    module, remote: str, expected: str
) -> None:
    assert module.parse_github_remote(remote) == expected


@pytest.mark.parametrize(
    "remote",
    [
        "https://gitlab.com/owner/repo.git",
        "https://token@github.com/owner/repo.git",
        "https://github.com/owner/repo/extra",
        "github.com/owner/repo",
    ],
)
def test_parse_github_remote_rejects_unsafe_or_non_github_urls(module, remote: str) -> None:
    with pytest.raises(module.LedgerError, match="GitHub remote"):
        module.parse_github_remote(remote)


def test_verify_checkout_binding_checks_origin_and_action_repository(
    module, tmp_path: Path
) -> None:
    repo, feature = make_feature(tmp_path, tasks="- [ ] T001 Current\n")
    loaded = module.load_feature(repo, feature)

    def good_run(args, input_text=None):
        assert args == ("git", "-C", str(repo.resolve()), "config", "--get", "remote.origin.url")
        return module.CommandResult(0, "https://github.com/owner/repo.git\n", "")

    module.verify_checkout_binding(
        loaded, run=good_run, environ={"GITHUB_REPOSITORY": "owner/repo"}
    )

    with pytest.raises(module.LedgerError, match="origin repository"):
        module.verify_checkout_binding(
            loaded,
            run=lambda args, input_text=None: module.CommandResult(
                0, "https://github.com/other/repo.git\n", ""
            ),
            environ={},
        )
    with pytest.raises(module.LedgerError, match="GITHUB_REPOSITORY"):
        module.verify_checkout_binding(
            loaded, run=good_run, environ={"GITHUB_REPOSITORY": "fork/repo"}
        )


def test_gh_client_checks_version_and_parses_paginated_issues(module) -> None:
    calls = []

    def run(args, input_text=None):
        calls.append((args, input_text))
        if args == ("gh", "version"):
            return module.CommandResult(0, "gh version 2.80.0 (2026-01-01)\n", "")
        assert args[:4] == ("gh", "api", "--paginate", "--slurp")
        return module.CommandResult(
            0,
            json.dumps(
                [
                    [
                        {
                            "number": 1,
                            "title": "Issue",
                            "body": "Body",
                            "state": "open",
                            "labels": [{"name": "speckit-task"}],
                        },
                        {
                            "number": 2,
                            "title": "PR",
                            "body": "",
                            "state": "open",
                            "labels": [],
                            "pull_request": {},
                        },
                    ],
                    [
                        {
                            "number": 3,
                            "title": "Closed",
                            "body": None,
                            "state": "closed",
                            "labels": [{"name": "done"}],
                        }
                    ],
                ]
            ),
            "",
        )

    client = module.GhClient("owner/repo", run=run)

    assert client.version == (2, 80, 0)
    assert client.list_issues() == (
        module.RemoteIssue(1, "Issue", "Body", "open", ("speckit-task",)),
        module.RemoteIssue(3, "Closed", "", "closed", ("done",)),
    )
    assert len(calls) == 2


def test_gh_client_rejects_old_version_and_malformed_or_oversized_output(module) -> None:
    old = module.GhClient(
        "owner/repo",
        run=lambda args, input_text=None: module.CommandResult(
            0, "gh version 2.39.9\n", ""
        ),
    )
    with pytest.raises(module.LedgerError, match="2.40 or newer"):
        _ = old.version

    malformed = module.GhClient(
        "owner/repo",
        run=lambda args, input_text=None: module.CommandResult(0, "{}", ""),
    )
    malformed._version = (2, 80, 0)
    with pytest.raises(module.LedgerError, match="paginated array"):
        malformed.list_issues()

    oversized = module.GhClient(
        "owner/repo",
        run=lambda args, input_text=None: module.CommandResult(
            0, "x" * (module.MAX_GH_OUTPUT_BYTES + 1), ""
        ),
    )
    oversized._version = (2, 80, 0)
    with pytest.raises(module.LedgerError, match="output exceeds"):
        oversized.list_issues()


def test_gh_client_never_exposes_command_stderr(module) -> None:
    client = module.GhClient(
        "owner/repo",
        run=lambda args, input_text=None: module.CommandResult(
            1, "", "TOKEN=must-not-leak"
        ),
    )

    with pytest.raises(module.LedgerError) as exc:
        _ = client.version

    assert "must-not-leak" not in str(exc.value)


def test_gh_client_mutations_use_json_stdin_without_shell_interpolation(module) -> None:
    calls = []

    def run(args, input_text=None):
        calls.append((args, input_text))
        if args == ("gh", "version"):
            return module.CommandResult(0, "gh version 2.80.0\n", "")
        if args[3] == "POST" and args[4] == "repos/owner/repo/issues":
            return module.CommandResult(0, '{"number":42}', "")
        return module.CommandResult(0, "{}", "")

    client = module.GhClient("owner/repo", run=run)
    client.create_label(
        "speckit-task", color="123abc", description="Managed task"
    )
    number = client.create_issue(
        title='Title "quoted"', body="Body\nline", labels=("speckit-task",)
    )
    client.update_issue(number, state="closed")
    client.add_labels(number, ("human",))

    assert number == 42
    assert all(call[0][0] == "gh" for call in calls)
    assert all("sh" not in call[0] and "bash" not in call[0] for call in calls)
    payloads = [json.loads(call[1]) for call in calls if call[1] is not None]
    assert payloads == [
        {
            "color": "123abc",
            "description": "Managed task",
            "name": "speckit-task",
        },
        {
            "body": "Body\nline",
            "labels": ["speckit-task"],
            "title": 'Title "quoted"',
        },
        {"state": "closed"},
        {"labels": ["human"]},
    ]


def test_gh_client_rejects_issue_sets_beyond_supported_bound(module) -> None:
    issues = [
        {
            "number": number,
            "title": f"Issue {number}",
            "body": "",
            "state": "open",
            "labels": [],
        }
        for number in range(1, module.MAX_REMOTE_ISSUES + 2)
    ]
    client = module.GhClient(
        "owner/repo",
        run=lambda args, input_text=None: module.CommandResult(
            0, json.dumps([issues]), ""
        ),
    )
    client._version = (2, 80, 0)

    with pytest.raises(module.LedgerError, match="issue count exceeds"):
        client.list_issues()


def action_environment() -> dict[str, str]:
    return {
        "GITHUB_ACTIONS": "true",
        "MESH_SPECKIT_LEDGER_APPLY": "1",
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_REPOSITORY": "owner/repo",
        "GITHUB_REF": "refs/heads/main",
        "MESH_DEFAULT_BRANCH": "main",
    }


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"GITHUB_ACTIONS": "false"}, "GitHub Actions"),
        ({"MESH_SPECKIT_LEDGER_APPLY": "0"}, "apply gate"),
        ({"GITHUB_EVENT_NAME": "pull_request"}, "event"),
        ({"GITHUB_REPOSITORY": "fork/repo"}, "repository"),
        ({"GITHUB_REF": "refs/heads/feature"}, "default branch"),
    ],
)
def test_authoritative_apply_environment_fails_closed(
    module, tmp_path: Path, change: dict[str, str], message: str
) -> None:
    repo, feature = make_feature(tmp_path, tasks="- [ ] T001 Current\n")
    loaded = module.load_feature(repo, feature)
    environ = action_environment()
    environ.update(change)

    with pytest.raises(module.LedgerError, match=message):
        module.validate_apply_environment(loaded.binding, environ)


class MemoryGitHub:
    def __init__(self, module, issues=(), labels=()):
        self.module = module
        self.issues = {issue.number: issue for issue in issues}
        self.labels = set(labels)
        self.calls = []
        self.next_number = max(self.issues, default=0) + 1

    def list_issues(self):
        self.calls.append(("list_issues",))
        return tuple(self.issues.values())

    def list_labels(self):
        self.calls.append(("list_labels",))
        return tuple(sorted(self.labels))

    def create_label(self, name, *, color, description):
        self.calls.append(("create_label", name, color, description))
        self.labels.add(name)

    def create_issue(self, *, title, body, labels):
        self.calls.append(("create_issue", title, tuple(labels)))
        number = self.next_number
        self.next_number += 1
        self.issues[number] = self.module.RemoteIssue(
            number, title, body, "open", tuple(labels)
        )
        return number

    def update_issue(self, number, *, title=None, body=None, state=None):
        self.calls.append(("update_issue", number, title, body, state))
        old = self.issues[number]
        self.issues[number] = self.module.RemoteIssue(
            number,
            title if title is not None else old.title,
            body if body is not None else old.body,
            state if state is not None else old.state,
            old.labels,
        )

    def add_labels(self, number, labels):
        self.calls.append(("add_labels", number, tuple(labels)))
        old = self.issues[number]
        merged = tuple(dict.fromkeys((*old.labels, *labels)))
        self.issues[number] = self.module.RemoteIssue(
            old.number, old.title, old.body, old.state, merged
        )


def test_authoritative_apply_creates_closes_and_replays_idempotently(
    module, tmp_path: Path
) -> None:
    repo, feature = make_feature(tmp_path, tasks="- [x] T001 Complete\n")
    loaded = module.load_feature(repo, feature)
    client = MemoryGitHub(module)

    result = module.apply_authoritative(loaded, client, action_environment())

    assert result.final_plan.aligned is True
    assert result.mutations == 4  # two labels, issue create, state close
    first_calls = list(client.calls)
    replay = module.apply_authoritative(loaded, client, action_environment())
    assert replay.mutations == 0
    assert all(call[0] not in {"create_label", "create_issue", "update_issue"} for call in client.calls[len(first_calls) :])


def test_authoritative_apply_updates_without_removing_human_labels(
    module, tmp_path: Path
) -> None:
    repo, feature = make_feature(tmp_path, tasks="- [ ] T001 New\n")
    loaded = module.load_feature(repo, feature)
    rendered = module.render_issue(loaded, loaded.tasks[0])
    old = remote_issue(
        module,
        rendered,
        title="Old",
        body=rendered.body.replace("New", "Old"),
        state="closed",
        labels=("speckit-task", "human"),
    )
    client = MemoryGitHub(module, (old,), ("speckit-task",))

    result = module.apply_authoritative(loaded, client, action_environment())

    assert result.final_plan.aligned is True
    assert set(client.issues[1].labels) == {
        "speckit-task",
        "speckit:example-a1b2c3d4",
        "human",
    }


def test_authoritative_apply_performs_no_mutation_when_plan_is_blocked(
    module, tmp_path: Path
) -> None:
    repo, feature = make_feature(tmp_path, tasks="- [ ] T001 Current\n")
    loaded = module.load_feature(repo, feature)
    legacy = module.RemoteIssue(1, "T001: Legacy", "", "open", ())
    client = MemoryGitHub(module, (legacy,))

    with pytest.raises(module.LedgerError, match="blocking drift"):
        module.apply_authoritative(loaded, client, action_environment())

    assert client.calls == [("list_issues",)]


def test_binding_init_is_plan_first_atomic_and_idempotent(module, tmp_path: Path) -> None:
    repo, feature = make_feature(tmp_path, tasks="- [ ] T001 Current\n")
    binding_path = feature / "github-ledger.json"
    binding_path.unlink()

    plan = module.build_binding_plan(repo, feature, repository="owner/repo")

    assert plan.operation == "create"
    assert plan.binding.feature_id.startswith("example-")
    assert not binding_path.exists()
    assert module.apply_binding_plan(plan) is True
    assert json.loads(binding_path.read_text(encoding="utf-8")) == {
        "enabled": True,
        "feature_id": plan.binding.feature_id,
        "repository": "owner/repo",
        "schema": "mesh.speckit.github-ledger.v1",
    }
    replay = module.build_binding_plan(repo, feature, repository="owner/repo")
    assert replay.operation == "noop"
    assert module.apply_binding_plan(replay) is False
    assert not list(feature.glob(".github-ledger.*.tmp"))


def test_binding_init_preserves_immutable_existing_identity(module, tmp_path: Path) -> None:
    repo, feature = make_feature(tmp_path, tasks="- [ ] T001 Current\n")

    with pytest.raises(module.LedgerError, match="feature_id is immutable"):
        module.build_binding_plan(
            repo,
            feature,
            repository="owner/repo",
            feature_id="different-a1b2c3d4",
        )


def test_caller_plan_is_pinned_plan_first_atomic_and_idempotent(
    module, tmp_path: Path
) -> None:
    repo, _ = make_feature(tmp_path, tasks="- [ ] T001 Current\n")
    runtime_ref = "a" * 40

    plan = module.build_caller_plan(
        repo,
        repository="owner/repo",
        runtime_repository="gptcompany/gobabygo",
        runtime_ref=runtime_ref,
    )

    assert plan.operation == "create"
    assert not plan.workflow_path.exists()
    assert f"@{runtime_ref}" in plan.content
    assert f"runtime_ref: {runtime_ref}" in plan.content
    assert "issues: read" in plan.content
    assert "issues: write" in plan.content
    assert module.apply_caller_plan(plan) is True
    replay = module.build_caller_plan(
        repo,
        repository="owner/repo",
        runtime_repository="gptcompany/gobabygo",
        runtime_ref=runtime_ref,
    )
    assert replay.operation == "noop"
    assert module.apply_caller_plan(replay) is False
    assert not list(plan.workflow_path.parent.glob(".speckit-ledger.*.tmp"))


@pytest.mark.parametrize("runtime_ref", ["main", "A" * 40, "a" * 39, "a" * 41])
def test_caller_plan_rejects_mutable_or_malformed_runtime_ref(
    module, tmp_path: Path, runtime_ref: str
) -> None:
    repo, _ = make_feature(tmp_path, tasks="- [ ] T001 Current\n")

    with pytest.raises(module.LedgerError, match="full lowercase 40-character"):
        module.build_caller_plan(
            repo,
            repository="owner/repo",
            runtime_repository="gptcompany/gobabygo",
            runtime_ref=runtime_ref,
        )


def test_caller_plan_refuses_to_overwrite_existing_workflow(module, tmp_path: Path) -> None:
    repo, _ = make_feature(tmp_path, tasks="- [ ] T001 Current\n")
    workflow = repo / module.CALLER_WORKFLOW
    workflow.parent.mkdir(parents=True)
    workflow.write_text("name: custom\n", encoding="utf-8")

    with pytest.raises(module.LedgerError, match="review it manually"):
        module.build_caller_plan(
            repo,
            repository="owner/repo",
            runtime_repository="gptcompany/gobabygo",
            runtime_ref="a" * 40,
        )


def test_cli_plan_and_check_share_read_only_remote_plan(
    module, tmp_path: Path, capsys
) -> None:
    repo, feature = make_feature(tmp_path, tasks="- [ ] T001 Current\n")

    def run(args, input_text=None):
        if args[-2:] == ("rev-parse", "--show-toplevel"):
            return module.CommandResult(0, f"{repo.resolve()}\n", "")
        if args[-3:] == ("config", "--get", "remote.origin.url"):
            return module.CommandResult(
                0, "https://github.com/owner/repo.git\n", ""
            )
        if args == ("gh", "version"):
            return module.CommandResult(0, "gh version 2.80.0\n", "")
        if args[:4] == ("gh", "api", "--paginate", "--slurp"):
            return module.CommandResult(0, "[[]]", "")
        raise AssertionError(args)

    assert module.main(["plan", str(feature), "--json"], run=run, environ={}) == 0
    plan_payload = json.loads(capsys.readouterr().out)
    assert plan_payload["actions"][0]["operation"] == "create"
    assert module.main(["check", str(feature), "--json"], run=run, environ={}) == 1
    check_payload = json.loads(capsys.readouterr().out)
    assert check_payload == plan_payload


def test_cli_blocking_drift_uses_exit_two(module, tmp_path: Path, capsys) -> None:
    repo, feature = make_feature(tmp_path, tasks="- [ ] T001 Current\n")
    legacy = [
        [
            {
                "number": 1,
                "title": "T001: Legacy",
                "body": "",
                "state": "open",
                "labels": [],
            }
        ]
    ]

    def run(args, input_text=None):
        if args[-2:] == ("rev-parse", "--show-toplevel"):
            return module.CommandResult(0, f"{repo.resolve()}\n", "")
        if args[-3:] == ("config", "--get", "remote.origin.url"):
            return module.CommandResult(0, "git@github.com:owner/repo.git\n", "")
        if args == ("gh", "version"):
            return module.CommandResult(0, "gh version 2.80.0\n", "")
        return module.CommandResult(0, json.dumps(legacy), "")

    assert module.main(["plan", str(feature)], run=run, environ={}) == 2
    output = capsys.readouterr().out
    assert "BLOCKING legacy_task_issue" in output


def test_discover_bound_features_rejects_duplicate_feature_ids(
    module, tmp_path: Path
) -> None:
    repo, first = make_feature(tmp_path, tasks="- [ ] T001 First\n")
    second = repo / "specs" / "002-second"
    second.mkdir()
    (second / "tasks.md").write_text("- [ ] T001 Second\n", encoding="utf-8")
    (second / "github-ledger.json").write_text(
        (first / "github-ledger.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    with pytest.raises(module.LedgerError, match="duplicate feature_id"):
        module.discover_bound_features(repo)


def test_cli_apply_fails_before_github_api_outside_actions(
    module, tmp_path: Path, capsys
) -> None:
    repo, feature = make_feature(tmp_path, tasks="- [ ] T001 Current\n")
    calls = []

    def run(args, input_text=None):
        calls.append(args)
        if args[-2:] == ("rev-parse", "--show-toplevel"):
            return module.CommandResult(0, f"{repo.resolve()}\n", "")
        if args[-3:] == ("config", "--get", "remote.origin.url"):
            return module.CommandResult(0, "https://github.com/owner/repo.git\n", "")
        raise AssertionError(f"unexpected remote command: {args}")

    assert module.main(["apply", str(feature)], run=run, environ={}) == 2
    assert "restricted to GitHub Actions" in capsys.readouterr().err
    assert all(args[0] == "git" for args in calls)
