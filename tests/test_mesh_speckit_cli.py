from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile

import pytest


def _load_module():
    script = Path(__file__).resolve().parents[1] / "scripts" / "mesh_speckit_cli.py"
    spec = importlib.util.spec_from_file_location("mesh_speckit_cli", script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _lock(path: Path, *, integrations=None) -> Path:
    payload = {
        "schema": 1,
        "version": "0.16.5",
        "tag": "v0.16.5",
        "source": "https://github.com/github/spec-kit",
        "integrations": integrations or ["claude"],
        "worker_providers": ["codex", "agy"],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _project(path: Path, integrations=("claude",)) -> Path:
    (path / ".specify").mkdir(parents=True)
    (path / ".specify" / "integration.json").write_text(
        json.dumps(
            {
                "version": "0.16.5",
                "default_integration": "claude",
                "installed_integrations": list(integrations),
            }
        ),
        encoding="utf-8",
    )
    for root in (path / ".claude" / "skills", path / ".agents" / "skills"):
        for name in ("specify", "plan", "converge"):
            skill = root / f"speckit-{name}"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
    return path


def _git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"], check=True)
    marker = path / "README.md"
    marker.write_text("# test\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "init"], check=True)
    return path.resolve()


def test_committed_lock_is_exact_and_active_providers_only() -> None:
    module = _load_module()
    lock = module.load_lock()

    assert lock["version"] == "1.0.3"
    assert lock["integrations"] == ["claude"]
    assert lock["worker_providers"] == ["codex", "agy"]


def test_manual_actions_reports_only_open_decisions_and_blocked_tasks(tmp_path) -> None:
    module = _load_module()
    feature = tmp_path / "specs" / "096-gate"
    feature.mkdir(parents=True)
    (feature / "tasks.md").write_text(
        """# Tasks

- [ ] **DEC-9** [D] Provenienza eventi money path
- [x] **DEC-8** [D] Decisione gia presa
- [ ] **T041** Implementare il verifier
  *Bloccato da*: DEC-9, DEC-8
- [ ] **T042** Testare il verifier
  **Blocked by**: DEC-9
- [ ] **T042b** Pubblicare il verifier. TDD_MODE: required. *Bloccato da*: DEC-9.
- [x] **T043** Task chiuso
  *Bloccato da*: DEC-9
""",
        encoding="utf-8",
    )

    result = module.build_manual_actions(feature)

    assert result["schema"] == "mesh.speckit.manual-actions.v1"
    assert result["count"] == 1
    assert result["actions"] == [
        {
            "id": "DEC-9",
            "title": "Provenienza eventi money path",
            "line": 3,
            "feature_dir": str(feature),
            "blocked_tasks": ["T041", "T042", "T042b"],
        }
    ]


def test_manual_actions_all_is_deterministic_and_bounded(tmp_path) -> None:
    module = _load_module()
    for feature_name, decision_id in (("002-zeta", "DEC-2"), ("001-alpha", "DEC-1")):
        feature = tmp_path / "specs" / feature_name
        feature.mkdir(parents=True)
        (feature / "tasks.md").write_text(
            f"- [ ] **{decision_id}** [D] Choose " + "x" * 300 + "\n",
            encoding="utf-8",
        )

    result = module.build_manual_actions(tmp_path, scan_all=True)

    assert [item["id"] for item in result["actions"]] == ["DEC-1", "DEC-2"]
    assert all(len(item["title"]) == 240 for item in result["actions"])


def test_manual_actions_rejects_symlinked_tasks_file(tmp_path) -> None:
    module = _load_module()
    outside = tmp_path / "outside.md"
    outside.write_text("- [ ] **DEC-1** [D] Unsafe\n", encoding="utf-8")
    feature = tmp_path / "specs" / "001-feature"
    feature.mkdir(parents=True)
    (feature / "tasks.md").symlink_to(outside)

    with pytest.raises(module.SpeckitRuntimeError, match="open tasks file safely"):
        module.build_manual_actions(feature)


def test_manual_actions_cli_emits_clear_state(tmp_path, capsys) -> None:
    module = _load_module()
    feature = tmp_path / "specs" / "001-feature"
    feature.mkdir(parents=True)
    (feature / "tasks.md").write_text("- [x] **DEC-1** [D] Done\n", encoding="utf-8")

    rc = module.main(["manual-actions", str(feature)])

    assert rc == 0
    assert capsys.readouterr().out == "MANUAL_CLEAR count=0\n"


def test_lock_rejects_gemini(tmp_path) -> None:
    module = _load_module()
    path = _lock(tmp_path / "lock.json", integrations=["claude", "codex", "gemini"])

    with pytest.raises(module.SpeckitRuntimeError, match="exactly claude"):
        module.load_lock(path)


def test_installed_version_reports_missing_binary(monkeypatch) -> None:
    module = _load_module()
    monkeypatch.setattr(module.shutil, "which", lambda _name: None)
    monkeypatch.setattr(module.Path, "is_file", lambda _path: False)

    assert module.installed_version() == {
        "available": False,
        "executable": None,
        "version": None,
        "error": None,
    }


def test_installed_version_falls_back_to_user_local_bin(monkeypatch, tmp_path) -> None:
    module = _load_module()
    executable = tmp_path / ".local" / "bin" / "specify"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setattr(module.shutil, "which", lambda _name: None)
    original_expanduser = module.Path.expanduser

    def fake_expanduser(path):
        if str(path) == "~/.local/bin/specify":
            return executable
        return original_expanduser(path)

    monkeypatch.setattr(module.Path, "expanduser", fake_expanduser)

    def runner(args, **_kwargs):
        return subprocess.CompletedProcess(args, 0, stdout="specify 0.16.5\n", stderr="")

    result = module.installed_version(runner)

    assert result["available"] is True
    assert result["executable"] == str(executable)
    assert result["version"] == "0.16.5"


def test_installed_version_parses_official_output(monkeypatch) -> None:
    module = _load_module()
    monkeypatch.setattr(module.shutil, "which", lambda _name: "/opt/bin/specify")

    def runner(args, **_kwargs):
        return subprocess.CompletedProcess(args, 0, stdout="Specify CLI 0.16.5\n", stderr="")

    assert module.installed_version(runner)["version"] == "0.16.5"


def test_project_capabilities_require_manifest_and_all_integrations(tmp_path) -> None:
    module = _load_module()
    repo = _project(tmp_path / "repo", integrations=("claude", "codex"))

    result = module.inspect_project(repo, module.ALLOWED_INTEGRATIONS)

    assert result["state"] == "partial"
    assert result["missing_integrations"] == ["agy"]
    assert result["enabled_capabilities"] == []


def test_project_without_manifest_distinguishes_legacy_from_missing(tmp_path) -> None:
    module = _load_module()
    legacy = tmp_path / "legacy"
    (legacy / ".specify" / "memory").mkdir(parents=True)
    (legacy / ".specify" / "memory" / "constitution.md").write_text(
        "# Legacy constitution\n", encoding="utf-8"
    )
    feature = legacy / "specs" / "001-old"
    feature.mkdir(parents=True)
    (feature / "spec.md").write_text("# Spec\n", encoding="utf-8")
    (feature / "tasks.md").write_text("# Tasks\n", encoding="utf-8")
    missing = tmp_path / "missing"
    missing.mkdir()

    legacy_result = module.inspect_project(legacy, module.ALLOWED_INTEGRATIONS)
    missing_result = module.inspect_project(missing, module.ALLOWED_INTEGRATIONS)

    assert legacy_result["state"] == "legacy"
    assert legacy_result["legacy_evidence"] == [
        ".specify/",
        "specs/001-old/{spec.md,tasks.md}",
    ]
    assert missing_result["state"] == "missing"
    assert missing_result["legacy_evidence"] == []


def test_legacy_detection_is_bounded_and_does_not_read_artifact_contents(tmp_path) -> None:
    module = _load_module()
    repo = tmp_path / "repo"
    commands = repo / ".claude" / "commands"
    commands.mkdir(parents=True)
    (commands / "speckit.plan.md").write_text("SECRET=must-not-leak\n", encoding="utf-8")

    result = module.inspect_project(repo, module.ALLOWED_INTEGRATIONS)

    assert result["state"] == "legacy"
    assert result["legacy_evidence"] == [".claude/commands/speckit*"]
    assert "SECRET" not in json.dumps(result)


def test_legacy_detection_recognizes_pre_dot_specify_layout(tmp_path) -> None:
    module = _load_module()
    repo = tmp_path / "repo"
    constitution = repo / "memory" / "constitution.md"
    command = repo / ".claude" / "commands" / "plan.md"
    constitution.parent.mkdir(parents=True)
    command.parent.mkdir(parents=True)
    constitution.write_text("# Historical principles\n", encoding="utf-8")
    command.write_text("# Historical plan command\n", encoding="utf-8")

    result = module.inspect_project(repo, module.ALLOWED_INTEGRATIONS)

    assert result["state"] == "legacy"
    assert result["legacy_evidence"] == [
        "memory/constitution.md",
        ".claude/commands/{plan.md}",
    ]
    assert result["legacy_commands"] == [".claude/commands/plan.md"]


def test_single_generic_claude_command_does_not_mark_project_legacy(tmp_path) -> None:
    module = _load_module()
    repo = tmp_path / "repo"
    command = repo / ".claude" / "commands" / "plan.md"
    command.parent.mkdir(parents=True)
    command.write_text("# Custom project command\n", encoding="utf-8")

    result = module.inspect_project(repo, module.ALLOWED_INTEGRATIONS)

    assert result["state"] == "missing"
    assert result["legacy_evidence"] == []
    assert result["legacy_commands"] == [".claude/commands/plan.md"]


def test_inspect_project_contains_unreadable_legacy_commands(
    monkeypatch, tmp_path, capsys
) -> None:
    module = _load_module()
    repo = tmp_path / "repo"
    commands = repo / ".claude" / "commands"
    commands.mkdir(parents=True)
    original_iterdir = module.Path.iterdir

    def guarded_iterdir(path):
        if path == commands:
            raise PermissionError("commands denied")
        return original_iterdir(path)

    monkeypatch.setattr(module.Path, "iterdir", guarded_iterdir)
    result = module.inspect_project(repo, module.ALLOWED_INTEGRATIONS)

    assert result["state"] == "invalid"
    assert result["error"] == "cannot inspect project commands: commands denied"

    lock = _lock(tmp_path / "lock.json")
    monkeypatch.setattr(
        module,
        "installed_version",
        lambda: {"available": False, "executable": None, "version": None, "error": None},
    )
    rc = module.main(
        ["--lock-file", str(lock), "status", str(repo), "--json"]
    )
    captured = capsys.readouterr()

    assert rc == 1
    assert json.loads(captured.out)["project"]["state"] == "invalid"
    assert "Traceback" not in captured.err


def test_project_capabilities_are_intersection_of_installed_skills(tmp_path) -> None:
    module = _load_module()
    repo = _project(tmp_path / "repo", integrations=("claude", "codex", "agy"))
    extra = repo / ".claude" / "skills" / "speckit-claude-only"
    extra.mkdir()
    (extra / "SKILL.md").write_text("# extra\n", encoding="utf-8")

    result = module.inspect_project(repo, module.ALLOWED_INTEGRATIONS)

    assert result["state"] == "aligned"
    assert result["enabled_capabilities"] == ["converge", "plan", "specify"]
    assert result["capabilities"]["codex"] == result["capabilities"]["agy"]


def test_status_rejects_outdated_project_manifest(monkeypatch, tmp_path) -> None:
    module = _load_module()
    repo = _project(tmp_path / "repo")
    manifest = repo / ".specify" / "integration.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["version"] = "0.16.4"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    lock = _lock(tmp_path / "lock.json")
    monkeypatch.setattr(
        module,
        "installed_version",
        lambda: {"available": True, "executable": "/bin/specify", "version": "0.16.5", "error": None},
    )

    result = module.build_status(repo, lock_file=lock, state_file=tmp_path / "none")

    assert result["runtime_aligned"] is True
    assert result["project"]["state"] == "partial"
    assert result["project"]["manifest_version"] == "0.16.4"
    assert result["project"]["version_aligned"] is False
    assert result["aligned"] is False


def test_project_rejects_active_unsupported_integration(tmp_path) -> None:
    module = _load_module()
    repo = _project(tmp_path / "repo", integrations=("claude", "codex", "agy", "gemini"))

    result = module.inspect_project(repo, module.ALLOWED_INTEGRATIONS)

    assert result["state"] == "unsupported"
    assert result["unsupported_integrations"] == ["gemini"]


def test_malformed_manifest_is_invalid(tmp_path) -> None:
    module = _load_module()
    repo = tmp_path / "repo"
    (repo / ".specify").mkdir(parents=True)
    (repo / ".specify" / "integration.json").write_text("{", encoding="utf-8")

    result = module.inspect_project(repo, module.ALLOWED_INTEGRATIONS)

    assert result["state"] == "invalid"
    assert "cannot read" in result["error"]


def test_status_uses_cached_release_without_network(monkeypatch, tmp_path) -> None:
    module = _load_module()
    repo = _project(tmp_path / "repo")
    lock = _lock(tmp_path / "lock.json")
    state = tmp_path / "state.json"
    state.write_text(
        json.dumps({"version": "0.16.6", "tag": "v0.16.6"}), encoding="utf-8"
    )
    monkeypatch.setattr(
        module,
        "installed_version",
        lambda: {"available": True, "executable": "/bin/specify", "version": "0.16.5", "error": None},
    )
    monkeypatch.setattr(module, "_fetch_latest_release", lambda: pytest.fail("network used"))

    result = module.build_status(repo, lock_file=lock, state_file=state)

    assert result["aligned"] is True
    assert result["latest_known_version"] == "0.16.6"
    assert result["update_available"] is True


def test_status_does_not_treat_older_cache_as_update(monkeypatch, tmp_path) -> None:
    module = _load_module()
    repo = _project(tmp_path / "repo")
    lock = _lock(tmp_path / "lock.json")
    state = tmp_path / "state.json"
    state.write_text(
        json.dumps({"version": "0.15.9", "tag": "v0.15.9"}), encoding="utf-8"
    )
    monkeypatch.setattr(
        module,
        "installed_version",
        lambda: {"available": True, "executable": "/bin/specify", "version": "0.16.5", "error": None},
    )

    result = module.build_status(repo, lock_file=lock, state_file=state)

    assert result["latest_known_version"] == "0.15.9"
    assert result["update_available"] is False


def _trusted_runtime_repo(tmp_path: Path) -> Path:
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    repo = _git_repo(tmp_path / "runtime")
    subprocess.run(["git", "-C", str(repo), "branch", "-M", "master"], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", str(bare)], check=True)
    subprocess.run(["git", "-C", str(repo), "push", "-qu", "origin", "HEAD"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "remote",
            "set-url",
            "origin",
            "https://github.com/gptcompany/gobabygo.git",
        ],
        check=True,
    )
    return repo


def test_orchestration_runtime_exposes_only_clean_origin_commit(tmp_path) -> None:
    module = _load_module()
    repo = _trusted_runtime_repo(tmp_path)
    commit = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    assert module.inspect_orchestration_runtime(repo) == {
        "repository": "gptcompany/gobabygo",
        "trusted": True,
        "commit": commit,
        "reason": None,
    }

    (repo / "untracked.txt").write_text("dirty\n", encoding="utf-8")
    dirty = module.inspect_orchestration_runtime(repo)
    assert dirty["trusted"] is False
    assert dirty["commit"] is None
    assert dirty["reason"] == "dirty_checkout"


def test_orchestration_runtime_rejects_wrong_origin_and_unpublished_head(tmp_path) -> None:
    module = _load_module()
    repo = _trusted_runtime_repo(tmp_path)
    subprocess.run(
        ["git", "-C", str(repo), "remote", "set-url", "origin", "https://github.com/other/repo.git"],
        check=True,
    )
    assert module.inspect_orchestration_runtime(repo)["reason"] == "unexpected_origin"

    subprocess.run(
        ["git", "-C", str(repo), "remote", "set-url", "origin", "https://github.com/gptcompany/gobabygo.git"],
        check=True,
    )
    (repo / "README.md").write_text("# changed\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "local"], check=True)
    unpublished = module.inspect_orchestration_runtime(repo)
    assert unpublished["trusted"] is False
    assert unpublished["commit"] is None
    assert unpublished["reason"] == "commit_not_on_origin"


@pytest.mark.parametrize(
    "origin",
    [
        "https://github.com/gptcompany/gobabygo",
        "https://github.com/gptcompany/gobabygo.git/",
        "git@github.com:gptcompany/gobabygo",
        "ssh://git@github.com/gptcompany/gobabygo",
    ],
)
def test_orchestration_runtime_accepts_canonical_origin_variants(
    tmp_path, origin
) -> None:
    module = _load_module()
    repo = _trusted_runtime_repo(tmp_path)
    subprocess.run(
        ["git", "-C", str(repo), "remote", "set-url", "origin", origin],
        check=True,
    )

    assert module.inspect_orchestration_runtime(repo)["trusted"] is True


def test_delegation_context_is_provider_neutral_and_repository_bounded(
    monkeypatch, tmp_path
) -> None:
    module = _load_module()
    repo = _git_repo(tmp_path / "repo")
    _project(repo)
    feature = repo / "specs" / "001-runtime"
    feature.mkdir(parents=True)
    lock = _lock(tmp_path / "lock.json")
    monkeypatch.setattr(
        module,
        "installed_version",
        lambda: {"available": True, "executable": "/bin/specify", "version": "0.16.5", "error": None},
    )

    writer = module.build_delegation_context(
        repo,
        phase="plan",
        feature_dir=Path("specs/001-runtime"),
        artifacts=[Path("tasks.md"), Path("spec.md")],
        role="writer",
        lock_file=lock,
    )
    reviewer = module.build_delegation_context(
        repo,
        phase="plan",
        feature_dir=Path("specs/001-runtime"),
        artifacts=[Path("spec.md"), Path("tasks.md")],
        role="reviewer",
        review_scope="commit:" + ("a" * 40) + ".." + ("b" * 40),
        lock_file=lock,
    )

    assert writer == {
        "schema": "mesh.speckit.context.v1",
        "version": "0.16.5",
        "phase": "plan",
        "feature_dir": "specs/001-runtime",
        "allowed_artifacts": ["specs/001-runtime/spec.md", "specs/001-runtime/tasks.md"],
        "role": "writer",
        "review_scope": "not-applicable",
        "review_policy": "different-provider-required",
    }
    assert reviewer["version"] == writer["version"]
    assert reviewer["phase"] == writer["phase"]
    assert reviewer["feature_dir"] == writer["feature_dir"]
    assert reviewer["allowed_artifacts"] == writer["allowed_artifacts"]
    assert reviewer["review_policy"] == "read-only-independent-provider"
    assert "provider" not in writer


def test_delegation_context_rejects_unsupported_phase_paths_and_mutable_review_scope(
    monkeypatch, tmp_path
) -> None:
    module = _load_module()
    repo = _git_repo(tmp_path / "repo")
    _project(repo)
    feature = repo / "specs" / "001-runtime"
    feature.mkdir(parents=True)
    lock = _lock(tmp_path / "lock.json")
    monkeypatch.setattr(
        module,
        "installed_version",
        lambda: {"available": True, "executable": "/bin/specify", "version": "0.16.5", "error": None},
    )
    base = {
        "repo": repo,
        "feature_dir": Path("specs/001-runtime"),
        "artifacts": [Path("spec.md")],
        "role": "writer",
        "lock_file": lock,
    }

    with pytest.raises(module.SpeckitRuntimeError, match="phase is not enabled"):
        module.build_delegation_context(phase="implement", **base)
    with pytest.raises(module.SpeckitRuntimeError, match="inside the feature directory"):
        module.build_delegation_context(
            phase="plan", **{**base, "artifacts": [Path("../../README.md")]}
        )
    with pytest.raises(module.SpeckitRuntimeError, match="reviewer requires"):
        module.build_delegation_context(
            phase="plan",
            **{**base, "role": "reviewer", "review_scope": "working-tree"},
        )


def test_immutable_review_scope_normalizer_is_reusable_and_canonical() -> None:
    module = _load_module()

    assert module.normalize_immutable_review_scope(
        "  COMMIT:" + ("A" * 40) + ".." + ("B" * 40) + "  "
    ) == "commit:" + ("a" * 40) + ".." + ("b" * 40)
    assert module.normalize_immutable_review_scope(
        "DIFF-SHA256:" + ("C" * 64)
    ) == "diff-sha256:" + ("c" * 64)

    with pytest.raises(module.SpeckitRuntimeError, match="reviewer requires"):
        module.normalize_immutable_review_scope("HEAD")


def test_delegation_context_accepts_immutable_decision_artifact(
    monkeypatch, tmp_path
) -> None:
    module = _load_module()
    repo = _git_repo(tmp_path / "repo")
    _project(repo)
    feature = repo / "specs" / "001-runtime"
    feature.mkdir(parents=True)
    decision = feature / "decision.md"
    decision.write_text("# Decision\n", encoding="utf-8")
    lock = _lock(tmp_path / "lock.json")
    monkeypatch.setattr(
        module,
        "installed_version",
        lambda: {
            "available": True,
            "executable": "/bin/specify",
            "version": "0.16.5",
            "error": None,
        },
    )

    context = module.build_delegation_context(
        repo,
        phase="plan",
        feature_dir=Path("specs/001-runtime"),
        artifacts=[Path("decision.md")],
        role="reviewer",
        review_scope=(
            f"artifact-sha256:{hashlib.sha256(decision.read_bytes()).hexdigest()}"
        ),
        lock_file=lock,
    )

    assert context["review_scope"] == (
        f"artifact-sha256:{hashlib.sha256(decision.read_bytes()).hexdigest()}"
    )

    with pytest.raises(module.SpeckitRuntimeError, match="does not match"):
        module.build_delegation_context(
            repo,
            phase="plan",
            feature_dir=Path("specs/001-runtime"),
            artifacts=[Path("decision.md")],
            role="reviewer",
            review_scope=f"artifact-sha256:{'0' * 64}",
            lock_file=lock,
        )


def test_bounded_sha256_rejects_symlink(tmp_path: Path) -> None:
    module = _load_module()
    target = tmp_path / "target.md"
    target.write_text("decision", encoding="utf-8")
    link = tmp_path / "decision.md"
    link.symlink_to(target)

    with pytest.raises(module.SpeckitRuntimeError, match="open .* safely"):
        module._bounded_sha256(link, max_bytes=1024, label="decision artifact")


def test_update_check_persists_allowlisted_metadata_only(monkeypatch, tmp_path) -> None:
    module = _load_module()
    state = tmp_path / "state" / "latest.json"
    monkeypatch.setattr(
        module,
        "_fetch_latest_release",
        lambda: {
            "version": "0.16.6",
            "tag": "v0.16.6",
            "published_at": "2026-08-20T00:00:00Z",
            "html_url": "https://github.com/github/spec-kit/releases/tag/v0.16.6",
        },
    )

    module.update_check(state)
    payload = json.loads(state.read_text(encoding="utf-8"))

    assert set(payload) == {"version", "tag", "published_at", "html_url", "checked_at"}
    assert "body" not in payload
    assert state.stat().st_mode & 0o777 == 0o600


def test_update_check_refuses_symlink_state(monkeypatch, tmp_path) -> None:
    module = _load_module()
    target = tmp_path / "target.json"
    target.write_text("{}", encoding="utf-8")
    state = tmp_path / "state.json"
    state.symlink_to(target)
    monkeypatch.setattr(
        module,
        "_fetch_latest_release",
        lambda: {
            "version": "0.16.6",
            "tag": "v0.16.6",
            "published_at": "",
            "html_url": "https://github.com/github/spec-kit/releases/tag/v0.16.6",
        },
    )

    with pytest.raises(module.SpeckitRuntimeError, match="refusing symlink"):
        module.update_check(state)


def test_cli_status_json_reports_unaligned_without_traceback(monkeypatch, tmp_path, capsys) -> None:
    module = _load_module()
    lock = _lock(tmp_path / "lock.json")
    repo = tmp_path / "missing-project"
    monkeypatch.setattr(
        module,
        "installed_version",
        lambda: {"available": False, "executable": None, "version": None, "error": None},
    )

    rc = module.main(
        ["--lock-file", str(lock), "--state-file", str(tmp_path / "none"), "status", str(repo), "--json"]
    )
    output = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert output["installed"]["available"] is False
    assert output["project"]["state"] == "invalid"


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        (subprocess.TimeoutExpired(["git"], 10), "git timed out after 10 seconds"),
        (OSError("unavailable"), "cannot start git: unavailable"),
    ],
)
def test_cli_normalizes_subprocess_failures(
    monkeypatch, tmp_path, capsys, failure, message
) -> None:
    module = _load_module()
    repo = tmp_path / "repo"
    repo.mkdir()
    lock = _lock(tmp_path / "lock.json")
    monkeypatch.setattr(module.subprocess, "run", lambda *_args, **_kwargs: (_ for _ in ()).throw(failure))

    rc = module.main(
        [
            "--lock-file",
            str(lock),
            "project",
            "init",
            str(repo),
            "--allow-multi-install-force",
        ]
    )
    captured = capsys.readouterr()

    assert rc == 2
    assert captured.out == ""
    assert captured.err.strip() == f"Error: {message}"
    assert "Traceback" not in captured.err


def test_cli_normalizes_residual_filesystem_failures(monkeypatch, tmp_path, capsys) -> None:
    module = _load_module()
    lock = _lock(tmp_path / "lock.json")
    monkeypatch.setattr(
        module,
        "build_project_plan",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("read failed")),
    )

    rc = module.main(
        [
            "--lock-file",
            str(lock),
            "project",
            "init",
            str(tmp_path),
            "--allow-multi-install-force",
        ]
    )
    captured = capsys.readouterr()

    assert rc == 2
    assert captured.out == ""
    assert captured.err.strip() == "Error: operating system failure: read failed"
    assert "Traceback" not in captured.err


def test_install_plan_requires_exact_locked_version(monkeypatch, tmp_path) -> None:
    module = _load_module()
    lock = module.load_lock(_lock(tmp_path / "lock.json"))
    uv = tmp_path / "uv"
    uv.write_text("", encoding="utf-8")
    monkeypatch.setattr(module.shutil, "which", lambda name: str(uv) if name == "uv" else None)

    with pytest.raises(module.SpeckitRuntimeError, match="does not match lock"):
        module.build_install_plan("0.16.6", lock)
    with pytest.raises(module.SpeckitRuntimeError, match="exact semantic version"):
        module.build_install_plan("latest", lock)

    plan = module.build_install_plan("v0.16.5", lock)
    assert plan["commands"][0][-1].endswith("@v0.16.5")


def test_install_apply_stops_on_first_failure(monkeypatch) -> None:
    module = _load_module()
    calls: list[list[str]] = []
    plan = {"version": "0.16.5", "commands": [["uv", "install"], ["specify", "check"]]}

    def fake_run(args, **_kwargs):
        calls.append(list(args))
        return subprocess.CompletedProcess(args, 7, stdout="", stderr="failed")

    monkeypatch.setattr(module, "_run_command", fake_run)

    with pytest.raises(module.SpeckitRuntimeError, match=r"failed \(7\)"):
        module.apply_install_plan(plan)
    assert calls == [["uv", "install"]]


def test_project_init_plan_requires_clean_exact_git_root(
    monkeypatch, tmp_path
) -> None:
    module = _load_module()
    repo = _git_repo(tmp_path / "repo")
    lock = module.load_lock(_lock(tmp_path / "lock.json"))
    monkeypatch.setattr(
        module,
        "installed_version",
        lambda: {"available": True, "executable": "/bin/specify", "version": "0.16.5", "error": None},
    )
    monkeypatch.setattr(
        module,
        "_migration_inventory",
        lambda _repo, _commands: {
            "generated_files": 1,
            "additions": [".specify/integration.json"],
            "generated_updates": [],
            "protected_preserved": [],
            "blocking_collisions": [],
            "legacy_constitution_migrations": {},
            "legacy_constitution_unmigrated": [],
            "legacy_commands_preserved": [],
            "generated_content_sha256": {
                ".specify/integration.json": "example"
            },
            "ignored_generated_paths": [],
        },
    )

    plan = module.build_project_plan("init", repo, lock)
    assert Path(plan["commands"][0][0]).name == "specify"
    assert plan["commands"][0][1:4] == ["init", "--here", "--force"]
    assert len(plan["commands"]) == 1
    assert plan["integrations"] == ["claude"]
    assert plan["worker_providers"] == ["codex", "agy"]
    assert plan["ready_to_apply"] is True
    assert plan["migration"]["additions"] == [".specify/integration.json"]
    subdir = repo / "src"
    subdir.mkdir()
    with pytest.raises(module.SpeckitRuntimeError, match="exact Git repository root"):
        module.build_project_plan(
            "init", subdir, lock, allow_multi_install_force=True
        )


def test_project_upgrade_inventories_extensions_without_updating_them(
    monkeypatch, tmp_path
) -> None:
    module = _load_module()
    repo = _git_repo(tmp_path / "repo")
    _project(repo)
    extensions = repo / ".specify" / "extensions"
    extensions.mkdir()
    (repo / ".specify" / "extensions.yml").write_text(
        "installed:\n  - git\n",
        encoding="utf-8",
    )
    (extensions / ".registry").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "extensions": {
                    "git": {
                        "version": "1.2.3",
                        "source": "catalog",
                        "enabled": True,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "speckit"], check=True)
    lock = module.load_lock(_lock(tmp_path / "lock.json"))

    plan = module.build_project_plan("upgrade", repo, lock)

    assert plan["extensions"] == [
        {
            "id": "git",
            "version": "1.2.3",
            "source": "catalog",
            "enabled": True,
            "configured": True,
        }
    ]
    assert plan["extension_updates"] == []
    assert plan["extension_update_policy"] == "separate-explicit-review"
    assert all(command[1:3] != ["extension", "update"] for command in plan["commands"])


def test_project_upgrade_rejects_malformed_extension_registry(
    tmp_path,
) -> None:
    module = _load_module()
    repo = _git_repo(tmp_path / "repo")
    _project(repo)
    extensions = repo / ".specify" / "extensions"
    extensions.mkdir()
    (repo / ".specify" / "extensions.yml").write_text(
        "installed:\n  - git\n",
        encoding="utf-8",
    )
    (extensions / ".registry").write_text("{", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "speckit"], check=True)
    lock = module.load_lock(_lock(tmp_path / "lock.json"))

    with pytest.raises(module.SpeckitRuntimeError, match="extension registry"):
        module.build_project_plan("upgrade", repo, lock)


def test_project_init_redirects_legacy_repo_to_migrate(tmp_path) -> None:
    module = _load_module()
    repo = _git_repo(tmp_path / "repo")
    (repo / ".specify").mkdir()
    (repo / ".specify" / "legacy.txt").write_text("legacy\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", ".specify"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "legacy"], check=True)
    lock = module.load_lock(_lock(tmp_path / "lock.json"))

    with pytest.raises(module.SpeckitRuntimeError, match="requires project migrate"):
        module.build_project_plan(
            "init", repo, lock, allow_multi_install_force=True
        )


