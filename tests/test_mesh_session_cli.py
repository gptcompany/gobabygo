from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "mesh_session_cli.py"
    spec = importlib.util.spec_from_file_location("mesh_session_cli", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_build_session_choices_enriches_router_data(monkeypatch):
    module = _load_module()

    def fake_router_get_json(router_url: str, auth_token: str, path: str):
        if path == "/sessions?state=open&limit=200":
            return {
                "sessions": [
                    {
                        "session_id": "sess-1",
                        "worker_id": "ws-gemini-1",
                        "cli_type": "gemini",
                        "account_profile": "default",
                        "task_id": "task-1",
                        "state": "open",
                        "metadata": {
                            "tmux_session": "mesh-gemini-sam-1234",
                            "working_dir": "/media/sam/1TB/snake-game",
                            "attach_kind": "ssh_tmux",
                            "attach_target": "ssh://sam@192.168.1.111:22?tmux_session=mesh-gemini-sam-1234",
                        },
                        "updated_at": "2026-03-11T14:00:00Z",
                    }
                ]
            }
        if path == "/tasks/task-1":
            return {
                "task_id": "task-1",
                "thread_id": "thread-1",
                "repo": "/media/sam/1TB/snake-game",
                "role": "worker",
                "title": "Implement snake movement",
                "status": "running",
            }
        if path == "/threads/thread-1":
            return {
                "thread_id": "thread-1",
                "name": "snake-game-demo-rerun",
                "status": "active",
            }
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(module, "router_get_json", fake_router_get_json)

    choices = module.build_session_choices(
        "http://router",
        "token",
        provider_users={"gemini": "sam"},
    )

    assert len(choices) == 1
    choice = choices[0]
    assert choice.repo_name == "snake-game"
    assert choice.role == "worker"
    assert choice.thread_name == "snake-game-demo-rerun"
    assert choice.attach_owner == "sam"
    assert choice.tmux_session == "mesh-gemini-sam-1234"


def test_build_session_choices_prefers_repo_and_role_from_session_metadata(monkeypatch):
    module = _load_module()

    def fake_router_get_json(router_url: str, auth_token: str, path: str):
        if path == "/sessions?state=open&limit=200":
            return {
                "sessions": [
                    {
                        "session_id": "sess-1",
                        "worker_id": "ws-gemini-1",
                        "cli_type": "gemini",
                        "account_profile": "default",
                        "task_id": "task-1",
                        "state": "open",
                        "metadata": {
                            "repo": "/media/sam/1TB/snake-game",
                            "role": "lead",
                            "tmux_session": "mesh-gemini-sam-1234",
                        },
                        "updated_at": "2026-03-11T14:00:00Z",
                    }
                ]
            }
        raise module.HTTPError(path, 404, "not found", None, None)

    monkeypatch.setattr(module, "router_get_json", fake_router_get_json)

    choices = module.build_session_choices("http://router", "token", provider_users={})

    assert len(choices) == 1
    choice = choices[0]
    assert choice.repo == "/media/sam/1TB/snake-game"
    assert choice.role == "lead"


def test_build_session_choices_reads_ui_group_id_from_task_payload(monkeypatch):
    module = _load_module()

    def fake_router_get_json(router_url: str, auth_token: str, path: str):
        if path == "/sessions?state=open&limit=200":
            return {
                "sessions": [
                    {
                        "session_id": "sess-1",
                        "worker_id": "ws-gemini-1",
                        "cli_type": "gemini",
                        "account_profile": "default",
                        "task_id": "task-1",
                        "state": "open",
                        "metadata": {
                            "repo": "/media/sam/1TB/snake-game",
                            "tmux_session": "mesh-gemini-sam-1234",
                        },
                        "updated_at": "2026-03-11T14:00:00Z",
                    }
                ]
            }
        if path == "/tasks/task-1":
            return {
                "task_id": "task-1",
                "repo": "/media/sam/1TB/snake-game",
                "role": "lead",
                "status": "running",
                "payload": {"ui_group_id": "snake-ui-1"},
            }
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(module, "router_get_json", fake_router_get_json)

    choices = module.build_session_choices("http://router", "token", provider_users={})

    assert len(choices) == 1
    assert choices[0].ui_group_id == "snake-ui-1"


def test_filter_session_choices_matches_repo_role_and_session():
    module = _load_module()
    choices = [
        module.SessionChoice(
            session_id="sess-alpha-1234",
            worker_id="worker-1",
            cli_type="gemini",
            account_profile="default",
            state="open",
            task_id="task-1",
            task_status="running",
            thread_id="thread-1",
            thread_name="snake-demo",
            thread_status="active",
            repo="/media/sam/1TB/snake-game",
            repo_name="snake-game",
            role="worker",
            title="Implement movement",
            updated_at="2026-03-11T14:00:00Z",
            tmux_session="mesh-gemini-sam-1234",
            attach_kind="ssh_tmux",
            attach_target="ssh://sam@192.168.1.111:22?tmux_session=mesh-gemini-sam-1234",
            attach_owner="sam",
        )
    ]

    assert module.filter_session_choices(choices, "snake-game")[0].session_id == "sess-alpha-1234"
    assert module.filter_session_choices(choices, "worker")[0].session_id == "sess-alpha-1234"
    assert module.filter_session_choices(choices, "alpha-12")[0].session_id == "sess-alpha-1234"


def test_filter_active_session_choices_hides_stale_and_unscoped_entries():
    module = _load_module()
    active = module.SessionChoice(
        session_id="sess-active",
        worker_id="worker-1",
        cli_type="gemini",
        account_profile="default",
        state="open",
        task_id="task-1",
        task_status="running",
        thread_id="thread-1",
        thread_name="snake-demo",
        thread_status="active",
        repo="/media/sam/1TB/snake-game",
        repo_name="snake-game",
        role="worker",
        title="Implement movement",
        updated_at="2026-03-11T14:00:00Z",
        tmux_session="mesh-gemini-sam-1234",
        attach_kind="ssh_tmux",
        attach_target="ssh://sam@192.168.1.111:22?tmux_session=mesh-gemini-sam-1234",
        attach_owner="sam",
    )
    stale = module.SessionChoice(
        session_id="sess-stale",
        worker_id="worker-2",
        cli_type="codex",
        account_profile="default",
        state="open",
        task_id="task-2",
        task_status="failed",
        thread_id="",
        thread_name="",
        thread_status="",
        repo="/media/sam/1TB/rektslug",
        repo_name="rektslug",
        role="",
        title="old smoke",
        updated_at="2026-03-11T14:01:00Z",
        tmux_session="mesh-codex-old-1234",
        attach_kind="",
        attach_target="",
        attach_owner="",
    )
    unrouted = module.SessionChoice(
        session_id="sess-unrouted",
        worker_id="worker-3",
        cli_type="claude",
        account_profile="default",
        state="open",
        task_id="",
        task_status="",
        thread_id="",
        thread_name="",
        thread_status="",
        repo="",
        repo_name="",
        role="",
        title="",
        updated_at="2026-03-11T14:02:00Z",
        tmux_session="mesh-route-1234",
        attach_kind="ssh_tmux",
        attach_target="ssh://sam@192.168.1.111:22?tmux_session=mesh-route-1234",
        attach_owner="",
    )

    filtered = module.filter_active_session_choices([active, stale, unrouted])

    assert [choice.session_id for choice in filtered] == ["sess-active"]


def test_build_attach_spec_prefers_upterm():
    module = _load_module()
    choice = module.SessionChoice(
        session_id="sess-1",
        worker_id="worker-1",
        cli_type="gemini",
        account_profile="default",
        state="open",
        task_id="task-1",
        task_status="running",
        thread_id="thread-1",
        thread_name="snake-demo",
        thread_status="active",
        repo="/media/sam/1TB/snake-game",
        repo_name="snake-game",
        role="worker",
        title="Implement movement",
        updated_at="2026-03-11T14:00:00Z",
        tmux_session="mesh-gemini-sam-1234",
        attach_kind="upterm",
        attach_target="ssh://tok123@upterm.example:2222",
        attach_owner="sam",
    )

    spec = module.build_attach_spec(choice, "sam@192.168.1.111")

    assert spec["mode"] == "upterm"
    assert spec["ssh_target"] == "tok123@upterm.example"
    assert spec["ssh_port"] == "2222"
    assert spec["remote_cmd"] == ""


def test_build_attach_spec_uses_owner_for_ssh_tmux():
    module = _load_module()
    choice = module.SessionChoice(
        session_id="sess-1",
        worker_id="worker-1",
        cli_type="codex",
        account_profile="default",
        state="open",
        task_id="task-1",
        task_status="running",
        thread_id="thread-1",
        thread_name="snake-demo",
        thread_status="active",
        repo="/media/sam/1TB/snake-game",
        repo_name="snake-game",
        role="lead",
        title="Review plan",
        updated_at="2026-03-11T14:00:00Z",
        tmux_session="mesh-codex-mesh-worker-1234",
        attach_kind="ssh_tmux",
        attach_target="ssh://sam@192.168.1.111:22?tmux_session=mesh-codex-mesh-worker-1234",
        attach_owner="mesh-worker",
    )

    spec = module.build_attach_spec(choice, "sam@192.168.1.111")

    assert spec["mode"] == "ssh_tmux"
    assert spec["ssh_target"] == "sam@192.168.1.111"
    assert "sudo -u mesh-worker tmux attach -t mesh-codex-mesh-worker-1234" in spec["remote_cmd"]


def test_select_choice_uses_numeric_prompt():
    module = _load_module()
    choices = [
        module.SessionChoice(
            session_id="sess-1",
            worker_id="worker-1",
            cli_type="gemini",
            account_profile="default",
            state="open",
            task_id="task-1",
            task_status="running",
            thread_id="thread-1",
            thread_name="snake-demo",
            thread_status="active",
            repo="/media/sam/1TB/snake-game",
            repo_name="snake-game",
            role="worker",
            title="Implement movement",
            updated_at="2026-03-11T14:00:00Z",
            tmux_session="mesh-gemini-sam-1111",
            attach_kind="ssh_tmux",
            attach_target="ssh://sam@192.168.1.111:22?tmux_session=mesh-gemini-sam-1111",
            attach_owner="sam",
        ),
        module.SessionChoice(
            session_id="sess-2",
            worker_id="worker-2",
            cli_type="codex",
            account_profile="default",
            state="open",
            task_id="task-2",
            task_status="running",
            thread_id="thread-2",
            thread_name="snake-review",
            thread_status="active",
            repo="/media/sam/1TB/snake-game",
            repo_name="snake-game",
            role="lead",
            title="Review movement",
            updated_at="2026-03-11T14:01:00Z",
            tmux_session="mesh-codex-mesh-worker-2222",
            attach_kind="ssh_tmux",
            attach_target="ssh://sam@192.168.1.111:22?tmux_session=mesh-codex-mesh-worker-2222",
            attach_owner="mesh-worker",
        ),
    ]

    selected = module.select_choice(choices, prompt_fn=lambda _: "2", interactive=True)

    assert selected.session_id == "sess-2"


def test_select_choice_prefers_questionary_when_available(monkeypatch):
    module = _load_module()
    choice = module.SessionChoice(
        session_id="sess-1",
        worker_id="worker-1",
        cli_type="gemini",
        account_profile="default",
        state="open",
        task_id="task-1",
        task_status="running",
        thread_id="thread-1",
        thread_name="snake-demo",
        thread_status="active",
        repo="/media/sam/1TB/snake-game",
        repo_name="snake-game",
        role="worker",
        title="Implement movement",
        updated_at="2026-03-11T14:00:00Z",
        tmux_session="mesh-gemini-sam-1111",
        attach_kind="ssh_tmux",
        attach_target="ssh://sam@192.168.1.111:22?tmux_session=mesh-gemini-sam-1111",
        attach_owner="sam",
    )
    monkeypatch.setattr(module, "_questionary_select_choice", lambda choices: choices[0])

    selected = module.select_choice([choice, choice], query="snake-game", interactive=True)

    assert selected.session_id == "sess-1"


def test_detect_repo_context_uses_git_root(tmp_path):
    module = _load_module()
    repo_root = tmp_path / "repo"
    nested = repo_root / "src"
    nested.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=repo_root, capture_output=True, check=True)

    repo_path, repo_name = module.detect_repo_context(str(nested))

    assert repo_path == str(repo_root)
    assert repo_name == "repo"


