from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess


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