def test_migration_plan_reports_updates_preservation_and_additions(
    monkeypatch, tmp_path
) -> None:
    module = _load_module()
    repo = _git_repo(tmp_path / "repo")
    template = repo / ".specify" / "templates" / "spec-template.md"
    constitution = repo / ".specify" / "memory" / "constitution.md"
    template.parent.mkdir(parents=True)
    constitution.parent.mkdir(parents=True)
    template.write_text("legacy template\n", encoding="utf-8")
    constitution.write_text("legacy constitution\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", ".specify"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "legacy"], check=True)
    lock = module.load_lock(_lock(tmp_path / "lock.json"))
    monkeypatch.setattr(module.shutil, "which", lambda name: "/bin/specify" if name == "specify" else None)
    monkeypatch.setattr(
        module,
        "installed_version",
        lambda: {"available": True, "executable": "/bin/specify", "version": "0.16.5", "error": None},
    )
    real_run = module._run_command

    def fake_run(args, **kwargs):
        if args[0] == "git":
            return real_run(args, **kwargs)
        staging = Path(kwargs["cwd"])
        generated_template = staging / ".specify" / "templates" / "spec-template.md"
        generated_constitution = staging / ".specify" / "memory" / "constitution.md"
        generated_skill = staging / ".claude" / "skills" / "speckit-plan" / "SKILL.md"
        for path in (generated_template, generated_constitution, generated_skill):
            path.parent.mkdir(parents=True, exist_ok=True)
        generated_template.write_text("current template\n", encoding="utf-8")
        generated_constitution.write_text("current constitution\n", encoding="utf-8")
        generated_skill.write_text("current skill\n", encoding="utf-8")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(module, "_run_command", fake_run)
    plan = module.build_project_plan(
        "migrate", repo, lock, allow_multi_install_force=True
    )
    accepted = module.build_project_plan(
        "migrate",
        repo,
        lock,
        allow_multi_install_force=True,
        accept_generated_updates=True,
    )

    assert plan["migration"] == {
        "generated_files": 3,
        "additions": [".claude/skills/speckit-plan/SKILL.md"],
        "generated_updates": [".specify/templates/spec-template.md"],
        "protected_preserved": [".specify/memory/constitution.md"],
        "blocking_collisions": [],
        "ignored_generated_paths": [],
        "legacy_constitution_migrations": {},
        "legacy_constitution_unmigrated": [],
        "legacy_commands_preserved": [],
        "generated_content_sha256": {
            ".claude/skills/speckit-plan/SKILL.md": hashlib.sha256(
                b"current skill\n"
            ).hexdigest(),
            ".specify/templates/spec-template.md": hashlib.sha256(
                b"current template\n"
            ).hexdigest(),
        },
    }
    assert plan["ready_to_apply"] is False
    assert accepted["ready_to_apply"] is True
    assert accepted["base_head"] == subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_migration_plan_fails_closed_on_agent_skill_collision(monkeypatch, tmp_path) -> None:
    module = _load_module()
    repo = _git_repo(tmp_path / "repo")
    legacy = repo / ".specify" / "legacy.txt"
    legacy.parent.mkdir()
    legacy.write_text("legacy\n", encoding="utf-8")
    skill = repo / ".agents" / "skills" / "speckit-plan" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("custom skill\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", ".specify", ".agents"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "legacy"], check=True)
    lock = module.load_lock(_lock(tmp_path / "lock.json"))
    monkeypatch.setattr(module.shutil, "which", lambda _name: "/bin/specify")
    monkeypatch.setattr(
        module,
        "installed_version",
        lambda: {"available": True, "executable": "/bin/specify", "version": "0.16.5", "error": None},
    )
    real_run = module._run_command

    def fake_run(args, **kwargs):
        if args[0] == "git":
            return real_run(args, **kwargs)
        generated = Path(kwargs["cwd"]) / ".agents" / "skills" / "speckit-plan" / "SKILL.md"
        generated.parent.mkdir(parents=True, exist_ok=True)
        generated.write_text("official skill\n", encoding="utf-8")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(module, "_run_command", fake_run)
    plan = module.build_project_plan(
        "migrate",
        repo,
        lock,
        allow_multi_install_force=True,
        accept_generated_updates=True,
    )

    assert plan["migration"]["blocking_collisions"] == [
        ".agents/skills/speckit-plan/SKILL.md"
    ]
    assert plan["ready_to_apply"] is False


