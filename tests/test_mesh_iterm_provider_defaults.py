from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


def _load_module():
    script = Path(__file__).resolve().parents[1] / "scripts" / "mesh_iterm_control.py"
    spec = importlib.util.spec_from_file_location("mesh_iterm_control_provider_defaults", script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("command", ["team-e2e", "speckit-team-e2e", "speckit-team-run"])
def test_active_team_defaults_use_antigravity_writer_and_codex_review(
    monkeypatch, command: str
) -> None:
    module = _load_module()
    argv = ["mesh_iterm_control.py", command, "--repo", "/tmp/repo"]
    if command != "team-e2e":
        argv.extend(["--feature", "provider-migration"])
    monkeypatch.setattr(sys, "argv", argv)

    args = module._parse_args()

    assert args.worker_cmd == "agy"
    assert args.worker_role == "worker-antigravity"
    if command == "speckit-team-run":
        assert args.reviewer_cmd == "codex"
    module._reject_retired_gemini_args(args)


def test_active_team_run_rejects_explicit_gemini(monkeypatch) -> None:
    module = _load_module()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "mesh_iterm_control.py",
            "speckit-team-run",
            "--repo",
            "/tmp/repo",
            "--feature",
            "provider-migration",
            "--worker-cmd",
            "gemini",
        ],
    )

    args = module._parse_args()

    with pytest.raises(RuntimeError, match="retired Gemini"):
        module._reject_retired_gemini_args(args)


def test_historical_two_cli_e2e_cannot_create_a_new_gemini_layout(monkeypatch) -> None:
    module = _load_module()
    monkeypatch.setattr(
        sys,
        "argv",
        ["mesh_iterm_control.py", "two-cli-e2e", "--repo", "/tmp/repo"],
    )

    args = module._parse_args()

    assert args.boss_cmd == ""
    with pytest.raises(RuntimeError, match="no longer an active provider"):
        module._reject_retired_gemini_args(args)