def test_emit_payload_can_write_to_file(tmp_path):
    module = _load_module()
    output_path = tmp_path / "payload.json"

    module._emit_payload({"ok": True}, str(output_path))

    assert json.loads(output_path.read_text(encoding="utf-8")) == {"ok": True}


def test_resolve_role_choice_matches_repo_and_ui_group():
    module = _load_module()
    choice = module.SessionChoice(
        session_id="sess-1",
        worker_id="worker-1",
        cli_type="gemini",
        account_profile="default",
        state="open",
        task_id="task-1",
        task_status="running",
        thread_id="thread-1",
        thread_name="snake-demo",
        thread_status="active",
        repo="/media/sam/1TB/snake-game",
        repo_name="snake-game",
        role="lead",
        title="Review movement",
        updated_at="2026-03-11T14:00:00Z",
        tmux_session="mesh-gemini-sam-1111",
        attach_kind="ssh_tmux",
        attach_target="ssh://sam@192.168.1.111:22?tmux_session=mesh-gemini-sam-1111",
        attach_owner="sam",
        ui_group_id="snake-ui-1",
    )

    selected = module.resolve_role_choice(
        [choice],
        role="lead",
        repo_path="/Users/sam/snake-game",
        repo_name="snake-game",
        ui_group_id="snake-ui-1",
    )

    assert selected.session_id == "sess-1"