@pytest.mark.parametrize("collision_kind", ["target-symlink", "parent-symlink"])
def test_migration_inventory_reports_symlink_paths_as_collisions(
    tmp_path, collision_kind
) -> None:
    module = _load_module()
    repo = tmp_path / "repo"
    staging = tmp_path / "staging"
    repo.mkdir()
    generated = staging / ".agents" / "skills" / "speckit-plan" / "SKILL.md"
    generated.parent.mkdir(parents=True)
    generated.write_text("official\n", encoding="utf-8")
    if collision_kind == "target-symlink":
        target = repo / ".agents" / "skills" / "speckit-plan" / "SKILL.md"
        target.parent.mkdir(parents=True)
        target.symlink_to(repo / "missing-target")
    else:
        parent = repo / ".agents" / "skills"
        parent.parent.mkdir(parents=True)
        parent.symlink_to(repo / "missing-directory")

    inventory = module._migration_inventory_from_tree(repo, staging)

    assert inventory["additions"] == []
    assert inventory["blocking_collisions"] == [
        ".agents/skills/speckit-plan/SKILL.md"
    ]


@pytest.mark.parametrize("target_kind", ["source-parent", "target", "target-parent"])
def test_historical_constitution_rejects_source_and_target_symlinks(
    tmp_path, target_kind
) -> None:
    module = _load_module()
    repo = tmp_path / "repo"
    staging = tmp_path / "staging"
    repo.mkdir()
    generated = staging / ".specify" / "memory" / "constitution.md"
    generated.parent.mkdir(parents=True)
    generated.write_text("default\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "constitution.md").write_text("external secret\n", encoding="utf-8")

    if target_kind == "source-parent":
        (repo / "memory").symlink_to(outside, target_is_directory=True)
    else:
        source = repo / "memory" / "constitution.md"
        source.parent.mkdir()
        source.write_text("historical\n", encoding="utf-8")
        if target_kind == "target":
            target = repo / ".specify" / "memory" / "constitution.md"
            target.parent.mkdir(parents=True)
            target.symlink_to(repo / "missing")
        else:
            parent = repo / ".specify" / "memory"
            parent.parent.mkdir()
            parent.symlink_to(outside, target_is_directory=True)

    inventory = module._migration_inventory_from_tree(repo, staging)

    assert inventory["legacy_constitution_migrations"] == {}
    assert inventory["blocking_collisions"] == [
        ".specify/memory/constitution.md"
    ]


def test_migration_inventory_reports_generated_paths_ignored_by_git(tmp_path) -> None:
    module = _load_module()
    repo = _git_repo(tmp_path / "repo")
    (repo / ".gitignore").write_text(".agents/\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", ".gitignore"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "ignore agents"], check=True)
    staging = tmp_path / "staging"
    generated = staging / ".agents" / "skills" / "speckit-plan" / "SKILL.md"
    generated.parent.mkdir(parents=True)
    generated.write_text("official\n", encoding="utf-8")

    inventory = module._migration_inventory_with_git(repo, staging)

    assert inventory["ignored_generated_paths"] == [
        ".agents/skills/speckit-plan/SKILL.md"
    ]


