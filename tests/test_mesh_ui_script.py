from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "mesh_iterm_ui.py"
    spec = importlib.util.spec_from_file_location("mesh_iterm_ui", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_command_for_role_uses_yaml_remote_init(tmp_path, monkeypatch):
    module = _load_module()
    config = tmp_path / "operator_ui.yaml"
    config.write_text(
        "roles:\n  boss:\n    remote_init: \"printf 'boss-ready\\\\n'\"\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MESH_UI_CONFIG", str(config))
    monkeypatch.delenv("MESH_UI_CMD_BOSS", raising=False)

    command = module._command_for_role("boss", "/media/sam/1TB/rektslug", "rektslug")

    assert "mesh_ui_role_shell.sh" in command
    assert "boss-ready" in command
    assert "/media/sam/1TB/rektslug" in command


def test_command_for_role_env_override_wins(tmp_path, monkeypatch):
    module = _load_module()
    config = tmp_path / "operator_ui.yaml"
    config.write_text(
        "roles:\n  boss:\n    remote_init: \"printf 'boss-ready\\\\n'\"\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MESH_UI_CONFIG", str(config))
    monkeypatch.setenv("MESH_UI_CMD_BOSS", "echo role={role} repo={repo_name}")

    command = module._command_for_role("boss", "/media/sam/1TB/rektslug", "rektslug")

    assert command == "echo role=boss repo=rektslug"
