from __future__ import annotations

import asyncio
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


def test_mesh_ui_role_shell_has_remote_repo_fallbacks():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "mesh_ui_role_shell.sh"
    content = script_path.read_text(encoding="utf-8")

    assert '"/media/sam/1TB/$repo_name"' in content
    assert '"/tmp/mesh-tasks/$repo_name"' in content


def test_mesh_ui_role_shell_skips_live_attach_when_remote_init_present():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "mesh_ui_role_shell.sh"
    content = script_path.read_text(encoding="utf-8")

    assert 'if [[ "$live_attach_mode" != "pre_resolved" && "${MESH_UI_ATTACH_LIVE:-1}" != "0" && -f "$live_attach_helper" ]]; then' in content


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


def test_command_for_role_uses_yaml_provider_runtime(tmp_path, monkeypatch):
    module = _load_module()
    config = tmp_path / "operator_ui.yaml"
    config.write_text(
        "roles:\n  boss:\n    provider: gemini\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MESH_UI_CONFIG", str(config))
    monkeypatch.delenv("MESH_UI_CMD_BOSS", raising=False)
    monkeypatch.delenv("MESH_UI_PROVIDER_OVERRIDE", raising=False)

    command = module._command_for_role("boss", "/media/sam/1TB/rektslug", "rektslug")

    assert "mesh_ui_role_shell.sh" in command
    assert "ccs gemini" in command
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


def test_command_for_role_provider_override_wins_for_worker(tmp_path, monkeypatch):
    module = _load_module()
    config = tmp_path / "operator_ui.yaml"
    config.write_text(
        "roles:\n  worker-codex:\n    provider: codex\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MESH_UI_CONFIG", str(config))
    monkeypatch.setenv("MESH_UI_PROVIDER_OVERRIDE", "gemini")

    command = module._command_for_role("worker-codex", "/media/sam/1TB/rektslug", "rektslug")

    assert "ccs gemini" in command
    assert "ccs codex" not in command


def test_command_for_role_marks_pre_resolved_live_attach(monkeypatch):
    module = _load_module()
    monkeypatch.delenv("MESH_UI_CMD_LEAD", raising=False)
    monkeypatch.delenv("MESH_UI_CONFIG", raising=False)

    command = module._command_for_role(
        "lead",
        "/media/sam/1TB/rektslug",
        "rektslug",
        live_remote_init="tmux attach -t mesh-demo",
    )

    assert "tmux attach -t mesh-demo" in command
    assert "pre_resolved" in command


def test_command_for_role_uses_provider_runtime_config_override(tmp_path, monkeypatch):
    module = _load_module()
    ui_config = tmp_path / "operator_ui.yaml"
    provider_config = tmp_path / "provider_runtime.yaml"
    ui_config.write_text(
        "roles:\n  boss:\n    provider: gemini\n",
        encoding="utf-8",
    )
    provider_config.write_text(
        "providers:\n  gemini:\n    command_template: \"custom-gemini --repo {target_account}\"\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MESH_UI_CONFIG", str(ui_config))
    monkeypatch.setenv("MESH_PROVIDER_RUNTIME_CONFIG", str(provider_config))

    command = module._command_for_role("boss", "/media/sam/1TB/rektslug", "rektslug")

    assert "custom-gemini --repo gemini" in command


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


class _FakeSession:
    def __init__(self, marker: str | None = None):
        self.marker = marker

    async def async_get_variable(self, name: str):
        assert name == "user.mesh_ui_tab"
        if self.marker is None:
            raise RuntimeError("missing marker")
        return self.marker

    async def async_set_variable(self, name: str, value: str):
        assert name == "user.mesh_ui_tab"
        self.marker = value


class _FakeTab:
    def __init__(self, sessions):
        self.sessions = list(sessions)
        self.current_session = self.sessions[0] if self.sessions else None
        self.closed = False

    async def async_close(self, force=True):
        self.closed = True


def test_is_mesh_ui_tab_checks_all_sessions():
    module = _load_module()
    tab = _FakeTab([_FakeSession(None), _FakeSession("1")])

    assert asyncio.run(module._is_mesh_ui_tab(tab)) is True


def test_mark_mesh_ui_sessions_marks_all_sessions():
    module = _load_module()
    sessions = [_FakeSession(None), _FakeSession(None), _FakeSession(None)]

    asyncio.run(module._mark_mesh_ui_sessions(sessions))

    assert [s.marker for s in sessions] == ["1", "1", "1"]


def test_cleanup_existing_mesh_tabs_closes_marked_tab_even_if_current_session_unmarked():
    module = _load_module()
    marked_tab = _FakeTab([_FakeSession(None), _FakeSession("1")])
    plain_tab = _FakeTab([_FakeSession(None)])
    window = type("Window", (), {"tabs": [marked_tab, plain_tab]})()

    asyncio.run(module._cleanup_existing_mesh_tabs(window))

    assert marked_tab.closed is True
    assert plain_tab.closed is False
