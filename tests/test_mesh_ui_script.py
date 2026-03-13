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


def test_mesh_ui_role_shell_marks_repo_safe_for_git():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "mesh_ui_role_shell.sh"
    content = script_path.read_text(encoding="utf-8")

    assert 'git config --global --add safe.directory "$target_dir"' in content


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


def test_select_live_sessions_prefers_exact_role_match():
    module = _load_module()
    session = {
        "session_id": "sess-lead",
        "cli_type": "codex",
        "metadata": {
            "tmux_session": "mesh-codex-codex-1234",
            "working_dir": "/media/sam/1TB/demo",
        },
        "updated_at": "2026-03-10T19:20:00Z",
        "created_at": "2026-03-10T19:10:00Z",
    }
    task = {
        "task_id": "task-lead",
        "repo": "/media/sam/1TB/demo",
        "role": "lead",
        "target_cli": "codex",
        "status": "running",
        "updated_at": "2026-03-10T19:21:00Z",
    }

    selected = module._select_live_sessions_for_roles(
        ["lead", "worker-codex"],
        "/media/sam/1TB/demo",
        "demo",
        [(session, task)],
    )

    assert "lead" in selected
    assert "worker-codex" not in selected


def test_build_tmux_attach_remote_init_uses_provider_runtime_user(monkeypatch):
    module = _load_module()
    monkeypatch.setattr(
        module,
        "_load_provider_session_users",
        lambda config_path=None: {"codex": "mesh-worker", "gemini": "sam"},
    )
    session = {
        "session_id": "sess-1",
        "cli_type": "codex",
        "metadata": {"tmux_session": "mesh-codex-codex-abcd"},
    }
    task = {"title": "Speckit Specify snake game codex"}

    remote_init = module._build_tmux_attach_remote_init("lead", session, task)

    assert "sudo -u mesh-worker tmux attach -t mesh-codex-codex-abcd" in remote_init
    assert "Speckit Specify snake game codex" in remote_init


def test_discover_live_remote_inits_returns_attach_for_matching_role(monkeypatch):
    module = _load_module()
    cfg = module.UiConfig(
        repo="/media/sam/1TB/demo",
        repo_name="demo",
        roles=["boss", "lead", "worker-codex"],
        max_panes_per_tab=5,
        single_tab=False,
        replace_tabs=True,
        preset="team-4x3",
        attach_live=True,
    )
    monkeypatch.setattr(module, "_load_router_env", lambda: ("http://router", "token"))
    monkeypatch.setattr(
        module,
        "_load_provider_session_users",
        lambda config_path=None: {"codex": "mesh-worker"},
    )

    def fake_router_get_json(router_url: str, auth_token: str, path: str):
        if path == "/sessions?state=open&limit=200":
            return {
                "sessions": [
                    {
                        "session_id": "sess-lead",
                        "task_id": "task-lead",
                        "cli_type": "codex",
                        "metadata": {
                            "tmux_session": "mesh-codex-codex-abcd",
                            "working_dir": "/media/sam/1TB/demo",
                        },
                        "updated_at": "2026-03-10T19:20:00Z",
                        "created_at": "2026-03-10T19:10:00Z",
                    }
                ]
            }
        if path == "/tasks/task-lead":
            return {
                "task_id": "task-lead",
                "repo": "/media/sam/1TB/demo",
                "role": "lead",
                "target_cli": "codex",
                "status": "running",
                "title": "Speckit Specify snake game codex",
                "updated_at": "2026-03-10T19:21:00Z",
            }
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(module, "_router_get_json", fake_router_get_json)

    remote_inits = module._discover_live_remote_inits(cfg)

    assert "lead" in remote_inits
    assert "worker-codex" not in remote_inits
    assert "sudo -u mesh-worker tmux attach -t mesh-codex-codex-abcd" in remote_inits["lead"]


def test_default_ui_roles_fit_two_tabs_with_three_panes():
    module = _load_module()

    assert module.DEFAULT_ROLES == [
        "boss",
        "president",
        "lead",
        "worker-codex",
        "worker-gemini",
        "verifier",
    ]
    assert module._split_groups(module.DEFAULT_ROLES, 3) == [
        ["boss", "president", "lead"],
        ["worker-codex", "worker-gemini", "verifier"],
    ]