def test_resolve_active_ui_group_id_prefers_live_router_group(monkeypatch):
    module = _load_module()
    monkeypatch.delenv("MESH_UI_GROUP_ID", raising=False)
    monkeypatch.setattr(module, "_read_ui_group_cache", lambda repo_name, cache_dir=None: "")
    choice = module.SessionChoice(
        session_id="sess-1",
        worker_id="worker-1",
        cli_type="gemini",
        account_profile="default",
        state="open",
        task_id="task-1",
        task_status="running",
        thread_id="thread-1",
        thread_name="snake-demo",
        thread_status="active",
        repo="/media/sam/1TB/snake-game",
        repo_name="snake-game",
        role="lead",
        title="Review movement",
        updated_at="2026-03-11T14:00:00Z",
        tmux_session="mesh-gemini-sam-1111",
        attach_kind="ssh_tmux",
        attach_target="ssh://sam@192.168.1.111:22?tmux_session=mesh-gemini-sam-1111",
        attach_owner="sam",
        ui_group_id="snake-ui-1",
    )

    assert (
        module.resolve_active_ui_group_id(
            "snake-game",
            repo_path="/Users/sam/snake-game",
            choices=[choice],
        )
        == "snake-ui-1"
    )


def test_resolve_active_ui_group_id_rejects_multiple_live_groups(monkeypatch):
    module = _load_module()
    monkeypatch.delenv("MESH_UI_GROUP_ID", raising=False)
    monkeypatch.setattr(module, "_read_ui_group_cache", lambda repo_name, cache_dir=None: "")
    choices = [
        module.SessionChoice(
            session_id="sess-1",
            worker_id="worker-1",
            cli_type="gemini",
            account_profile="default",
            state="open",
            task_id="task-1",
            task_status="running",
            thread_id="thread-1",
            thread_name="snake-demo",
            thread_status="active",
            repo="/media/sam/1TB/snake-game",
            repo_name="snake-game",
            role="lead",
            title="Review movement",
            updated_at="2026-03-11T14:00:00Z",
            tmux_session="mesh-gemini-sam-1111",
            attach_kind="ssh_tmux",
            attach_target="ssh://sam@192.168.1.111:22?tmux_session=mesh-gemini-sam-1111",
            attach_owner="sam",
            ui_group_id="snake-ui-1",
        ),
        module.SessionChoice(
            session_id="sess-2",
            worker_id="worker-2",
            cli_type="codex",
            account_profile="default",
            state="open",
            task_id="task-2",
            task_status="running",
            thread_id="thread-2",
            thread_name="snake-review",
            thread_status="active",
            repo="/media/sam/1TB/snake-game",
            repo_name="snake-game",
            role="worker-codex",
            title="Review movement",
            updated_at="2026-03-11T14:01:00Z",
            tmux_session="mesh-codex-sam-2222",
            attach_kind="ssh_tmux",
            attach_target="ssh://sam@192.168.1.111:22?tmux_session=mesh-codex-sam-2222",
            attach_owner="sam",
            ui_group_id="snake-ui-2",
        ),
    ]

    try:
        module.resolve_active_ui_group_id(
            "snake-game",
            repo_path="/Users/sam/snake-game",
            choices=choices,
        )
    except ValueError as exc:
        assert "multiple live ui_group_id" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_resolve_role_choice_errors_on_ambiguity():
    module = _load_module()
    choices = [
        module.SessionChoice(
            session_id="sess-1",
            worker_id="worker-1",
            cli_type="gemini",
            account_profile="default",
            state="open",
            task_id="task-1",
            task_status="running",
            thread_id="thread-1",
            thread_name="snake-demo",
            thread_status="active",
            repo="/media/sam/1TB/snake-game",
            repo_name="snake-game",
            role="lead",
            title="Review movement",
            updated_at="2026-03-11T14:00:00Z",
            tmux_session="mesh-gemini-sam-1111",
            attach_kind="ssh_tmux",
            attach_target="ssh://sam@192.168.1.111:22?tmux_session=mesh-gemini-sam-1111",
            attach_owner="sam",
            ui_group_id="snake-ui-1",
        ),
        module.SessionChoice(
            session_id="sess-2",
            worker_id="worker-2",
            cli_type="codex",
            account_profile="default",
            state="open",
            task_id="task-2",
            task_status="running",
            thread_id="thread-2",
            thread_name="snake-review",
            thread_status="active",
            repo="/media/sam/1TB/snake-game",
            repo_name="snake-game",
            role="lead",
            title="Review movement",
            updated_at="2026-03-11T14:01:00Z",
            tmux_session="mesh-codex-sam-2222",
            attach_kind="ssh_tmux",
            attach_target="ssh://sam@192.168.1.111:22?tmux_session=mesh-codex-sam-2222",
            attach_owner="sam",
            ui_group_id="snake-ui-1",
        ),
    ]

    try:
        module.resolve_role_choice(
            choices,
            role="lead",
            repo_path="/Users/sam/snake-game",
            repo_name="snake-game",
            ui_group_id="snake-ui-1",
        )
    except ValueError as exc:
        assert "ambiguous live sessions" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_main_send_posts_router_message(monkeypatch, capsys):
    module = _load_module()
    selected = module.SessionChoice(
        session_id="sess-1",
        worker_id="worker-1",
        cli_type="gemini",
        account_profile="default",
        state="open",
        task_id="task-1",
        task_status="running",
        thread_id="thread-1",
        thread_name="snake-demo",
        thread_status="active",
        repo="/media/sam/1TB/snake-game",
        repo_name="snake-game",
        role="lead",
        title="Review movement",
        updated_at="2026-03-11T14:00:00Z",
        tmux_session="mesh-gemini-sam-1111",
        attach_kind="ssh_tmux",
        attach_target="ssh://sam@192.168.1.111:22?tmux_session=mesh-gemini-sam-1111",
        attach_owner="sam",
        ui_group_id="snake-ui-1",
    )
    posted: list[tuple[str, dict[str, object]]] = []

    monkeypatch.setattr(module, "load_router_env", lambda: ("http://router", "token"))
    monkeypatch.setattr(module, "build_session_choices", lambda *args, **kwargs: [selected])
    monkeypatch.setattr(module, "detect_repo_context", lambda cwd=None: ("/Users/sam/snake-game", "snake-game"))
    monkeypatch.setattr(
        module,
        "resolve_active_ui_group_id",
        lambda repo_name, *, repo_path, choices: "snake-ui-1",
    )
    monkeypatch.setattr(
        module,
        "router_post_json",
        lambda router_url, auth_token, path, payload: posted.append((path, payload)) or {"status": "accepted"},
    )
    monkeypatch.setattr(sys, "argv", ["mesh_session_cli.py", "send", "lead", "hello", "world"])

    assert module.main() == 0
    assert posted == [
        (
            "/sessions/send",
            {
                "session_id": "sess-1",
                "direction": "in",
                "role": "operator",
                "content": "hello world",
                "metadata": {"ui_group_id": "snake-ui-1", "target_role": "lead"},
            },
        )
    ]
    assert "[mesh send] role=lead session=sess-1" in capsys.readouterr().out