def test_generated_metadata_digest_ignores_only_allowlisted_timestamps() -> None:
    module = _load_module()
    relative = ".specify/integrations/claude.manifest.json"
    first = b'{"integration":"claude","version":"0.16.5","installed_at":"2026-08-20T10:24:14.1+00:00"}'
    later = b'{"installed_at":"2026-08-20T10:25:00Z","version":"0.16.5","integration":"claude"}'
    changed = b'{"integration":"claude","version":"0.16.6","installed_at":"2026-08-20T10:25:00Z"}'
    missing = b'{"integration":"claude","version":"0.16.5"}'

    assert module._normalized_generated_digest(
        first, relative
    ) == module._normalized_generated_digest(later, relative)
    assert module._normalized_generated_digest(
        first, relative
    ) != module._normalized_generated_digest(changed, relative)
    assert module._normalized_generated_digest(
        first, relative
    ) != module._normalized_generated_digest(missing, relative)
    with pytest.raises(module.SpeckitRuntimeError, match="invalid generated metadata"):
        module._normalized_generated_digest(
            b'{"integration":"claude","installed_at":{"unexpected":true}}',
            relative,
        )
    with pytest.raises(module.SpeckitRuntimeError, match="invalid generated metadata"):
        module._normalized_generated_digest(
            b'{"integration":"claude","installed_at":"9999-99-99T99:99:99Z"}',
            relative,
        )
    with pytest.raises(module.SpeckitRuntimeError, match="invalid generated metadata"):
        module._normalized_generated_digest(
            b'{"integration":"claude","version":"0.16.5","version":"0.16.6"}',
            relative,
        )


