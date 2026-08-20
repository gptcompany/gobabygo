from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "mesh_probity_hook.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("mesh_probity_hook", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git_repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


def _payload(cwd: Path) -> bytes:
    return json.dumps(
        {
            "session_id": "test-session",
            "transcript_path": None,
            "cwd": str(cwd),
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_use_id": "call-1",
            "tool_input": {"command": "pytest -q"},
        }
    ).encode()


def test_missing_config_is_a_noop(monkeypatch, tmp_path) -> None:
    module = _load_module()
    repo = _git_repo(tmp_path / "repo")
    monkeypatch.setattr(module, "_probity_executable", lambda: (_ for _ in ()).throw(AssertionError()))

    assert json.loads(module.dispatch(_payload(repo))) == {}


def test_opted_in_repo_forwards_exact_payload(monkeypatch, tmp_path) -> None:
    module = _load_module()
    repo = _git_repo(tmp_path / "repo")
    nested = repo / "src"
    nested.mkdir()
    config = repo / "probity.config.ts"
    config.write_text("export default {}\n", encoding="utf-8")
    capture = tmp_path / "capture"
    fake = tmp_path / "probity"
    fake.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$PWD\" \"$@\" > \"$CAPTURE\"\ncat >/dev/null\nprintf '{\"decision\":\"allow\"}\\n'\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    monkeypatch.setenv("CAPTURE", str(capture))
    monkeypatch.setattr(module, "_probity_executable", lambda: str(fake))
    raw = _payload(nested)

    response = json.loads(module.dispatch(raw))

    assert response == {"decision": "allow"}
    assert capture.read_text(encoding="utf-8").splitlines() == [
        str(repo),
        "--agent",
        "codex",
        "--config",
        str(config),
    ]


def test_ambiguous_config_denies_without_running_probity(monkeypatch, tmp_path) -> None:
    module = _load_module()
    repo = _git_repo(tmp_path / "repo")
    (repo / "probity.config.ts").write_text("export default {}\n", encoding="utf-8")
    (repo / "probity.config.js").write_text("export default {}\n", encoding="utf-8")
    monkeypatch.setattr(module, "_probity_executable", lambda: (_ for _ in ()).throw(AssertionError()))

    response = json.loads(module.dispatch(_payload(repo)))

    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "multiple" in response["hookSpecificOutput"]["permissionDecisionReason"]


def test_missing_runtime_denies_only_an_opted_in_repo(monkeypatch, tmp_path) -> None:
    module = _load_module()
    repo = _git_repo(tmp_path / "repo")
    (repo / "probity.config.ts").write_text("export default {}\n", encoding="utf-8")
    monkeypatch.setattr(module, "_probity_executable", lambda: None)

    response = json.loads(module.dispatch(_payload(repo)))

    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "unavailable" in response["hookSpecificOutput"]["permissionDecisionReason"]


def test_invalid_probity_output_fails_closed(monkeypatch, tmp_path) -> None:
    module = _load_module()
    repo = _git_repo(tmp_path / "repo")
    (repo / "probity.config.ts").write_text("export default {}\n", encoding="utf-8")
    fake = tmp_path / "probity"
    fake.write_text("#!/bin/sh\ncat >/dev/null\nprintf not-json\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setattr(module, "_probity_executable", lambda: str(fake))

    response = json.loads(module.dispatch(_payload(repo)))

    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "invalid JSON" in response["hookSpecificOutput"]["permissionDecisionReason"]


def test_invalid_or_non_pretool_payload_is_a_noop() -> None:
    module = _load_module()

    assert json.loads(module.dispatch(b"not-json")) == {}
    assert json.loads(module.dispatch(b'{"hook_event_name":"Stop"}')) == {}


def test_cli_never_exposes_probity_stderr(monkeypatch, tmp_path) -> None:
    repo = _git_repo(tmp_path / "repo")
    (repo / "probity.config.ts").write_text("export default {}\n", encoding="utf-8")
    fake = tmp_path / "probity"
    fake.write_text("#!/bin/sh\ncat >/dev/null\necho SECRET >&2\nexit 7\n", encoding="utf-8")
    fake.chmod(0o755)

    proc = subprocess.run(
        ["python3", str(MODULE_PATH)],
        input=_payload(repo),
        env={**os.environ, "MESH_PROBITY_BIN": str(fake)},
        check=False,
        capture_output=True,
    )

    assert proc.returncode == 0
    assert b"SECRET" not in proc.stdout + proc.stderr
    assert json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