def test_main_enter_and_interrupt_dispatch_controls(monkeypatch):
    module = _load_module()
    selected = module.SessionChoice(
        session_id="sess-1",
        worker_id="worker-1",
        cli_type="gemini",
        account_profile="default",
        state="open",
        task_id="task-1",
        task_status="running",
        thread_id="thread-1",
        thread_name="snake-demo",
        thread_status="active",
        repo="/media/sam/1TB/snake-game",
        repo_name="snake-game",
        role="lead",
        title="Review movement",
        updated_at="2026-03-11T14:00:00Z",
        tmux_session="mesh-gemini-sam-1111",
        attach_kind="ssh_tmux",
        attach_target="ssh://sam@192.168.1.111:22?tmux_session=mesh-gemini-sam-1111",
        attach_owner="sam",
        ui_group_id="snake-ui-1",
    )
    posted: list[tuple[str, dict[str, object]]] = []

    monkeypatch.setattr(module, "load_router_env", lambda: ("http://router", "token"))
    monkeypatch.setattr(module, "build_session_choices", lambda *args, **kwargs: [selected])
    monkeypatch.setattr(module, "detect_repo_context", lambda cwd=None: ("/Users/sam/snake-game", "snake-game"))
    monkeypatch.setattr(
        module,
        "resolve_active_ui_group_id",
        lambda repo_name, *, repo_path, choices: "snake-ui-1",
    )
    monkeypatch.setattr(
        module,
        "router_post_json",
        lambda router_url, auth_token, path, payload: posted.append((path, payload)) or {"status": "accepted"},
    )

    monkeypatch.setattr(sys, "argv", ["mesh_session_cli.py", "enter", "lead"])
    assert module.main() == 0
    monkeypatch.setattr(sys, "argv", ["mesh_session_cli.py", "interrupt", "lead"])
    assert module.main() == 0

    assert posted == [
        ("/sessions/send-key", {"session_id": "sess-1", "key": "Enter", "repeat": 1}),
        ("/sessions/signal", {"session_id": "sess-1", "signal": "interrupt"}),
    ]


