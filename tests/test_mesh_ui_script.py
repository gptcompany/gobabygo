from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import yaml


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "mesh_iterm_ui.py"
    spec = importlib.util.spec_from_file_location("mesh_iterm_ui", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_live_attach_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "mesh_ui_live_attach.py"
    spec = importlib.util.spec_from_file_location("mesh_ui_live_attach", script_path)
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


def test_mesh_ui_role_shell_sets_role_label_and_badge():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "mesh_ui_role_shell.sh"
    content = script_path.read_text(encoding="utf-8")

    assert "printf '\\033]0;%s\\007' \"$label\"" in content
    assert "SetBadgeFormat" in content
    assert 'label="mesh:${role} (operator) | ${repo_name}"' in content
    assert 'label="mesh:${role} (${LAUNCH_MODE}:${PROVIDER}:${session_short}) | ${repo_name}"' in content
    assert content.count("set_session_label() {") >= 2
    assert content.count("emit_role_banner() {") >= 2


def test_mesh_ui_role_shell_banner_shows_runtime_identity():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "mesh_ui_role_shell.sh"
    content = script_path.read_text(encoding="utf-8")

    assert "provider=%s session=%s repo=%s ui_group=%s" in content


def test_mesh_ui_role_shell_exports_ui_group_id():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "mesh_ui_role_shell.sh"
    content = script_path.read_text(encoding="utf-8")

    assert 'UI_GROUP_ID="${7:-}"' in content
    assert 'LAUNCH_MODE="${8:-}"' in content
    assert 'PROVIDER="${9:-}"' in content
    assert 'SESSION_ID="${10:-}"' in content
    assert 'export MESH_UI_GROUP_ID="$ui_group_id"' in content
    assert 'export MESH_UI_LAUNCH_MODE="$launch_mode"' in content
    assert 'export MESH_UI_PROVIDER="$provider"' in content
    assert 'export MESH_UI_SESSION_ID="$session_id"' in content
    assert 'UI_GROUP_ID=%q LAUNCH_MODE=%q PROVIDER=%q SESSION_ID=%q bash -lc %q' in content


def test_operator_ui_boss_is_not_provider_backed():
    config_path = Path(__file__).resolve().parents[1] / "mapping" / "operator_ui.yaml"
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    boss = data["roles"]["boss"]

    assert "provider" not in boss
    assert "remote_init" in boss


def test_mesh_ui_role_shell_has_remote_repo_fallbacks():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "mesh_ui_role_shell.sh"
    content = script_path.read_text(encoding="utf-8")

    assert '"/media/sam/1TB/$repo_name"' in content
    assert '"/tmp/mesh-tasks/$repo_name"' in content


def test_mesh_ui_role_shell_skips_live_attach_when_remote_init_present():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "mesh_ui_role_shell.sh"
    content = script_path.read_text(encoding="utf-8")

    assert 'if [[ "$live_attach_mode" != "pre_resolved" && "${MESH_UI_ATTACH_LIVE:-1}" != "0" && -f "$live_attach_helper" ]]; then' in content


def test_mesh_ui_live_attach_uses_exported_ui_group_id(monkeypatch, capsys):
    module = _load_live_attach_module()
    captured = SimpleNamespace(cfg=None)

    class FakeUiModule:
        class UiConfig:
            def __init__(self, **kwargs):
                for key, value in kwargs.items():
                    setattr(self, key, value)
                captured.cfg = SimpleNamespace(**kwargs)

        @staticmethod
        def _discover_live_remote_inits(cfg):
            return {"lead": f"attach:{cfg.ui_group_id}"}

    monkeypatch.setattr(module, "_load_mesh_iterm_ui", lambda: FakeUiModule)
    monkeypatch.setenv("MESH_UI_GROUP_ID", "demo-ui-9")
    monkeypatch.setattr(
        sys,
        "argv",
        ["mesh_ui_live_attach.py", "lead", "/media/sam/1TB/demo", "demo", "boss,lead"],
    )

    assert module.main() == 0
    assert capsys.readouterr().out.strip() == "attach:demo-ui-9"
    assert captured.cfg is not None
    assert captured.cfg.ui_group_id == "demo-ui-9"


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

    command = module._command_for_role(
        "boss",
        "/media/sam/1TB/rektslug",
        "rektslug",
        ui_group_id="rektslug-ui-1",
    )

    assert "env MESH_UI_GROUP_ID=rektslug-ui-1" in command
    assert "bash -lc" in command
    assert "echo role=boss repo=rektslug" in command


def test_command_for_role_env_override_accepts_ui_group_placeholder(monkeypatch):
    module = _load_module()
    monkeypatch.setenv("MESH_UI_CMD_BOSS", "echo group={ui_group_id}")

    command = module._command_for_role(
        "boss",
        "/media/sam/1TB/rektslug",
        "rektslug",
        ui_group_id="rektslug-ui-1",
    )

    assert "env MESH_UI_GROUP_ID=rektslug-ui-1" in command
    assert "bash -lc" in command
    assert "echo group=rektslug-ui-1" in command


def test_default_mapping_routes_worker_codex_to_work_codex(monkeypatch):
    module = _load_module()
    monkeypatch.delenv("MESH_UI_CONFIG", raising=False)
    monkeypatch.delenv("MESH_UI_PROVIDER_OVERRIDE", raising=False)

    provider, target_account = module._resolve_role_task_target("worker-codex")

    assert provider == "codex"
    assert target_account == "work-codex"


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


def test_command_for_role_passes_ui_group_id(monkeypatch):
    module = _load_module()
    monkeypatch.delenv("MESH_UI_CMD_BOSS", raising=False)
    monkeypatch.delenv("MESH_UI_CONFIG", raising=False)

    command = module._command_for_role(
        "boss",
        "/media/sam/1TB/rektslug",
        "rektslug",
        ui_group_id="rektslug-ui-1",
    )

    assert "rektslug-ui-1" in command


def test_command_for_role_passes_runtime_identity_to_helper(monkeypatch):
    module = _load_module()
    monkeypatch.delenv("MESH_UI_CMD_LEAD", raising=False)
    monkeypatch.delenv("MESH_UI_CONFIG", raising=False)

    command = module._command_for_role(
        "lead",
        "/media/sam/1TB/rektslug",
        "rektslug",
        ui_group_id="rektslug-ui-1",
        launch_mode="spawn",
        provider="gemini",
        session_id="12345678-abcd-ef01-2345-6789abcdef01",
    )

    assert "spawn" in command
    assert "gemini" in command
    assert "12345678-abcd-ef01-2345-6789abcdef01" in command


def test_command_for_role_falls_back_to_role_provider_for_spawn_labels(tmp_path, monkeypatch):
    module = _load_module()
    config = tmp_path / "operator_ui.yaml"
    config.write_text(
        "roles:\n  worker-codex:\n    provider: codex\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MESH_UI_CONFIG", str(config))
    monkeypatch.delenv("MESH_UI_CMD_WORKER_CODEX", raising=False)

    command = module._command_for_role(
        "worker-codex",
        "/media/sam/1TB/rektslug",
        "rektslug",
        ui_group_id="rektslug-ui-1",
        launch_mode="spawn",
        provider="",
        session_id="",
    )

    assert "codex" in command


def test_command_for_role_yaml_command_template_accepts_ui_group_id(tmp_path, monkeypatch):
    module = _load_module()
    config = tmp_path / "operator_ui.yaml"
    config.write_text(
        "roles:\n  boss:\n    command_template: \"echo group={ui_group_id} repo={repo_name}\"\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MESH_UI_CONFIG", str(config))
    monkeypatch.delenv("MESH_UI_CMD_BOSS", raising=False)

    command = module._command_for_role(
        "boss",
        "/media/sam/1TB/rektslug",
        "rektslug",
        ui_group_id="rektslug-ui-1",
    )

    assert "env MESH_UI_GROUP_ID=rektslug-ui-1" in command
    assert "bash -lc" in command
    assert "echo group=rektslug-ui-1 repo=rektslug" in command


def test_command_for_role_custom_override_receives_runtime_identity_env(monkeypatch):
    module = _load_module()
    monkeypatch.setenv("MESH_UI_CMD_BOSS", "env | grep '^MESH_UI_'")

    command = module._command_for_role(
        "boss",
        "/media/sam/1TB/rektslug",
        "rektslug",
        ui_group_id="rektslug-ui-1",
        launch_mode="attach",
        provider="gemini",
        session_id="sess-1234",
    )

    assert "MESH_UI_GROUP_ID=rektslug-ui-1" in command
    assert "MESH_UI_LAUNCH_MODE=attach" in command
    assert "MESH_UI_PROVIDER=gemini" in command
    assert "MESH_UI_SESSION_ID=sess-1234" in command


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


def test_ui_group_cache_round_trip(tmp_path):
    module = _load_module()
    repo_path = "/Users/sam/snake-game"

    path = module._write_ui_group_cache(
        "snake-game",
        "snake-game-ui-20260314T120000Z",
        repo_path=repo_path,
        cache_dir=tmp_path,
    )

    assert path == module._ui_group_cache_path("snake-game", repo_path=repo_path, cache_dir=tmp_path)
    assert module._read_ui_group_cache("snake-game", repo_path=repo_path, cache_dir=tmp_path) == {
        "repo_name": "snake-game",
        "ui_group_id": "snake-game-ui-20260314T120000Z",
        "repo_path": repo_path,
    }

    module._clear_ui_group_cache("snake-game", repo_path=repo_path, cache_dir=tmp_path)
    assert module._read_ui_group_cache("snake-game", repo_path=repo_path, cache_dir=tmp_path) is None


def test_resolve_active_ui_group_id_reuses_live_cached_group(tmp_path, monkeypatch):
    module = _load_module()
    repo_path = "/Users/sam/snake-game"
    module._write_ui_group_cache(
        "snake-game",
        "snake-game-ui-20260314T120000Z",
        repo_path=repo_path,
        cache_dir=tmp_path,
    )

    monkeypatch.setattr(module, "_router_has_live_ui_group", lambda *args, **kwargs: True)

    ui_group_id = module._resolve_active_ui_group_id(
        "snake-game",
        repo_path=repo_path,
        router_url="http://router",
        auth_token="token",
        cache_dir=tmp_path,
        timestamp="20260315T130000Z",
    )

    assert ui_group_id == "snake-game-ui-20260314T120000Z"
    assert module._read_ui_group_cache("snake-game", repo_path=repo_path, cache_dir=tmp_path) == {
        "repo_name": "snake-game",
        "ui_group_id": "snake-game-ui-20260314T120000Z",
        "repo_path": repo_path,
    }


def test_resolve_active_ui_group_id_replaces_stale_cached_group(tmp_path, monkeypatch):
    module = _load_module()
    repo_path = "/Users/sam/snake-game"
    module._write_ui_group_cache(
        "snake-game",
        "snake-game-ui-20260314T120000Z",
        repo_path=repo_path,
        cache_dir=tmp_path,
    )

    monkeypatch.setattr(module, "_router_has_live_ui_group", lambda *args, **kwargs: False)

    ui_group_id = module._resolve_active_ui_group_id(
        "snake-game",
        repo_path=repo_path,
        router_url="http://router",
        auth_token="token",
        cache_dir=tmp_path,
        timestamp="20260315T130000Z",
    )

    assert ui_group_id == "snake-game-ui-20260315T130000Z"
    assert module._read_ui_group_cache("snake-game", repo_path=repo_path, cache_dir=tmp_path) == {
        "repo_name": "snake-game",
        "ui_group_id": "snake-game-ui-20260315T130000Z",
        "repo_path": repo_path,
    }


def test_resolve_active_ui_group_id_preserves_cached_group_when_live_check_is_indeterminate(tmp_path, monkeypatch):
    module = _load_module()
    repo_path = "/Users/sam/snake-game"
    module._write_ui_group_cache(
        "snake-game",
        "snake-game-ui-20260314T120000Z",
        repo_path=repo_path,
        cache_dir=tmp_path,
    )

    monkeypatch.setattr(module, "_router_has_live_ui_group", lambda *args, **kwargs: None)

    ui_group_id = module._resolve_active_ui_group_id(
        "snake-game",
        repo_path=repo_path,
        router_url="http://router",
        auth_token="token",
        cache_dir=tmp_path,
        timestamp="20260315T130000Z",
    )

    assert ui_group_id == "snake-game-ui-20260314T120000Z"
    assert module._read_ui_group_cache("snake-game", repo_path=repo_path, cache_dir=tmp_path) == {
        "repo_name": "snake-game",
        "ui_group_id": "snake-game-ui-20260314T120000Z",
        "repo_path": repo_path,
    }


def test_resolve_active_ui_group_id_ignores_cache_for_other_repo_path(tmp_path, monkeypatch):
    module = _load_module()
    module._write_ui_group_cache(
        "snake-game",
        "snake-game-ui-20260314T120000Z",
        repo_path="/Users/sam/other/snake-game",
        cache_dir=tmp_path,
    )

    monkeypatch.setattr(module, "_router_has_live_ui_group", lambda *args, **kwargs: True)

    ui_group_id = module._resolve_active_ui_group_id(
        "snake-game",
        repo_path="/Users/sam/snake-game",
        router_url="http://router",
        auth_token="token",
        cache_dir=tmp_path,
        timestamp="20260315T130000Z",
    )

    assert ui_group_id == "snake-game-ui-20260315T130000Z"


def test_select_live_sessions_prefers_exact_role_match():
    module = _load_module()
    session = {
        "session_id": "sess-lead",
        "cli_type": "codex",
        "metadata": {
            "tmux_session": "mesh-codex-codex-1234",
            "repo": "/media/sam/1TB/demo",
            "ui_group_id": "demo-ui-1",
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
        "demo-ui-1",
        [(session, task)],
    )

    assert "lead" in selected
    assert "worker-codex" not in selected


def test_select_live_sessions_allows_exact_worker_role_under_provider_override():
    module = _load_module()
    session = {
        "session_id": "sess-worker",
        "cli_type": "gemini",
        "metadata": {
            "tmux_session": "mesh-gemini-gemini-1234",
            "repo": "/media/sam/1TB/demo",
            "ui_group_id": "demo-ui-1",
        },
        "updated_at": "2026-03-10T19:20:00Z",
        "created_at": "2026-03-10T19:10:00Z",
    }
    task = {
        "task_id": "task-worker",
        "repo": "/media/sam/1TB/demo",
        "role": "worker-codex",
        "target_cli": "gemini",
        "status": "running",
        "updated_at": "2026-03-10T19:21:00Z",
    }

    selected = module._select_live_sessions_for_roles(
        ["worker-codex"],
        "/media/sam/1TB/demo",
        "demo",
        "demo-ui-1",
        [(session, task)],
    )

    assert selected["worker-codex"][0]["session_id"] == "sess-worker"


def test_build_role_launch_plans_attaches_matching_session_and_spawns_missing_roles(monkeypatch):
    module = _load_module()
    cfg = module.UiConfig(
        repo="/media/sam/1TB/demo",
        repo_name="demo",
        roles=["lead", "worker-codex"],
        max_panes_per_tab=3,
        single_tab=False,
        replace_tabs=True,
        preset="auto",
        attach_live=True,
        ui_group_id="demo-ui-20260315T130000Z",
    )
    monkeypatch.setattr(
        module,
        "_load_provider_session_users",
        lambda config_path=None: {"codex": "mesh-worker"},
    )
    session = {
        "session_id": "sess-lead",
        "cli_type": "codex",
        "metadata": {
            "tmux_session": "mesh-codex-codex-abcd",
            "repo": "/media/sam/1TB/demo",
            "ui_group_id": "demo-ui-20260315T130000Z",
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
        "title": "Speckit Specify snake game codex",
        "updated_at": "2026-03-10T19:21:00Z",
    }

    plans = module._build_role_launch_plans(cfg, [(session, task)])

    assert plans["lead"].mode == "attach"
    assert plans["lead"].session_id == "sess-lead"
    assert "sudo -u mesh-worker tmux attach -t mesh-codex-codex-abcd" in plans["lead"].remote_init
    assert plans["worker-codex"].mode == "spawn"
    assert plans["worker-codex"].remote_init == ""


def test_select_live_sessions_filters_to_active_ui_group():
    module = _load_module()
    matching = (
        {
            "session_id": "sess-matching",
            "cli_type": "gemini",
            "metadata": {
                "repo": "/media/sam/1TB/demo",
                "ui_group_id": "demo-ui-1",
            },
            "updated_at": "2026-03-10T19:20:00Z",
            "created_at": "2026-03-10T19:10:00Z",
        },
        {
            "task_id": "task-matching",
            "repo": "/media/sam/1TB/demo",
            "role": "lead",
            "target_cli": "gemini",
            "status": "running",
            "updated_at": "2026-03-10T19:21:00Z",
        },
    )
    other_group = (
        {
            "session_id": "sess-other",
            "cli_type": "gemini",
            "metadata": {
                "repo": "/media/sam/1TB/demo",
                "ui_group_id": "demo-ui-2",
            },
            "updated_at": "2026-03-10T19:22:00Z",
            "created_at": "2026-03-10T19:11:00Z",
        },
        {
            "task_id": "task-other",
            "repo": "/media/sam/1TB/demo",
            "role": "lead",
            "target_cli": "gemini",
            "status": "running",
            "updated_at": "2026-03-10T19:23:00Z",
        },
    )

    selected = module._select_live_sessions_for_roles(
        ["lead"],
        "/media/sam/1TB/demo",
        "demo",
        "demo-ui-1",
        [matching, other_group],
    )

    assert selected["lead"][0]["session_id"] == "sess-matching"


def test_select_live_sessions_falls_back_to_task_payload_ui_group_for_mixed_worker_versions():
    module = _load_module()
    matching = (
        {
            "session_id": "sess-matching",
            "cli_type": "gemini",
            "metadata": {
                "repo": "/media/sam/1TB/demo",
            },
            "updated_at": "2026-03-10T19:20:00Z",
            "created_at": "2026-03-10T19:10:00Z",
        },
        {
            "task_id": "task-matching",
            "repo": "/media/sam/1TB/demo",
            "role": "lead",
            "target_cli": "gemini",
            "status": "running",
            "payload": {"ui_group_id": "demo-ui-1"},
            "updated_at": "2026-03-10T19:21:00Z",
        },
    )
    other_group = (
        {
            "session_id": "sess-other",
            "cli_type": "gemini",
            "metadata": {
                "repo": "/media/sam/1TB/demo",
            },
            "updated_at": "2026-03-10T19:22:00Z",
            "created_at": "2026-03-10T19:11:00Z",
        },
        {
            "task_id": "task-other",
            "repo": "/media/sam/1TB/demo",
            "role": "lead",
            "target_cli": "gemini",
            "status": "running",
            "payload": {"ui_group_id": "demo-ui-2"},
            "updated_at": "2026-03-10T19:23:00Z",
        },
    )

    selected = module._select_live_sessions_for_roles(
        ["lead"],
        "/media/sam/1TB/demo",
        "demo",
        "demo-ui-1",
        [matching, other_group],
    )

    assert selected["lead"][0]["session_id"] == "sess-matching"


def test_build_role_launch_plan_without_attach_handle_falls_back_to_spawn():
    module = _load_module()
    session = {
        "session_id": "sess-lead",
        "cli_type": "codex",
        "metadata": {
            "repo": "/media/sam/1TB/demo",
        },
    }
    task = {
        "task_id": "task-lead",
        "repo": "/media/sam/1TB/demo",
        "role": "lead",
        "target_cli": "codex",
    }

    plan = module._build_role_launch_plan("lead", (session, task))

    assert plan.mode == "spawn"
    assert plan.session_id == ""


def test_create_ui_role_task_posts_expected_payload(monkeypatch):
    module = _load_module()
    cfg = module.UiConfig(
        repo="/media/sam/1TB/demo",
        repo_name="demo",
        roles=["lead"],
        max_panes_per_tab=3,
        single_tab=False,
        replace_tabs=True,
        preset="auto",
        attach_live=True,
        ui_group_id="demo-ui-1",
    )
    monkeypatch.setattr(module, "_load_ui_role_rules", lambda config_path=None: {"lead": {"provider": "gemini"}})
    calls = []

    def fake_post(router_url: str, auth_token: str, path: str, payload: dict):
        calls.append((router_url, auth_token, path, payload))
        return {"task_id": "task-lead-1"}

    monkeypatch.setattr(module, "_router_post_json", fake_post)

    task_info = module._create_ui_role_task("http://router", "token", cfg, "lead")

    assert task_info == {"role": "lead", "task_id": "task-lead-1", "target_cli": "gemini", "created": True}
    assert len(calls) == 1
    _, _, path, payload = calls[0]
    assert path == "/tasks"
    assert payload["repo"] == "/media/sam/1TB/demo"
    assert payload["role"] == "lead"
    assert payload["target_cli"] == "gemini"
    assert payload["execution_mode"] == "session"
    assert payload["idempotency_key"].startswith("mesh-ui::demo-ui-1::lead::")
    assert payload["payload"]["ui_role_session"] is True
    assert payload["payload"]["ui_group_id"] == "demo-ui-1"
    assert payload["payload"]["working_dir"] == "/media/sam/1TB/demo"
    assert payload["payload"]["prompt"].startswith("You are lead for repository demo")
    assert "Acknowledge readiness briefly" in payload["payload"]["prompt"]


def test_create_ui_role_task_bootstraps_worker_codex_prompt(monkeypatch):
    module = _load_module()
    cfg = module.UiConfig(
        repo="/media/sam/1TB/snake-game",
        repo_name="snake-game",
        roles=["worker-codex"],
        max_panes_per_tab=3,
        single_tab=False,
        replace_tabs=True,
        preset="auto",
        attach_live=True,
        ui_group_id="snake-game-ui-1",
    )
    calls = []

    def fake_post(router_url: str, auth_token: str, path: str, payload: dict):
        calls.append((router_url, auth_token, path, payload))
        return {"task_id": "task-worker-codex-1"}

    monkeypatch.setattr(module, "_router_post_json", fake_post)

    task_info = module._create_ui_role_task("http://router", "token", cfg, "worker-codex")

    assert task_info == {"role": "worker-codex", "task_id": "task-worker-codex-1", "target_cli": "codex", "created": True}
    _, _, path, payload = calls[0]
    assert path == "/tasks"
    assert payload["target_account"] == "work-codex"
    assert payload["payload"]["prompt"].startswith("You are worker-codex for repository snake-game")
    assert "Do not exit" in payload["payload"]["prompt"]


def test_ui_role_bootstrap_prompt_uses_role_override_env(monkeypatch):
    module = _load_module()
    cfg = module.UiConfig(
        repo="/media/sam/1TB/demo",
        repo_name="demo",
        roles=["president"],
        max_panes_per_tab=3,
        single_tab=False,
        replace_tabs=True,
        preset="auto",
        attach_live=True,
        ui_group_id="demo-ui-1",
    )

    monkeypatch.setenv(
        "MESH_UI_BOOTSTRAP_PROMPT_PRESIDENT",
        "Ciao dal {role} per {repo_name} in {repo}. Coordina un saluto collettivo e non uscire.",
    )

    prompt = module._ui_role_bootstrap_prompt(cfg, "president", "gemini")

    assert prompt == (
        "Ciao dal president per demo in /media/sam/1TB/demo. "
        "Coordina un saluto collettivo e non uscire."
    )


def test_create_ui_role_task_reuses_existing_pending_task(monkeypatch):
    module = _load_module()
    cfg = module.UiConfig(
        repo="/media/sam/1TB/demo",
        repo_name="demo",
        roles=["lead"],
        max_panes_per_tab=3,
        single_tab=False,
        replace_tabs=True,
        preset="auto",
        attach_live=True,
        ui_group_id="demo-ui-1",
    )
    monkeypatch.setattr(
        module,
        "_find_existing_ui_role_task",
        lambda router_url, auth_token, cfg_value, role: {
            "task_id": "task-lead-existing",
            "target_cli": "gemini",
        },
    )
    called = {"post": False}
    monkeypatch.setattr(
        module,
        "_router_post_json",
        lambda *args, **kwargs: called.__setitem__("post", True),
    )

    task_info = module._create_ui_role_task("http://router", "token", cfg, "lead")

    assert task_info == {
        "role": "lead",
        "task_id": "task-lead-existing",
        "target_cli": "gemini",
        "created": False,
    }
    assert called["post"] is False


def test_create_ui_role_task_does_not_reuse_terminal_existing_task(monkeypatch):
    module = _load_module()
    cfg = module.UiConfig(
        repo="/media/sam/1TB/demo",
        repo_name="demo",
        roles=["lead"],
        max_panes_per_tab=3,
        single_tab=False,
        replace_tabs=True,
        preset="auto",
        attach_live=True,
        ui_group_id="demo-ui-1",
    )
    monkeypatch.setattr(module, "_load_ui_role_rules", lambda config_path=None: {"lead": {"provider": "gemini"}})
    monkeypatch.setattr(
        module,
        "_find_existing_ui_role_task",
        lambda router_url, auth_token, cfg_value, role: {
            "task_id": "task-lead-failed",
            "target_cli": "gemini",
            "status": "failed",
        },
    )
    calls = []

    def fake_post(router_url: str, auth_token: str, path: str, payload: dict):
        calls.append(payload)
        return {"task_id": "task-lead-new"}

    monkeypatch.setattr(module, "_router_post_json", fake_post)

    task_info = module._create_ui_role_task("http://router", "token", cfg, "lead")

    assert task_info == {
        "role": "lead",
        "task_id": "task-lead-new",
        "target_cli": "gemini",
        "created": True,
    }
    assert len(calls) == 1


def test_create_ui_role_task_recovers_existing_task_on_duplicate_idempotency(monkeypatch):
    module = _load_module()
    cfg = module.UiConfig(
        repo="/media/sam/1TB/demo",
        repo_name="demo",
        roles=["lead"],
        max_panes_per_tab=3,
        single_tab=False,
        replace_tabs=True,
        preset="auto",
        attach_live=True,
        ui_group_id="demo-ui-1",
    )
    monkeypatch.setattr(module, "_load_ui_role_rules", lambda config_path=None: {"lead": {"provider": "gemini"}})
    calls = {"find": 0}

    def fake_find(router_url, auth_token, cfg_value, role):
        calls["find"] += 1
        if calls["find"] == 1:
            return None
        return {"task_id": "task-lead-existing", "target_cli": "gemini"}

    def fake_post(router_url, auth_token, path, payload):
        raise module.HTTPError(path, 409, "duplicate", None, None)

    monkeypatch.setattr(module, "_find_existing_ui_role_task", fake_find)
    monkeypatch.setattr(module, "_router_post_json", fake_post)

    task_info = module._create_ui_role_task("http://router", "token", cfg, "lead")

    assert task_info == {
        "role": "lead",
        "task_id": "task-lead-existing",
        "target_cli": "gemini",
        "created": False,
    }


def test_create_ui_role_task_retries_duplicate_when_only_terminal_task_exists(monkeypatch):
    module = _load_module()
    cfg = module.UiConfig(
        repo="/media/sam/1TB/demo",
        repo_name="demo",
        roles=["lead"],
        max_panes_per_tab=3,
        single_tab=False,
        replace_tabs=True,
        preset="auto",
        attach_live=True,
        ui_group_id="demo-ui-1",
    )
    monkeypatch.setattr(module, "_load_ui_role_rules", lambda config_path=None: {"lead": {"provider": "gemini"}})
    calls = {"find": 0}

    def fake_find(router_url, auth_token, cfg_value, role):
        calls["find"] += 1
        if calls["find"] == 1:
            return None
        return {"task_id": "task-lead-cancelled", "target_cli": "gemini", "status": "canceled"}

    post_payloads = []

    def fake_post(router_url, auth_token, path, payload):
        post_payloads.append(dict(payload))
        if len(post_payloads) == 1:
            raise module.HTTPError(path, 409, "duplicate", None, None)
        return {"task_id": "task-lead-new"}

    monkeypatch.setattr(module, "_find_existing_ui_role_task", fake_find)
    monkeypatch.setattr(module, "_router_post_json", fake_post)

    task_info = module._create_ui_role_task("http://router", "token", cfg, "lead")

    assert task_info == {
        "role": "lead",
        "task_id": "task-lead-new",
        "target_cli": "gemini",
        "created": True,
    }
    assert len(post_payloads) == 2
    assert post_payloads[0]["idempotency_key"] != post_payloads[1]["idempotency_key"]


def test_find_existing_ui_role_task_prefers_running_over_queued_duplicates(monkeypatch):
    module = _load_module()
    cfg = module.UiConfig(
        repo="/media/sam/1TB/demo",
        repo_name="demo",
        roles=["lead"],
        max_panes_per_tab=3,
        single_tab=False,
        replace_tabs=True,
        preset="auto",
        attach_live=True,
        ui_group_id="demo-ui-1",
    )

    def fake_get(router_url: str, auth_token: str, path: str):
        if path == "/tasks?status=queued&limit=200":
            return {
                "tasks": [
                    {
                        "task_id": "task-queued",
                        "status": "queued",
                        "repo": "/media/sam/1TB/demo",
                        "role": "lead",
                        "payload": {"ui_role_session": True, "ui_group_id": "demo-ui-1"},
                        "updated_at": "2026-03-10T19:20:00Z",
                    }
                ]
            }
        if path == "/tasks?status=running&limit=200":
            return {
                "tasks": [
                    {
                        "task_id": "task-running",
                        "status": "running",
                        "repo": "/media/sam/1TB/demo",
                        "role": "lead",
                        "payload": {"ui_role_session": True, "ui_group_id": "demo-ui-1"},
                        "updated_at": "2026-03-10T19:10:00Z",
                    }
                ]
            }
        return {"tasks": []}

    monkeypatch.setattr(module, "_router_get_json", fake_get)

    task = module._find_existing_ui_role_task("http://router", "token", cfg, "lead")

    assert task is not None
    assert task["task_id"] == "task-running"


def test_find_existing_ui_role_task_ignores_other_ui_groups(monkeypatch):
    module = _load_module()
    cfg = module.UiConfig(
        repo="/media/sam/1TB/demo",
        repo_name="demo",
        roles=["lead"],
        max_panes_per_tab=3,
        single_tab=False,
        replace_tabs=True,
        preset="auto",
        attach_live=True,
        ui_group_id="demo-ui-2",
    )

    def fake_get(router_url: str, auth_token: str, path: str):
        if path == "/tasks?status=queued&limit=200":
            return {
                "tasks": [
                    {
                        "task_id": "task-other-group",
                        "status": "queued",
                        "repo": "/media/sam/1TB/demo",
                        "role": "lead",
                        "payload": {"ui_role_session": True, "ui_group_id": "demo-ui-1"},
                        "updated_at": "2026-03-10T19:20:00Z",
                    }
                ]
            }
        if path == "/tasks?status=running&limit=200":
            return {
                "tasks": [
                    {
                        "task_id": "task-current-group",
                        "status": "running",
                        "repo": "/media/sam/1TB/demo",
                        "role": "lead",
                        "payload": {"ui_role_session": True, "ui_group_id": "demo-ui-2"},
                        "updated_at": "2026-03-10T19:10:00Z",
                    }
                ]
            }
        return {"tasks": []}

    monkeypatch.setattr(module, "_router_get_json", fake_get)

    task = module._find_existing_ui_role_task("http://router", "token", cfg, "lead")

    assert task is not None
    assert task["task_id"] == "task-current-group"


def test_find_existing_ui_role_task_includes_terminal_states_for_duplicate_recovery(monkeypatch):
    module = _load_module()
    cfg = module.UiConfig(
        repo="/media/sam/1TB/demo",
        repo_name="demo",
        roles=["lead"],
        max_panes_per_tab=3,
        single_tab=False,
        replace_tabs=True,
        preset="auto",
        attach_live=True,
        ui_group_id="demo-ui-1",
    )

    def fake_get(router_url: str, auth_token: str, path: str):
        if path == "/tasks?status=failed&limit=200":
            return {
                "tasks": [
                    {
                        "task_id": "task-failed",
                        "status": "failed",
                        "repo": "/media/sam/1TB/demo",
                        "role": "lead",
                        "payload": {"ui_role_session": True, "ui_group_id": "demo-ui-1"},
                        "updated_at": "2026-03-21T10:20:00Z",
                    }
                ]
            }
        return {"tasks": []}

    monkeypatch.setattr(module, "_router_get_json", fake_get)

    task = module._find_existing_ui_role_task("http://router", "token", cfg, "lead")

    assert task is not None
    assert task["task_id"] == "task-failed"


def test_find_existing_ui_role_task_matches_repo_name_when_cfg_repo_is_path(monkeypatch):
    module = _load_module()
    cfg = module.UiConfig(
        repo="/media/sam/1TB/progressive-deploy",
        repo_name="progressive-deploy",
        roles=["lead"],
        max_panes_per_tab=3,
        single_tab=False,
        replace_tabs=True,
        preset="auto",
        attach_live=True,
        ui_group_id="progressive-deploy-ui-20260321T101109Z",
    )

    def fake_get(router_url: str, auth_token: str, path: str):
        if path == "/tasks?status=running&limit=200":
            return {
                "tasks": [
                    {
                        "task_id": "task-short-repo",
                        "status": "running",
                        "repo": "progressive-deploy",
                        "role": "lead",
                        "payload": {
                            "ui_role_session": True,
                            "ui_group_id": "progressive-deploy-ui-20260321T101109Z",
                            "working_dir": "progressive-deploy",
                        },
                        "updated_at": "2026-03-21T10:20:00Z",
                    }
                ]
            }
        return {"tasks": []}

    monkeypatch.setattr(module, "_router_get_json", fake_get)

    task = module._find_existing_ui_role_task("http://router", "token", cfg, "lead")

    assert task is not None
    assert task["task_id"] == "task-short-repo"


def test_spawn_missing_agent_role_plans_resolves_spawned_sessions(monkeypatch):
    module = _load_module()
    cfg = module.UiConfig(
        repo="/media/sam/1TB/demo",
        repo_name="demo",
        roles=["boss", "lead", "worker-codex"],
        max_panes_per_tab=3,
        single_tab=False,
        replace_tabs=True,
        preset="auto",
        attach_live=True,
        ui_group_id="demo-ui-1",
    )
    existing = {
        "boss": module.RoleLaunchPlan(role="boss", mode="spawn"),
        "lead": module.RoleLaunchPlan(role="lead", mode="spawn"),
        "worker-codex": module.RoleLaunchPlan(role="worker-codex", mode="spawn"),
    }
    created_roles = []
    task_ids = {"lead": "task-lead-1", "worker-codex": "task-worker-1"}

    def fake_create(router_url: str, auth_token: str, cfg_value, role: str):
        created_roles.append(role)
        return {
            "role": role,
            "task_id": task_ids[role],
            "target_cli": "gemini" if role == "lead" else "codex",
            "created": True,
        }

    fetch_calls = {"count": 0}

    def fake_fetch(router_url: str, auth_token: str, task_id: str):
        fetch_calls["count"] += 1
        if task_id == "task-lead-1":
            return (
                {
                    "session_id": "sess-lead",
                    "cli_type": "gemini",
                    "metadata": {
                        "repo": "/media/sam/1TB/demo",
                        "ui_group_id": "demo-ui-1",
                        "tmux_session": "mesh-gemini-lead",
                    },
                },
                {
                    "task_id": "task-lead-1",
                    "repo": "/media/sam/1TB/demo",
                    "role": "lead",
                    "target_cli": "gemini",
                    "status": "running",
                },
            )
        if task_id == "task-worker-1":
            return (
                {
                    "session_id": "sess-worker",
                    "cli_type": "codex",
                    "metadata": {
                        "repo": "/media/sam/1TB/demo",
                        "ui_group_id": "demo-ui-1",
                        "tmux_session": "mesh-codex-worker",
                    },
                },
                {
                    "task_id": "task-worker-1",
                    "repo": "/media/sam/1TB/demo",
                    "role": "worker-codex",
                    "target_cli": "codex",
                    "status": "running",
                },
            )
        return None

    monkeypatch.setattr(module, "_create_ui_role_task", fake_create)
    monkeypatch.setattr(module, "_fetch_live_session_pair_for_task", fake_fetch)
    monkeypatch.setattr(module, "_load_provider_session_users", lambda config_path=None: {"codex": "mesh-worker"})
    monkeypatch.setattr(module.time, "sleep", lambda _: None)

    plans = module._spawn_missing_agent_role_plans(
        cfg,
        existing,
        router_url="http://router",
        auth_token="token",
        timeout_s=5.0,
        poll_interval_s=0.01,
    )

    assert created_roles == ["lead", "worker-codex"]
    assert plans["lead"].mode == "spawn"
    assert plans["lead"].session_id == "sess-lead"
    assert plans["worker-codex"].mode == "spawn"
    assert plans["worker-codex"].session_id == "sess-worker"
    assert "sudo -u mesh-worker tmux attach -t mesh-codex-worker" in plans["worker-codex"].remote_init
    assert fetch_calls["count"] >= 1


def test_spawn_missing_agent_role_plans_ignores_other_group_sessions_until_matching_group_appears(monkeypatch):
    module = _load_module()
    cfg = module.UiConfig(
        repo="/media/sam/1TB/demo",
        repo_name="demo",
        roles=["boss", "lead"],
        max_panes_per_tab=3,
        single_tab=False,
        replace_tabs=True,
        preset="auto",
        attach_live=True,
        ui_group_id="demo-ui-2",
    )
    existing = {
        "boss": module.RoleLaunchPlan(role="boss", mode="spawn"),
        "lead": module.RoleLaunchPlan(role="lead", mode="spawn"),
    }

    monkeypatch.setattr(
        module,
        "_create_ui_role_task",
        lambda router_url, auth_token, cfg_value, role: {
            "role": role,
            "task_id": "task-lead-2",
            "target_cli": "gemini",
            "created": True,
        },
    )

    polls = {"count": 0}

    def fake_fetch(router_url: str, auth_token: str, task_id: str):
        assert task_id == "task-lead-2"
        polls["count"] += 1
        if polls["count"] == 1:
            return None
        return (
            {
                "session_id": "sess-current-group",
                "cli_type": "gemini",
                "metadata": {
                    "repo": "/media/sam/1TB/demo",
                    "ui_group_id": "demo-ui-2",
                    "tmux_session": "mesh-gemini-current",
                },
            },
            {
                "task_id": "task-lead-2",
                "repo": "/media/sam/1TB/demo",
                "role": "lead",
                "target_cli": "gemini",
                "status": "running",
            },
        )

    monkeypatch.setattr(module, "_fetch_live_session_pair_for_task", fake_fetch)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)

    plans = module._spawn_missing_agent_role_plans(
        cfg,
        existing,
        router_url="http://router",
        auth_token="token",
        timeout_s=5.0,
        poll_interval_s=0.01,
    )

    assert polls["count"] >= 2
    assert plans["lead"].mode == "spawn"
    assert plans["lead"].session_id == "sess-current-group"
    assert "mesh-gemini-current" in plans["lead"].remote_init


def test_spawn_missing_agent_role_plans_marks_timeout(monkeypatch):
    module = _load_module()
    cfg = module.UiConfig(
        repo="/media/sam/1TB/demo",
        repo_name="demo",
        roles=["lead"],
        max_panes_per_tab=3,
        single_tab=False,
        replace_tabs=True,
        preset="auto",
        attach_live=True,
        ui_group_id="demo-ui-1",
    )
    existing = {"lead": module.RoleLaunchPlan(role="lead", mode="spawn")}

    monkeypatch.setattr(
        module,
        "_create_ui_role_task",
        lambda router_url, auth_token, cfg_value, role: {
            "role": role,
            "task_id": "task-lead-1",
            "target_cli": "gemini",
            "created": True,
        },
    )
    monkeypatch.setattr(module, "_fetch_live_session_pair_for_task", lambda router_url, auth_token, task_id: None)
    canceled = []
    monkeypatch.setattr(module, "_cancel_ui_role_task", lambda router_url, auth_token, task_id: canceled.append(task_id))
    monkeypatch.setattr(module.time, "sleep", lambda _: None)

    plans = module._spawn_missing_agent_role_plans(
        cfg,
        existing,
        router_url="http://router",
        auth_token="token",
        timeout_s=0.0,
        poll_interval_s=0.01,
    )

    assert plans["lead"].mode == "error"
    assert "retry hint: mesh ui respawn lead" in plans["lead"].remote_init
    assert canceled == ["task-lead-1"]


def test_spawn_missing_agent_role_plans_does_not_cancel_reused_task_on_timeout(monkeypatch):
    module = _load_module()
    cfg = module.UiConfig(
        repo="/media/sam/1TB/demo",
        repo_name="demo",
        roles=["lead"],
        max_panes_per_tab=3,
        single_tab=False,
        replace_tabs=True,
        preset="auto",
        attach_live=True,
        ui_group_id="demo-ui-1",
    )
    existing = {"lead": module.RoleLaunchPlan(role="lead", mode="spawn")}

    monkeypatch.setattr(
        module,
        "_create_ui_role_task",
        lambda router_url, auth_token, cfg_value, role: {
            "role": role,
            "task_id": "task-lead-existing",
            "target_cli": "gemini",
            "created": False,
        },
    )
    monkeypatch.setattr(module, "_fetch_live_session_pair_for_task", lambda router_url, auth_token, task_id: None)
    canceled = []
    monkeypatch.setattr(module, "_cancel_ui_role_task", lambda router_url, auth_token, task_id: canceled.append(task_id))
    monkeypatch.setattr(module.time, "sleep", lambda _: None)

    plans = module._spawn_missing_agent_role_plans(
        cfg,
        existing,
        router_url="http://router",
        auth_token="token",
        timeout_s=0.0,
        poll_interval_s=0.01,
    )

    assert plans["lead"].mode == "error"
    assert canceled == []


def test_spawn_missing_agent_role_plans_marks_router_unavailable():
    module = _load_module()
    cfg = module.UiConfig(
        repo="/media/sam/1TB/demo",
        repo_name="demo",
        roles=["boss", "lead"],
        max_panes_per_tab=3,
        single_tab=False,
        replace_tabs=True,
        preset="auto",
        attach_live=True,
        ui_group_id="demo-ui-1",
    )
    existing = {
        "boss": module.RoleLaunchPlan(role="boss", mode="spawn"),
        "lead": module.RoleLaunchPlan(role="lead", mode="spawn"),
    }

    plans = module._spawn_missing_agent_role_plans(
        cfg,
        existing,
        router_url="",
        auth_token="",
    )

    assert plans["boss"].mode == "spawn"
    assert plans["lead"].mode == "error"
    assert "router unavailable" in plans["lead"].error


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


def test_fetch_live_session_pairs_returns_router_backed_pairs(monkeypatch):
    module = _load_module()

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
                            "repo": "/media/sam/1TB/demo",
                            "ui_group_id": "demo-ui-1",
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

    session_pairs = module._fetch_live_session_pairs("http://router", "token")

    assert len(session_pairs) == 1
    session, task = session_pairs[0]
    assert session["session_id"] == "sess-lead"
    assert task["task_id"] == "task-lead"


def test_fetch_live_session_pairs_keeps_session_when_task_lookup_fails(monkeypatch):
    module = _load_module()

    def fake_router_get_json(router_url: str, auth_token: str, path: str):
        if path == "/sessions?state=open&limit=200":
            return {
                "sessions": [
                    {
                        "session_id": "sess-lead",
                        "task_id": "task-lead",
                        "cli_type": "codex",
                        "metadata": {
                            "repo": "/media/sam/1TB/demo",
                            "role": "lead",
                            "ui_group_id": "demo-ui-1",
                        },
                    }
                ]
            }
        raise module.HTTPError(path, 404, "not found", None, None)

    monkeypatch.setattr(module, "_router_get_json", fake_router_get_json)

    session_pairs = module._fetch_live_session_pairs("http://router", "token")

    assert len(session_pairs) == 1
    session, task = session_pairs[0]
    assert session["metadata"]["repo"] == "/media/sam/1TB/demo"
    assert task == {"task_id": "task-lead"}


def test_fetch_live_session_pair_for_task_returns_router_backed_pair(monkeypatch):
    module = _load_module()

    def fake_router_get_json(router_url: str, auth_token: str, path: str):
        if path == "/tasks/task-lead":
            return {
                "task_id": "task-lead",
                "session_id": "sess-lead",
                "repo": "/media/sam/1TB/demo",
                "role": "lead",
                "target_cli": "gemini",
                "status": "running",
            }
        if path == "/sessions/sess-lead":
            return {
                "session_id": "sess-lead",
                "task_id": "task-lead",
                "cli_type": "gemini",
                "metadata": {
                    "repo": "/media/sam/1TB/demo",
                    "ui_group_id": "demo-ui-1",
                    "tmux_session": "mesh-gemini-lead",
                },
            }
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(module, "_router_get_json", fake_router_get_json)

    pair = module._fetch_live_session_pair_for_task("http://router", "token", "task-lead")

    assert pair is not None
    session, task = pair
    assert session["session_id"] == "sess-lead"
    assert task["task_id"] == "task-lead"


def test_fetch_live_session_pair_for_task_returns_none_without_session_id(monkeypatch):
    module = _load_module()

    def fake_router_get_json(router_url: str, auth_token: str, path: str):
        if path == "/tasks/task-lead":
            return {
                "task_id": "task-lead",
                "repo": "/media/sam/1TB/demo",
                "role": "lead",
                "target_cli": "gemini",
                "status": "assigned",
            }
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(module, "_router_get_json", fake_router_get_json)

    pair = module._fetch_live_session_pair_for_task("http://router", "token", "task-lead")

    assert pair is None


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
