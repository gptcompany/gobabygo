from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys
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


def test_mesh_ui_role_shell_marks_repo_safe_for_git():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "mesh_ui_role_shell.sh"
    content = script_path.read_text(encoding="utf-8")

    assert 'git config --global --add safe.directory "$target_dir"' in content


def test_mesh_ui_role_shell_sets_role_label_and_badge():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "mesh_ui_role_shell.sh"
    content = script_path.read_text(encoding="utf-8")

    assert "printf '\\033]0;%s\\007' \"$label\"" in content
    assert "SetBadgeFormat" in content


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


def test_ui_group_cache_round_trip(tmp_path):
    module = _load_module()

    path = module._write_ui_group_cache(
        "snake-game",
        "snake-game-ui-20260314T120000Z",
        cache_dir=tmp_path,
    )

    assert path == tmp_path / "snake-game.json"
    assert module._read_ui_group_cache("snake-game", cache_dir=tmp_path) == {
        "repo_name": "snake-game",
        "ui_group_id": "snake-game-ui-20260314T120000Z",
    }

    module._clear_ui_group_cache("snake-game", cache_dir=tmp_path)
    assert module._read_ui_group_cache("snake-game", cache_dir=tmp_path) is None


def test_resolve_active_ui_group_id_reuses_live_cached_group(tmp_path, monkeypatch):
    module = _load_module()
    module._write_ui_group_cache(
        "snake-game",
        "snake-game-ui-20260314T120000Z",
        cache_dir=tmp_path,
    )

    monkeypatch.setattr(module, "_router_has_live_ui_group", lambda *args, **kwargs: True)

    ui_group_id = module._resolve_active_ui_group_id(
        "snake-game",
        router_url="http://router",
        auth_token="token",
        cache_dir=tmp_path,
        timestamp="20260315T130000Z",
    )

    assert ui_group_id == "snake-game-ui-20260314T120000Z"
    assert module._read_ui_group_cache("snake-game", cache_dir=tmp_path) == {
        "repo_name": "snake-game",
        "ui_group_id": "snake-game-ui-20260314T120000Z",
    }


def test_resolve_active_ui_group_id_replaces_stale_cached_group(tmp_path, monkeypatch):
    module = _load_module()
    module._write_ui_group_cache(
        "snake-game",
        "snake-game-ui-20260314T120000Z",
        cache_dir=tmp_path,
    )

    monkeypatch.setattr(module, "_router_has_live_ui_group", lambda *args, **kwargs: False)

    ui_group_id = module._resolve_active_ui_group_id(
        "snake-game",
        router_url="http://router",
        auth_token="token",
        cache_dir=tmp_path,
        timestamp="20260315T130000Z",
    )

    assert ui_group_id == "snake-game-ui-20260315T130000Z"
    assert module._read_ui_group_cache("snake-game", cache_dir=tmp_path) == {
        "repo_name": "snake-game",
        "ui_group_id": "snake-game-ui-20260315T130000Z",
    }


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

    assert task_info == {"role": "lead", "task_id": "task-lead-1", "target_cli": "gemini"}
    assert len(calls) == 1
    _, _, path, payload = calls[0]
    assert path == "/tasks"
    assert payload["repo"] == "/media/sam/1TB/demo"
    assert payload["role"] == "lead"
    assert payload["target_cli"] == "gemini"
    assert payload["execution_mode"] == "session"
    assert payload["payload"]["ui_role_session"] is True
    assert payload["payload"]["ui_group_id"] == "demo-ui-1"
    assert payload["payload"]["working_dir"] == "/media/sam/1TB/demo"
    assert "prompt" not in payload["payload"]


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
        return {"role": role, "task_id": task_ids[role], "target_cli": "gemini" if role == "lead" else "codex"}

    fetch_calls = {"count": 0}

    def fake_fetch(router_url: str, auth_token: str):
        fetch_calls["count"] += 1
        return [
            (
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
            ),
            (
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
            ),
        ]

    monkeypatch.setattr(module, "_create_ui_role_task", fake_create)
    monkeypatch.setattr(module, "_fetch_live_session_pairs", fake_fetch)
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
        lambda router_url, auth_token, cfg_value, role: {"role": role, "task_id": "task-lead-1", "target_cli": "gemini"},
    )
    monkeypatch.setattr(module, "_fetch_live_session_pairs", lambda router_url, auth_token: [])
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