def test_parse_send_accepts_ui_group_id_after_role(monkeypatch):
    module = _load_module()
    monkeypatch.setattr(
        sys,
        "argv",
        ["mesh_session_cli.py", "send", "lead", "--ui-group-id", "snake-ui-9", "hello", "world"],
    )

    args = module._parse_args()

    assert args.cmd == "send"
    assert args.role == "lead"
    assert args.ui_group_id == "snake-ui-9"
    assert args.message == ["hello", "world"]


def test_main_summary_prints_latest_completion_summary(monkeypatch, capsys):
    module = _load_module()
    selected = module.SessionChoice(
        session_id="sess-1",
        worker_id="worker-1",
        cli_type="gemini",
        account_profile="default",
        state="closed",
        task_id="task-1",
        task_status="completed",
        thread_id="thread-1",
        thread_name="snake-demo",
        thread_status="completed",
        repo="/media/sam/1TB/snake-game",
        repo_name="snake-game",
        role="lead",
        title="Review movement",
        updated_at="2026-03-11T14:00:00Z",
        tmux_session="mesh-gemini-sam-1111",
        attach_kind="ssh_tmux",
        attach_target="ssh://sam@192.168.1.111:22?tmux_session=mesh-gemini-sam-1111",
        attach_owner="sam",
        ui_group_id="snake-ui-1",
    )

    def fake_router_get_json(router_url: str, auth_token: str, path: str):
        if path == "/sessions/messages?session_id=sess-1&after_seq=0&limit=200":
            return {
                "messages": [
                    {
                        "seq": 5,
                        "content": "lead completed.",
                        "metadata": {
                            "type": "completion_summary",
                            "role": "lead",
                            "ui_group_id": "snake-ui-1",
                            "status": "completed",
                            "summary_text": "lead completed.",
                            "target_roles": ["president", "boss"],
                        },
                    }
                ]
            }
        if path == "/sessions/messages?session_id=sess-1&after_seq=5&limit=200":
            return {"messages": []}
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(module, "load_router_env", lambda: ("http://router", "token"))
    monkeypatch.setattr(module, "build_session_choices", lambda *args, **kwargs: [selected])
    monkeypatch.setattr(module, "router_get_json", fake_router_get_json)
    monkeypatch.setattr(module, "detect_repo_context", lambda cwd=None: ("/Users/sam/snake-game", "snake-game"))
    monkeypatch.setattr(
        module,
        "resolve_active_ui_group_id",
        lambda repo_name, *, repo_path, choices: "snake-ui-1",
    )
    monkeypatch.setattr(sys, "argv", ["mesh_session_cli.py", "summary", "lead"])

    assert module.main() == 0
    output = capsys.readouterr().out
    assert "[mesh summary] role=lead session=sess-1" in output
    assert "lead completed." in output


