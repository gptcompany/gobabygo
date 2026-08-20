from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import tomllib


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "mesh_probity_cli.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("mesh_probity_cli", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git_repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


def test_lock_is_exact_and_codex_only() -> None:
    module = _load_module()

    lock = module.load_lock()

    assert lock["package"] == "@nizos/probity"
    assert lock["version"] == "1.10.0"
    assert lock["supported_agents"] == ["codex"]


def test_project_requires_one_root_config(tmp_path) -> None:
    module = _load_module()
    repo = _git_repo(tmp_path / "repo")

    assert module.inspect_project(repo)["state"] == "missing"
    (repo / "probity.config.ts").write_text("export default {}\n", encoding="utf-8")
    assert module.inspect_project(repo)["state"] == "enabled"
    (repo / "probity.config.js").write_text("export default {}\n", encoding="utf-8")

    result = module.inspect_project(repo)
    assert result["state"] == "ambiguous"
    assert result["config"] is None


def test_project_rejects_symlink_config(tmp_path) -> None:
    module = _load_module()
    repo = _git_repo(tmp_path / "repo")
    external = tmp_path / "external.mjs"
    external.write_text("export default {}\n", encoding="utf-8")
    (repo / "probity.config.mjs").symlink_to(external)

    result = module.inspect_project(repo)

    assert result["state"] == "unsafe"
    assert result["unsafe_config_candidates"] == ["probity.config.mjs"]
    assert result["config"] is None


def test_status_is_read_only_and_reports_missing_runtime(monkeypatch, tmp_path) -> None:
    module = _load_module()
    repo = _git_repo(tmp_path / "repo")
    (repo / "probity.config.ts").write_text("export default {}\n", encoding="utf-8")
    monkeypatch.setattr(module, "_probity_executable", lambda: None)

    payload = module.build_status(repo)

    assert payload["schema"] == "mesh.probity.status.v1"
    assert payload["installed"] == {"executable": None, "version": None}
    assert payload["project"]["state"] == "enabled"
    assert payload["aligned"] is False


def test_installed_runtime_allows_slow_cli_startup(monkeypatch, tmp_path) -> None:
    module = _load_module()
    executable = tmp_path / "probity"
    executable.write_text("#!/bin/sh\nprintf '1.10.0\\n'\n", encoding="utf-8")
    executable.chmod(0o755)
    observed = {}

    def fake_run(*args, **kwargs):
        observed["timeout"] = kwargs["timeout"]
        return subprocess.CompletedProcess(args[0], 0, stdout="1.10.0\n", stderr="")

    monkeypatch.setattr(module, "_probity_executable", lambda: str(executable))
    monkeypatch.setattr(module.subprocess, "run", fake_run)

    assert module.installed_runtime()["version"] == "1.10.0"
    assert observed["timeout"] == 30


def test_cli_json_returns_one_for_unaligned_project(tmp_path) -> None:
    repo = _git_repo(tmp_path / "repo")

    proc = subprocess.run(
        ["python3", str(MODULE_PATH), "status", str(repo), "--json"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["project"]["state"] == "missing"
    assert payload["supported_agents"] == ["codex"]


def test_invalid_lock_fails_closed(tmp_path) -> None:
    module = _load_module()
    lock = tmp_path / "lock.json"
    lock.write_text('{"schema":1,"version":"latest"}\n', encoding="utf-8")

    try:
        module.load_lock(lock)
    except module.ProbityRuntimeError as exc:
        assert str(exc) == "invalid Probity lock"
    else:
        raise AssertionError("invalid lock was accepted")


def test_enable_hooks_feature_replaces_deprecated_alias() -> None:
    module = _load_module()
    original = "model = \"test\"\n\n[features]\ncodex_hooks = false\nmulti_agent = true\n"

    updated = module._enable_hooks_feature(original)

    parsed = tomllib.loads(updated)
    assert parsed["features"] == {"hooks": True, "multi_agent": True}
    assert "codex_hooks" not in updated


def test_enable_hooks_feature_adds_missing_section() -> None:
    module = _load_module()

    updated = module._enable_hooks_feature('model = "test"\n')

    assert tomllib.loads(updated)["features"]["hooks"] is True
    assert updated.count("[features]") == 1


def test_merge_codex_hook_preserves_unrelated_entries_and_is_idempotent() -> None:
    module = _load_module()
    existing = json.dumps(
        {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [{"type": "command", "command": "/tmp/unrelated"}],
                    },
                    {
                        "matcher": "Bash",
                        "hooks": [{"type": "command", "command": "/old/mesh_probity_hook.py"}],
                    },
                ]
            }
        }
    )

    first = module._merge_codex_hook(existing, command="/new/mesh_probity_hook.py")
    second = module._merge_codex_hook(first, command="/new/mesh_probity_hook.py")
    entries = json.loads(second)["hooks"]["PreToolUse"]

    assert len(entries) == 2
    assert entries[0]["hooks"][0]["command"] == "/tmp/unrelated"
    assert entries[1]["hooks"][0]["command"] == "/new/mesh_probity_hook.py"


def test_install_plan_does_not_touch_targets(tmp_path) -> None:
    module = _load_module()
    codex_home = tmp_path / "codex"
    prefix = tmp_path / "npm"
    hook = tmp_path / "lib" / "hook.py"

    plan = module.install_codex(
        apply=False,
        npm_prefix=prefix,
        codex_home=codex_home,
        hook_path=hook,
    )

    assert plan["schema"] == "mesh.probity.install.v1"
    assert plan["version"] == "1.10.0"
    assert plan["trust_review_required"] is True
    assert not codex_home.exists()
    assert not prefix.exists()
    assert not hook.exists()


def test_install_preflights_both_codex_files_before_download(monkeypatch, tmp_path) -> None:
    module = _load_module()
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text('[features]\nhooks = false\n', encoding="utf-8")
    (codex_home / "hooks.json").write_text("not-json\n", encoding="utf-8")
    monkeypatch.setattr(
        module,
        "_npm_pack",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("download started")),
    )

    try:
        module.install_codex(
            apply=True,
            npm_prefix=tmp_path / "npm",
            codex_home=codex_home,
            hook_path=tmp_path / "hook.py",
        )
    except module.ProbityRuntimeError as exc:
        assert "invalid Codex hooks.json" in str(exc)
    else:
        raise AssertionError("invalid hooks file was accepted")
    assert tomllib.loads((codex_home / "config.toml").read_text(encoding="utf-8"))["features"]["hooks"] is False


def test_install_reuses_exact_runtime_and_updates_hook(monkeypatch, tmp_path) -> None:
    module = _load_module()
    prefix = tmp_path / "npm"
    executable = prefix / "bin" / "probity"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\nprintf '1.10.0\\n'\n", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setattr(
        module,
        "_npm_pack",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("download started")),
    )
    codex_home = tmp_path / "codex"
    hook = tmp_path / "lib" / "hook.py"

    result = module.install_codex(
        apply=True,
        npm_prefix=prefix,
        codex_home=codex_home,
        hook_path=hook,
    )

    assert result["runtime_action"] == "reused"
    assert hook.read_text(encoding="utf-8") == module.HOOK_SOURCE.read_text(encoding="utf-8")
    command = json.loads((codex_home / "hooks.json").read_text(encoding="utf-8"))["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert "MESH_PROBITY_EXPECTED_VERSION=1.10.0" in command
    assert f"MESH_PROBITY_BIN={executable}" in command
