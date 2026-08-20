from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

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

    assert module.installed_version() == {
        "available": False,
        "executable": None,
        "version": None,
        "error": None,
    }


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
