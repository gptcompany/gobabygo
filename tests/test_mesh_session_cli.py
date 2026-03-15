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