def test_main_summary_filters_by_target_role(monkeypatch, tmp_path):
    module = _load_module()
    selected = module.SessionChoice(
        session_id="sess-1",
        worker_id="worker-1",
        cli_type="gemini",
        account_profile="default",
        state="closed",
        task_id="task-1",
        task_status="completed",
        thread_id="thread-1",
        thread_name="snake-demo",
        thread_status="completed",
        repo="/media/sam/1TB/snake-game",
        repo_name="snake-game",
        role="lead",
        title="Review movement",
        updated_at="2026-03-11T14:00:00Z",
        tmux_session="mesh-gemini-sam-1111",
        attach_kind="ssh_tmux",
        attach_target="ssh://sam@192.168.1.111:22?tmux_session=mesh-gemini-sam-1111",
        attach_owner="sam",
        ui_group_id="snake-ui-1",
    )
    output_path = tmp_path / "summary.json"

    def fake_router_get_json(router_url: str, auth_token: str, path: str):
        if path == "/sessions/messages?session_id=sess-1&after_seq=0&limit=200":
            return {
                "messages": [
                    {
                        "seq": 4,
                        "content": "ignored",
                        "metadata": {
                            "type": "completion_summary",
                            "role": "lead",
                            "ui_group_id": "snake-ui-1",
                            "status": "completed",
                            "summary_text": "ignored",
                            "target_roles": ["boss"],
                        },
                    },
                    {
                        "seq": 5,
                        "content": "president update",
                        "metadata": {
                            "type": "completion_summary",
                            "role": "lead",
                            "ui_group_id": "snake-ui-1",
                            "status": "completed",
                            "summary_text": "president update",
                            "target_role": "president",
                            "target_roles": ["president", "boss"],
                        },
                    },
                    {
                        "seq": 6,
                        "content": "routed from lead",
                        "metadata": {
                            "type": "completion_summary",
                            "role": "lead",
                            "ui_group_id": "snake-ui-1",
                            "status": "completed",
                            "summary_text": "routed from lead",
                            "target_role": "president",
                            "target_roles": ["president", "boss"],
                            "source_role": "lead",
                        },
                    },
                ]
            }
        if path == "/sessions/messages?session_id=sess-1&after_seq=6&limit=200":
            return {"messages": []}
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(module, "load_router_env", lambda: ("http://router", "token"))
    monkeypatch.setattr(module, "build_session_choices", lambda *args, **kwargs: [selected])
    monkeypatch.setattr(module, "router_get_json", fake_router_get_json)
    monkeypatch.setattr(module, "detect_repo_context", lambda cwd=None: ("/Users/sam/snake-game", "snake-game"))
    monkeypatch.setattr(
        module,
        "resolve_active_ui_group_id",
        lambda repo_name, *, repo_path, choices: "snake-ui-1",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["mesh_session_cli.py", "summary", "lead", "--target", "president", "--output", str(output_path)],
    )

    assert module.main() == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["summary"]["target_role"] == "president"
    assert payload["content"] == "president update"