def test_migration_plan_requires_pinned_runtime(monkeypatch, tmp_path) -> None:
    module = _load_module()
    repo = _git_repo(tmp_path / "repo")
    legacy = repo / ".specify" / "legacy.txt"
    legacy.parent.mkdir()
    legacy.write_text("legacy\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", ".specify"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "legacy"], check=True)
    lock = module.load_lock(_lock(tmp_path / "lock.json"))
    monkeypatch.setattr(
        module,
        "installed_version",
        lambda: {"available": True, "executable": "/bin/specify", "version": "0.16.4", "error": None},
    )

    with pytest.raises(module.SpeckitRuntimeError, match="requires pinned Spec Kit 0.16.5"):
        module.build_project_plan(
            "migrate", repo, lock, allow_multi_install_force=True
        )


def test_migration_apply_installs_all_providers_and_preserves_constitution(
    monkeypatch, tmp_path
) -> None:
    module = _load_module()
    repo = _git_repo(tmp_path / "repo")
    template = repo / ".specify" / "templates" / "spec-template.md"
    constitution = repo / ".specify" / "memory" / "constitution.md"
    template.parent.mkdir(parents=True)
    constitution.parent.mkdir(parents=True)
    template.write_text("legacy template\n", encoding="utf-8")
    constitution.write_text("legacy constitution\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", ".specify"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "legacy"], check=True)

    def write_bundle(staging, _commands):
        manifest = staging / ".specify" / "integration.json"
        generated_template = staging / ".specify" / "templates" / "spec-template.md"
        generated_constitution = staging / ".specify" / "memory" / "constitution.md"
        claude = staging / ".claude" / "skills" / "speckit-plan" / "SKILL.md"
        agents = staging / ".agents" / "skills" / "speckit-plan" / "SKILL.md"
        for path in (manifest, generated_template, generated_constitution, claude, agents):
            path.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(
            json.dumps(
                {
                    "version": "0.16.5",
                    "default_integration": "claude",
                    "installed_integrations": ["claude", "codex", "agy"],
                }
            ),
            encoding="utf-8",
        )
        generated_template.write_text("current template\n", encoding="utf-8")
        generated_constitution.write_text("default constitution\n", encoding="utf-8")
        claude.write_text("# Plan\n", encoding="utf-8")
        agents.write_text("# Plan\n", encoding="utf-8")

    with tempfile.TemporaryDirectory() as temporary:
        staged = Path(temporary)
        write_bundle(staged, [])
        inventory = module._migration_inventory_with_git(repo, staged)
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    plan = {
        "schema": "mesh.speckit.project-plan.v1",
        "action": "migrate",
        "repo": str(repo),
        "required_version": "0.16.5",
        "commands": [["specify", "init"]],
        "base_head": head,
        "migration": inventory,
        "accept_generated_updates": True,
        "ready_to_apply": True,
    }
    monkeypatch.setattr(module, "_generate_migration_tree", write_bundle)
    monkeypatch.setattr(
        module,
        "installed_version",
        lambda: {"available": True, "executable": "/bin/specify", "version": "0.16.5", "error": None},
    )

    result = module.apply_migration_plan(plan)

    assert result["applied"] is True
    assert result["preserved_paths"] == [".specify/memory/constitution.md"]
    assert constitution.read_text(encoding="utf-8") == "legacy constitution\n"
    assert template.read_text(encoding="utf-8") == "current template\n"
    assert (repo / ".claude" / "skills" / "speckit-plan" / "SKILL.md").is_file()
    assert (repo / ".agents" / "skills" / "speckit-plan" / "SKILL.md").is_file()


