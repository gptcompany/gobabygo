from __future__ import annotations

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
        "integrations": integrations or ["claude", "codex", "agy"],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _project(path: Path, integrations=("claude", "codex", "agy")) -> Path:
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

    assert lock["version"] == "0.16.5"
    assert lock["integrations"] == ["claude", "codex", "agy"]


def test_lock_rejects_gemini(tmp_path) -> None:
    module = _load_module()
    path = _lock(tmp_path / "lock.json", integrations=["claude", "codex", "gemini"])

    with pytest.raises(module.SpeckitRuntimeError, match="exactly claude, codex, agy"):
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


def test_project_capabilities_are_intersection_of_installed_skills(tmp_path) -> None:
    module = _load_module()
    repo = _project(tmp_path / "repo")
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


def test_project_init_plan_requires_clean_exact_git_root_and_force_consent(tmp_path) -> None:
    module = _load_module()
    repo = _git_repo(tmp_path / "repo")
    lock = module.load_lock(_lock(tmp_path / "lock.json"))

    with pytest.raises(module.SpeckitRuntimeError, match="allow-multi-install-force"):
        module.build_project_plan("init", repo, lock)

    plan = module.build_project_plan(
        "init", repo, lock, allow_multi_install_force=True
    )
    assert Path(plan["commands"][0][0]).name == "specify"
    assert plan["commands"][0][1:4] == ["init", "--here", "--force"]
    assert Path(plan["commands"][2][0]).name == "specify"
    assert plan["commands"][2][1:] == [
        "integration",
        "install",
        "agy",
        "--force",
    ]
    subdir = repo / "src"
    subdir.mkdir()
    with pytest.raises(module.SpeckitRuntimeError, match="exact Git repository root"):
        module.build_project_plan(
            "init", subdir, lock, allow_multi_install_force=True
        )


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

    def partial_write(_source, target):
        target.write_text("partial\n", encoding="utf-8")
        raise failure

    monkeypatch.setattr(module, "_atomic_copy_migration_file", partial_write)

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

    with pytest.raises(module._MigrationInterrupted, match="SIGTERM"):
        with module._defer_migration_signals():
            handlers[module.signal.SIGTERM](module.signal.SIGTERM, None)
            inside_completed = True

    assert inside_completed is True
    assert handlers == previous


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


def test_project_plan_refuses_dirty_repo_before_commands(tmp_path) -> None:
    module = _load_module()
    repo = _git_repo(tmp_path / "repo")
    lock = module.load_lock(_lock(tmp_path / "lock.json"))
    (repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")

    with pytest.raises(module.SpeckitRuntimeError, match="worktree must be clean"):
        module.build_project_plan(
            "init", repo, lock, allow_multi_install_force=True
        )


def test_project_upgrade_rejects_missing_or_unsupported_integrations(tmp_path) -> None:
    module = _load_module()
    lock = module.load_lock(_lock(tmp_path / "lock.json"))
    repo = _git_repo(tmp_path / "repo")
    _project(repo, integrations=("claude", "codex"))
    subprocess.run(["git", "-C", str(repo), "add", ".specify", ".claude", ".agents"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "speckit"], check=True)

    with pytest.raises(module.SpeckitRuntimeError, match="missing required integrations: agy"):
        module.build_project_plan("upgrade", repo, lock)

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
        "repo": str(repo),
        "required_version": "0.16.5",
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
        "repo": str(repo),
        "required_version": "0.16.5",
        "commands": [["specify", "integration", "upgrade", "claude"]],
    }
    monkeypatch.setattr(
        module,
        "installed_version",
        lambda: {"available": True, "executable": "/bin/specify", "version": "0.16.6", "error": None},
    )
    monkeypatch.setattr(
        module,
        "_run_command",
        lambda *_args, **_kwargs: pytest.fail("project command ran with runtime drift"),
    )

    with pytest.raises(module.SpeckitRuntimeError, match="requires pinned Spec Kit 0.16.5"):
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