def test_list_completion_summaries_paginates(monkeypatch):
    module = _load_module()

    def fake_router_get_json(router_url: str, auth_token: str, path: str):
        if path == "/sessions/messages?session_id=sess-1&after_seq=0&limit=200":
            return {"messages": [{"seq": index + 1, "content": f"msg-{index}", "metadata": {}} for index in range(200)]}
        if path == "/sessions/messages?session_id=sess-1&after_seq=200&limit=200":
            return {
                "messages": [
                    {
                        "seq": 201,
                        "content": "done",
                        "metadata": {
                            "type": "completion_summary",
                            "role": "lead",
                            "ui_group_id": "snake-ui-1",
                            "status": "completed",
                            "summary_text": "done",
                            "target_roles": ["president", "boss"],
                        },
                    }
                ]
            }
        if path == "/sessions/messages?session_id=sess-1&after_seq=201&limit=200":
            return {"messages": []}
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(module, "router_get_json", fake_router_get_json)

    summaries = module._list_completion_summaries("http://router", "token", "sess-1")

    assert len(summaries) == 1
    assert summaries[0]["seq"] == 201


def test_resolve_role_summary_ignores_routed_summary_for_other_role(monkeypatch):
    module = _load_module()
    choice = module.SessionChoice(
        session_id="sess-president",
        worker_id="worker-1",
        cli_type="gemini",
        account_profile="default",
        state="closed",
        task_id="task-1",
        task_status="completed",
        thread_id="thread-1",
        thread_name="snake-demo",
        thread_status="completed",
        repo="/media/sam/1TB/snake-game",
        repo_name="snake-game",
        role="president",
        title="Review movement",
        updated_at="2026-03-11T14:00:00Z",
        tmux_session="mesh-gemini-sam-1111",
        attach_kind="ssh_tmux",
        attach_target="ssh://sam@192.168.1.111:22?tmux_session=mesh-gemini-sam-1111",
        attach_owner="sam",
        ui_group_id="snake-ui-1",
    )

    def fake_router_get_json(router_url: str, auth_token: str, path: str):
        if path == "/sessions/messages?session_id=sess-president&after_seq=0&limit=200":
            return {
                "messages": [
                    {
                        "seq": 10,
                        "content": "lead routed",
                        "metadata": {
                            "type": "completion_summary",
                            "role": "lead",
                            "ui_group_id": "snake-ui-1",
                            "status": "completed",
                            "summary_text": "lead routed",
                            "target_role": "president",
                            "target_roles": ["president", "boss"],
                        },
                    }
                ]
            }
        if path == "/sessions/messages?session_id=sess-president&after_seq=10&limit=200":
            return {"messages": []}
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(module, "router_get_json", fake_router_get_json)

    try:
        module.resolve_role_summary(
            "http://router",
            "token",
            [choice],
            role="president",
            repo_path="/Users/sam/snake-game",
            repo_name="snake-game",
            ui_group_id="snake-ui-1",
        )
    except ValueError as exc:
        assert "no completion summary" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_main_summary_reports_router_fetch_errors(monkeypatch, capsys):
    module = _load_module()
    selected = module.SessionChoice(
        session_id="sess-1",
        worker_id="worker-1",
        cli_type="gemini",
        account_profile="default",
        state="closed",
        task_id="task-1",
        task_status="completed",
        thread_id="thread-1",
        thread_name="snake-demo",
        thread_status="completed",
        repo="/media/sam/1TB/snake-game",
        repo_name="snake-game",
        role="lead",
        title="Review movement",
        updated_at="2026-03-11T14:00:00Z",
        tmux_session="mesh-gemini-sam-1111",
        attach_kind="ssh_tmux",
        attach_target="ssh://sam@192.168.1.111:22?tmux_session=mesh-gemini-sam-1111",
        attach_owner="sam",
        ui_group_id="snake-ui-1",
    )

    def fake_router_get_json(router_url: str, auth_token: str, path: str):
        if path == "/sessions/messages?session_id=sess-1&after_seq=0&limit=200":
            raise module.URLError("boom")
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(module, "load_router_env", lambda: ("http://router", "token"))
    monkeypatch.setattr(module, "build_session_choices", lambda *args, **kwargs: [selected])
    monkeypatch.setattr(module, "router_get_json", fake_router_get_json)
    monkeypatch.setattr(module, "detect_repo_context", lambda cwd=None: ("/Users/sam/snake-game", "snake-game"))
    monkeypatch.setattr(
        module,
        "resolve_active_ui_group_id",
        lambda repo_name, *, repo_path, choices: "snake-ui-1",
    )
    monkeypatch.setattr(sys, "argv", ["mesh_session_cli.py", "summary", "lead"])

    assert module.main() == 1
    assert "cannot connect to mesh router" in capsys.readouterr().err