def test_migration_apply_moves_historical_constitution_to_current_path(
    monkeypatch, tmp_path
) -> None:
    module = _load_module()
    repo = _git_repo(tmp_path / "repo")
    historical = repo / "memory" / "constitution.md"
    legacy_command = repo / ".claude" / "commands" / "plan.md"
    historical.parent.mkdir(parents=True)
    legacy_command.parent.mkdir(parents=True)
    historical.write_text("# Historical principles\n", encoding="utf-8")
    legacy_command.write_text("# Historical plan command\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repo), "add", "memory", ".claude"], check=True
    )
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "legacy"], check=True)

    def write_bundle(staging, _commands):
        manifest = staging / ".specify" / "integration.json"
        constitution = staging / ".specify" / "memory" / "constitution.md"
        claude = staging / ".claude" / "skills" / "speckit-plan" / "SKILL.md"
        agents = staging / ".agents" / "skills" / "speckit-plan" / "SKILL.md"
        for path in (manifest, constitution, claude, agents):
            path.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(
            json.dumps(
                {
                    "version": "0.16.5",
                    "default_integration": "claude",
                    "installed_integrations": ["claude", "codex", "agy"],
                }
            ),
            encoding="utf-8",
        )
        constitution.write_text("# Default constitution\n", encoding="utf-8")
        claude.write_text("# Plan\n", encoding="utf-8")
        agents.write_text("# Plan\n", encoding="utf-8")

    with tempfile.TemporaryDirectory() as temporary:
        staged = Path(temporary)
        write_bundle(staged, [])
        inventory = module._migration_inventory_with_git(repo, staged)
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    plan = {
        "action": "migrate",
        "repo": str(repo),
        "required_version": "0.16.5",
        "commands": [["specify", "init"]],
        "base_head": head,
        "migration": inventory,
        "accept_generated_updates": False,
        "ready_to_apply": True,
    }
    monkeypatch.setattr(module, "_generate_migration_tree", write_bundle)
    monkeypatch.setattr(
        module,
        "installed_version",
        lambda: {"available": True, "executable": "/bin/specify", "version": "0.16.5", "error": None},
    )

    result = module.apply_migration_plan(plan)

    current = repo / ".specify" / "memory" / "constitution.md"
    assert current.read_bytes() == historical.read_bytes()
    assert result["migration"]["legacy_constitution_migrations"] == {
        ".specify/memory/constitution.md": "memory/constitution.md"
    }
    assert result["migration"]["generated_content_sha256"][
        ".specify/memory/constitution.md"
    ] == hashlib.sha256(historical.read_bytes()).hexdigest()
    assert result["migration"]["legacy_commands_preserved"] == [
        ".claude/commands/plan.md"
    ]
    assert legacy_command.read_text(encoding="utf-8") == "# Historical plan command\n"


def test_migration_inventory_reports_unmigrated_historical_constitution(
    tmp_path,
) -> None:
    module = _load_module()
    repo = tmp_path / "repo"
    staging = tmp_path / "staging"
    historical = repo / "memory" / "constitution.md"
    current = repo / ".specify" / "memory" / "constitution.md"
    generated = staging / ".specify" / "memory" / "constitution.md"
    for path in (historical, current, generated):
        path.parent.mkdir(parents=True, exist_ok=True)
    historical.write_text("historical\n", encoding="utf-8")
    current.write_text("current\n", encoding="utf-8")
    generated.write_text("generated\n", encoding="utf-8")

    inventory = module._migration_inventory_from_tree(repo, staging)

    assert inventory["legacy_constitution_migrations"] == {}
    assert inventory["legacy_constitution_unmigrated"] == [
        "memory/constitution.md"
    ]
    assert inventory["protected_preserved"] == [
        ".specify/memory/constitution.md"
    ]