def test_main_close_signals_sessions_and_clears_cache(monkeypatch, tmp_path, capsys):
    module = _load_module()
    cache_dir = tmp_path / "ui-cache"
    cache_dir.mkdir()
    cache_file = cache_dir / "snake-game.json"
    cache_file.write_text(
        json.dumps({"repo_name": "snake-game", "ui_group_id": "snake-ui-1"}) + "\n",
        encoding="utf-8",
    )
    choices = [
        module.SessionChoice(
            session_id="sess-1",
            worker_id="worker-1",
            cli_type="gemini",
            account_profile="default",
            state="open",
            task_id="task-1",
            task_status="running",
            thread_id="thread-1",
            thread_name="snake-demo",
            thread_status="active",
            repo="/media/sam/1TB/snake-game",
            repo_name="snake-game",
            role="lead",
            title="Review movement",
            updated_at="2026-03-11T14:00:00Z",
            tmux_session="mesh-gemini-sam-1111",
            attach_kind="ssh_tmux",
            attach_target="ssh://sam@192.168.1.111:22?tmux_session=mesh-gemini-sam-1111",
            attach_owner="sam",
            ui_group_id="snake-ui-1",
        ),
        module.SessionChoice(
            session_id="sess-2",
            worker_id="worker-2",
            cli_type="codex",
            account_profile="default",
            state="open",
            task_id="task-2",
            task_status="running",
            thread_id="thread-2",
            thread_name="snake-review",
            thread_status="active",
            repo="/media/sam/1TB/snake-game",
            repo_name="snake-game",
            role="president",
            title="Review movement",
            updated_at="2026-03-11T14:01:00Z",
            tmux_session="mesh-codex-sam-2222",
            attach_kind="ssh_tmux",
            attach_target="ssh://sam@192.168.1.111:22?tmux_session=mesh-codex-sam-2222",
            attach_owner="sam",
            ui_group_id="snake-ui-1",
        ),
    ]
    posted: list[tuple[str, dict[str, object]]] = []

    monkeypatch.setenv("MESH_UI_GROUP_CACHE_DIR", str(cache_dir))
    monkeypatch.setattr(module, "load_router_env", lambda: ("http://router", "token"))
    monkeypatch.setattr(module, "build_session_choices", lambda *args, **kwargs: choices)
    monkeypatch.setattr(module, "detect_repo_context", lambda cwd=None: ("/Users/sam/snake-game", "snake-game"))
    monkeypatch.setattr(
        module,
        "resolve_active_ui_group_id",
        lambda repo_name, *, repo_path, choices: "snake-ui-1",
    )
    monkeypatch.setattr(
        module,
        "router_post_json",
        lambda router_url, auth_token, path, payload: posted.append((path, payload)) or {"status": "accepted"},
    )
    monkeypatch.setattr(sys, "argv", ["mesh_session_cli.py", "close"])

    assert module.main() == 0
    assert posted == [
        ("/sessions/signal", {"session_id": "sess-1", "signal": "terminate"}),
        ("/sessions/signal", {"session_id": "sess-2", "signal": "terminate"}),
    ]
    assert not cache_file.exists()
    assert "signaled=2" in capsys.readouterr().out


def test_main_close_keeps_cache_when_failures_occur(monkeypatch, tmp_path, capsys):
    module = _load_module()
    cache_dir = tmp_path / "ui-cache"
    cache_dir.mkdir()
    cache_file = cache_dir / "snake-game.json"
    cache_file.write_text(
        json.dumps({"repo_name": "snake-game", "ui_group_id": "snake-ui-1"}) + "\n",
        encoding="utf-8",
    )
    choices = [
        module.SessionChoice(
            session_id="sess-1",
            worker_id="worker-1",
            cli_type="gemini",
            account_profile="default",
            state="open",
            task_id="task-1",
            task_status="running",
            thread_id="thread-1",
            thread_name="snake-demo",
            thread_status="active",
            repo="/media/sam/1TB/snake-game",
            repo_name="snake-game",
            role="lead",
            title="Review movement",
            updated_at="2026-03-11T14:00:00Z",
            tmux_session="mesh-gemini-sam-1111",
            attach_kind="ssh_tmux",
            attach_target="ssh://sam@192.168.1.111:22?tmux_session=mesh-gemini-sam-1111",
            attach_owner="sam",
            ui_group_id="snake-ui-1",
        )
    ]

    def fake_router_post_json(router_url: str, auth_token: str, path: str, payload: dict[str, object]):
        raise module.URLError("boom")

    monkeypatch.setenv("MESH_UI_GROUP_CACHE_DIR", str(cache_dir))
    monkeypatch.setattr(module, "load_router_env", lambda: ("http://router", "token"))
    monkeypatch.setattr(module, "build_session_choices", lambda *args, **kwargs: choices)
    monkeypatch.setattr(module, "detect_repo_context", lambda cwd=None: ("/Users/sam/snake-game", "snake-game"))
    monkeypatch.setattr(
        module,
        "resolve_active_ui_group_id",
        lambda repo_name, *, repo_path, choices: "snake-ui-1",
    )
    monkeypatch.setattr(module, "router_post_json", fake_router_post_json)
    monkeypatch.setattr(sys, "argv", ["mesh_session_cli.py", "close"])

    assert module.main() == 1
    assert cache_file.exists()
    assert "Failures:" in capsys.readouterr().err