@pytest.mark.parametrize(
    "failure",
    [OSError("simulated copy failure"), KeyboardInterrupt()],
    ids=["os-error", "keyboard-interrupt"],
)
def test_migration_apply_rolls_back_only_its_own_partial_writes(
    monkeypatch, tmp_path, failure
) -> None:
    module = _load_module()
    repo = _git_repo(tmp_path / "repo")
    template = repo / ".specify" / "templates" / "spec-template.md"
    template.parent.mkdir(parents=True)
    template.write_text("legacy template\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", ".specify"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "legacy"], check=True)

    def write_bundle(staging, _commands):
        generated = staging / ".specify" / "templates" / "spec-template.md"
        generated.parent.mkdir(parents=True, exist_ok=True)
        generated.write_text("current template\n", encoding="utf-8")

    with tempfile.TemporaryDirectory() as temporary:
        staged = Path(temporary)
        write_bundle(staged, [])
        inventory = module._migration_inventory_with_git(repo, staged)
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    plan = {
        "action": "migrate",
        "repo": str(repo),
        "required_version": "0.16.5",
        "commands": [["specify", "init"]],
        "base_head": head,
        "migration": inventory,
        "accept_generated_updates": True,
        "ready_to_apply": True,
    }
    monkeypatch.setattr(module, "_generate_migration_tree", write_bundle)
    monkeypatch.setattr(
        module,
        "installed_version",
        lambda: {"available": True, "executable": "/bin/specify", "version": "0.16.5", "error": None},
    )

    def partial_write(_source, target, _expected_digest, _relative):
        target.write_text("partial\n", encoding="utf-8")
        raise failure

    monkeypatch.setattr(module, "_atomic_copy_migration_file", partial_write)
    handlers = {}
    previous = {
        module.signal.SIGINT: object(),
        module.signal.SIGTERM: object(),
    }
    monkeypatch.setattr(module.signal, "getsignal", lambda value: previous[value])
    monkeypatch.setattr(
        module.signal,
        "signal",
        lambda value, handler: handlers.__setitem__(value, handler),
    )
    real_restore = module._restore_migration_file

    def guarded_restore(target, backup):
        assert handlers[module.signal.SIGINT] is not previous[module.signal.SIGINT]
        assert handlers[module.signal.SIGTERM] is not previous[module.signal.SIGTERM]
        real_restore(target, backup)

    monkeypatch.setattr(module, "_restore_migration_file", guarded_restore)

    with pytest.raises(module.SpeckitRuntimeError, match="was rolled back"):
        module.apply_migration_plan(plan)
    assert template.read_text(encoding="utf-8") == "legacy template\n"
    assert subprocess.run(
        ["git", "-C", str(repo), "status", "--short"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout == ""


def test_migration_signal_guard_defers_sigterm_until_critical_section_exits(
    monkeypatch
) -> None:
    module = _load_module()
    handlers = {}
    previous = {
        module.signal.SIGINT: object(),
        module.signal.SIGTERM: object(),
    }
    monkeypatch.setattr(module.signal, "getsignal", lambda value: previous[value])
    monkeypatch.setattr(
        module.signal,
        "signal",
        lambda value, handler: handlers.__setitem__(value, handler),
    )
    inside_completed = False

    with module._defer_migration_signals() as pending:
        handlers[module.signal.SIGTERM](module.signal.SIGTERM, None)
        inside_completed = True

    assert inside_completed is True
    assert pending == [module.signal.SIGTERM]
    assert handlers == previous


def test_migration_signal_is_redelivered_after_handlers_are_restored(
    monkeypatch
) -> None:
    module = _load_module()
    handlers = {}
    previous = {
        module.signal.SIGINT: object(),
        module.signal.SIGTERM: object(),
    }
    delivered = []
    monkeypatch.setattr(module.signal, "getsignal", lambda value: previous[value])
    monkeypatch.setattr(
        module.signal,
        "signal",
        lambda value, handler: handlers.__setitem__(value, handler),
    )

    def record_kill(pid, signal_number):
        assert pid == module.os.getpid()
        assert handlers == previous
        delivered.append(signal_number)

    monkeypatch.setattr(module.os, "kill", record_kill)

    with module._defer_migration_signals() as pending:
        handlers[module.signal.SIGTERM](module.signal.SIGTERM, None)
    with pytest.raises(SystemExit, match=str(128 + module.signal.SIGTERM)):
        module._redeliver_migration_signal(pending[0])

    assert delivered == [module.signal.SIGTERM]


def test_atomic_migration_copy_and_restore_preserve_mode_and_cleanup(tmp_path) -> None:
    module = _load_module()
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.write_text("new\n", encoding="utf-8")
    source.chmod(0o750)

    module._atomic_copy_migration_file(source, target)

    assert target.read_text(encoding="utf-8") == "new\n"
    assert target.stat().st_mode & 0o777 == 0o750
    assert list(tmp_path.glob(".target.*")) == []

    module._restore_migration_file(target, (b"old\n", 0o640))
    assert target.read_text(encoding="utf-8") == "old\n"
    assert target.stat().st_mode & 0o777 == 0o640
    assert list(tmp_path.glob(".target.restore.*")) == []

    module._restore_migration_file(target, None)
    assert not target.exists()

    with pytest.raises(module.SpeckitRuntimeError, match="source changed"):
        module._atomic_copy_migration_file(source, target, "0" * 64)
    assert not target.exists()
    assert list(tmp_path.glob(".target.*")) == []


def test_atomic_migration_copy_makes_generated_shell_scripts_executable(tmp_path) -> None:
    module = _load_module()
    source = tmp_path / "source.sh"
    target = tmp_path / "target.sh"
    source.write_text("#!/bin/sh\n", encoding="utf-8")
    source.chmod(0o644)

    module._atomic_copy_migration_file(
        source,
        target,
        relative=".specify/scripts/bash/check-prerequisites.sh",
    )

    assert target.stat().st_mode & 0o777 == 0o755


def test_safe_migration_target_cleans_partial_parent_creation(
    monkeypatch, tmp_path
) -> None:
    module = _load_module()
    root = tmp_path / "repo"
    root.mkdir()
    original_mkdir = module.Path.mkdir

    def fail_second_parent(path, *args, **kwargs):
        if path == root / "first" / "second":
            raise OSError("no space")
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(module.Path, "mkdir", fail_second_parent)

    with pytest.raises(OSError, match="no space"):
        module._safe_migration_target(root, "first/second/file.txt")

    assert not (root / "first").exists()


def test_generated_file_digest_enforces_streaming_size_limit(
    monkeypatch, tmp_path
) -> None:
    module = _load_module()
    generated = tmp_path / "generated.txt"
    generated.write_bytes(b"12345")
    monkeypatch.setattr(module, "_MAX_MIGRATION_FILE_BYTES", 4)

    with pytest.raises(module.SpeckitRuntimeError, match="exceeds size limit"):
        module._generated_file_digest(generated, "generated.txt")


def test_migration_apply_rolls_back_failed_alignment(monkeypatch, tmp_path) -> None:
    module = _load_module()
    repo = _git_repo(tmp_path / "repo")
    legacy = repo / ".specify" / "legacy.txt"
    legacy.parent.mkdir()
    legacy.write_text("legacy\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", ".specify"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "legacy"], check=True)

    def write_incomplete_bundle(staging, _commands):
        manifest = staging / ".specify" / "integration.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(
            json.dumps(
                {
                    "version": "0.16.5",
                    "default_integration": "claude",
                    "installed_integrations": ["claude"],
                }
            ),
            encoding="utf-8",
        )

    with tempfile.TemporaryDirectory() as temporary:
        staged = Path(temporary)
        write_incomplete_bundle(staged, [])
        inventory = module._migration_inventory_with_git(repo, staged)
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    plan = {
        "action": "migrate",
        "repo": str(repo),
        "required_version": "0.16.5",
        "commands": [["specify", "init"]],
        "base_head": head,
        "migration": inventory,
        "accept_generated_updates": False,
        "ready_to_apply": True,
    }
    monkeypatch.setattr(module, "_generate_migration_tree", write_incomplete_bundle)
    monkeypatch.setattr(
        module,
        "installed_version",
        lambda: {"available": True, "executable": "/bin/specify", "version": "0.16.5", "error": None},
    )

    with pytest.raises(module.SpeckitRuntimeError, match="not aligned"):
        module.apply_migration_plan(plan)
    assert not (repo / ".specify" / "integration.json").exists()
    assert subprocess.run(
        ["git", "-C", str(repo), "status", "--short"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout == ""


def test_migration_apply_refuses_unaccepted_generated_updates(tmp_path) -> None:
    module = _load_module()
    repo = _git_repo(tmp_path / "repo")
    plan = {
        "action": "migrate",
        "repo": str(repo),
        "base_head": subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip(),
        "migration": {"blocking_collisions": [], "generated_updates": ["template"]},
        "ready_to_apply": False,
    }

    with pytest.raises(module.SpeckitRuntimeError, match="accept-generated-updates"):
        module.apply_migration_plan(plan)


def test_migration_apply_refuses_ignored_paths_and_parallel_apply(tmp_path) -> None:
    module = _load_module()
    repo = _git_repo(tmp_path / "repo")
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    plan = {
        "action": "migrate",
        "repo": str(repo),
        "base_head": head,
        "migration": {
            "blocking_collisions": [],
            "generated_updates": [],
            "ignored_generated_paths": [".agents/skills/speckit-plan/SKILL.md"],
        },
        "ready_to_apply": False,
    }

    with pytest.raises(module.SpeckitRuntimeError, match="ignored by Git"):
        module.apply_migration_plan(plan)

    with module._migration_lock(repo):
        with pytest.raises(module.SpeckitRuntimeError, match="another Spec Kit migration"):
            module.apply_migration_plan(plan)


def test_migration_apply_refuses_repo_change_during_sandbox_generation(
    monkeypatch, tmp_path
) -> None:
    module = _load_module()
    repo = _git_repo(tmp_path / "repo")
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    plan = {
        "action": "migrate",
        "repo": str(repo),
        "required_version": "0.16.5",
        "commands": [["specify", "init"]],
        "base_head": head,
        "migration": {
            "generated_files": 0,
            "additions": [],
            "generated_updates": [],
            "protected_preserved": [],
            "blocking_collisions": [],
        },
        "accept_generated_updates": False,
        "ready_to_apply": True,
    }

    def generate_while_repo_changes(_staging, _commands):
        (repo / "worker-change.txt").write_text("concurrent\n", encoding="utf-8")

    monkeypatch.setattr(module, "_generate_migration_tree", generate_while_repo_changes)
    monkeypatch.setattr(
        module,
        "installed_version",
        lambda: {"available": True, "executable": "/bin/specify", "version": "0.16.5", "error": None},
    )

    with pytest.raises(module.SpeckitRuntimeError, match="changed while preparing"):
        module.apply_migration_plan(plan)
    assert not (repo / ".specify" / "integration.json").exists()


def test_migration_apply_refuses_changed_generated_content(monkeypatch, tmp_path) -> None:
    module = _load_module()
    repo = _git_repo(tmp_path / "repo")

    def write_bundle(staging, _commands, *, content="planned\n"):
        generated = staging / ".agents" / "skills" / "speckit-plan" / "SKILL.md"
        generated.parent.mkdir(parents=True, exist_ok=True)
        generated.write_text(content, encoding="utf-8")

    with tempfile.TemporaryDirectory() as temporary:
        staged = Path(temporary)
        write_bundle(staged, [])
        inventory = module._migration_inventory_with_git(repo, staged)
    plan = {
        "action": "init",
        "repo": str(repo),
        "required_version": "0.16.5",
        "commands": [["specify", "init"]],
        "base_head": module._git_head(repo),
        "migration": inventory,
        "accept_generated_updates": False,
        "ready_to_apply": True,
    }
    monkeypatch.setattr(
        module,
        "_generate_migration_tree",
        lambda staging, commands: write_bundle(staging, commands, content="changed\n"),
    )
    monkeypatch.setattr(
        module,
        "installed_version",
        lambda: {"available": True, "executable": "/bin/specify", "version": "0.16.5", "error": None},
    )

    with pytest.raises(module.SpeckitRuntimeError, match="inventory changed"):
        module.apply_migration_plan(plan)

    assert not (repo / ".agents").exists()


def test_project_plan_refuses_dirty_repo_before_commands(tmp_path) -> None:
    module = _load_module()
    repo = _git_repo(tmp_path / "repo")
    lock = module.load_lock(_lock(tmp_path / "lock.json"))
    (repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")

    with pytest.raises(module.SpeckitRuntimeError, match="worktree must be clean"):
        module.build_project_plan(
            "init", repo, lock, allow_multi_install_force=True
        )


def test_project_upgrade_reconciles_workers_and_rejects_unknown_integrations(tmp_path) -> None:
    module = _load_module()
    lock = module.load_lock(_lock(tmp_path / "lock.json"))
    repo = _git_repo(tmp_path / "repo")
    _project(repo, integrations=("claude", "codex", "agy"))
    subprocess.run(["git", "-C", str(repo), "add", ".specify", ".claude", ".agents"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "speckit"], check=True)

    plan = module.build_project_plan("upgrade", repo, lock)
    assert [command[1:] for command in plan["commands"]] == [
        ["integration", "uninstall", "agy"],
        ["integration", "uninstall", "codex"],
        ["integration", "upgrade", "claude"],
    ]
    assert all("--force" not in command for command in plan["commands"])

    manifest = repo / ".specify" / "integration.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["installed_integrations"] = ["claude", "codex", "agy", "gemini"]
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", str(manifest)], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "unsupported"], check=True)

    with pytest.raises(module.SpeckitRuntimeError, match="unsupported active integrations: gemini"):
        module.build_project_plan("upgrade", repo, lock)


def test_project_apply_reports_partial_changed_paths(monkeypatch, tmp_path) -> None:
    module = _load_module()
    repo = _git_repo(tmp_path / "repo")
    plan = {
        "action": "upgrade",
        "repo": str(repo),
        "required_version": "0.16.5",
        "base_head": module._git_head(repo),
        "commands": [["specify", "first"], ["specify", "second"]],
    }
    calls = 0
    monkeypatch.setattr(module.shutil, "which", lambda _name: "/bin/specify")
    monkeypatch.setattr(
        module,
        "installed_version",
        lambda: {"available": True, "executable": "/bin/specify", "version": "0.16.5", "error": None},
    )

    def fake_run(args, **kwargs):
        nonlocal calls
        if args[0] == "git":
            return subprocess.run(args, check=False, capture_output=True, text=True, **kwargs)
        calls += 1
        if calls == 1:
            (repo / "generated.txt").write_text("partial\n", encoding="utf-8")
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(args, 3, stdout="", stderr="failed")

    monkeypatch.setattr(module, "_run_command", fake_run)

    with pytest.raises(module.SpeckitRuntimeError, match=r"partial changed paths: \?\? generated.txt"):
        module.apply_project_plan(plan)


def test_project_apply_refuses_runtime_drift_before_commands(monkeypatch, tmp_path) -> None:
    module = _load_module()
    repo = _git_repo(tmp_path / "repo")
    plan = {
        "action": "upgrade",
        "repo": str(repo),
        "required_version": "0.16.5",
        "base_head": module._git_head(repo),
        "commands": [["specify", "integration", "upgrade", "claude"]],
    }
    monkeypatch.setattr(
        module,
        "installed_version",
        lambda: {"available": True, "executable": "/bin/specify", "version": "0.16.6", "error": None},
    )
    real_run = module._run_command

    def refuse_project_command(args, **kwargs):
        if args[0] == "git":
            return real_run(args, **kwargs)
        pytest.fail("project command ran with runtime drift")

    monkeypatch.setattr(module, "_run_command", refuse_project_command)

    with pytest.raises(module.SpeckitRuntimeError, match="requires pinned Spec Kit 0.16.5"):
        module.apply_project_plan(plan)


def test_project_upgrade_apply_rejects_drift_and_parallel_apply(
    monkeypatch, tmp_path
) -> None:
    module = _load_module()
    repo = _git_repo(tmp_path / "repo")
    plan = {
        "action": "upgrade",
        "repo": str(repo),
        "required_version": "0.16.5",
        "base_head": module._git_head(repo),
        "commands": [["specify", "integration", "upgrade", "claude"]],
    }
    monkeypatch.setattr(
        module,
        "installed_version",
        lambda: {"available": True, "executable": "/bin/specify", "version": "0.16.5", "error": None},
    )
    real_run = module._run_command

    def refuse_upgrade(args, **kwargs):
        if args[0] == "git":
            return real_run(args, **kwargs)
        pytest.fail("upgrade command ran after drift")

    monkeypatch.setattr(module, "_run_command", refuse_upgrade)
    (repo / "drift.txt").write_text("dirty\n", encoding="utf-8")

    with pytest.raises(module.SpeckitRuntimeError, match="worktree changed"):
        module.apply_project_plan(plan)

    (repo / "drift.txt").unlink()
    with module._migration_lock(repo):
        with pytest.raises(module.SpeckitRuntimeError, match="another Spec Kit migration"):
            module.apply_project_plan(plan)


def test_cli_install_without_apply_only_prints_plan(monkeypatch, tmp_path, capsys) -> None:
    module = _load_module()
    lock = _lock(tmp_path / "lock.json")
    uv = tmp_path / "uv"
    uv.write_text("", encoding="utf-8")
    monkeypatch.setattr(module.shutil, "which", lambda name: str(uv) if name == "uv" else None)
    monkeypatch.setattr(
        module,
        "apply_install_plan",
        lambda _plan: pytest.fail("install executor called without --apply"),
    )

    rc = module.main(["--lock-file", str(lock), "install", "0.16.5", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["applied"] is False


def test_cli_project_without_apply_only_prints_plan(monkeypatch, tmp_path, capsys) -> None:
    module = _load_module()
    repo = _git_repo(tmp_path / "repo")
    lock = _lock(tmp_path / "lock.json")
    monkeypatch.setattr(
        module,
        "apply_project_plan",
        lambda _plan: pytest.fail("project executor called without --apply"),
    )
    monkeypatch.setattr(
        module,
        "installed_version",
        lambda: {"available": True, "executable": "/bin/specify", "version": "0.16.5", "error": None},
    )
    monkeypatch.setattr(
        module,
        "_migration_inventory",
        lambda _repo, _commands: {
            "generated_files": 1,
            "additions": [".specify/integration.json"],
            "generated_updates": [],
            "protected_preserved": [],
            "blocking_collisions": [],
            "legacy_constitution_migrations": {},
            "legacy_constitution_unmigrated": [],
            "legacy_commands_preserved": [],
            "ignored_generated_paths": [],
        },
    )

    rc = module.main(
        [
            "--lock-file",
            str(lock),
            "project",
            "init",
            str(repo),
            "--allow-multi-install-force",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["applied"] is False
