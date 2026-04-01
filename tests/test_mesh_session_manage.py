from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "mesh_session_manage.py"
    sys.path.insert(0, str(script_path.parent))
    spec = importlib.util.spec_from_file_location("mesh_session_manage", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _choice(module, *, role: str = "boss", session_id: str = "sess-1234567890ab"):
    return module.build_session_choices.__globals__["SessionChoice"](
        session_id=session_id,
        worker_id="ws-gemini-1",
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
        role=role,
        title=f"mesh ui {role} snake-game",
        updated_at="2026-04-01T20:00:00Z",
        tmux_session="mesh-gemini-sam-1234",
        attach_kind="ssh_tmux",
        attach_target="ssh://sam@192.168.1.111:22?tmux_session=mesh-gemini-sam-1234",
        attach_owner="sam",
        ui_group_id="snake-ui-1",
    )


def test_select_action_prefers_questionary(monkeypatch):
    module = _load_module()
    target = module.ManageTarget(target_kind="layout", repo="repo", repo_name="repo", ui_group_id="group")
    actions = module.available_actions(target)
    monkeypatch.setattr(module, "_questionary_select_action", lambda actions: actions[1])

    selected = module.select_action(actions, interactive=True)

    assert selected.key == "kill"


def test_build_manage_targets_creates_layout_and_panels():
    module = _load_module()
    boss = _choice(module, role="boss", session_id="sess-boss-123456")
    president = _choice(module, role="president", session_id="sess-pres-1234")

    targets = module.build_manage_targets([boss, president])

    assert [target.target_kind for target in targets] == ["layout", "session", "session"]
    assert targets[0].child_roles == ("boss", "president")
    assert targets[1].role == "boss"
    assert targets[2].role == "president"


def test_main_attach_outputs_payload(monkeypatch):
    module = _load_module()
    choice = _choice(module)
    target = module._target_from_choice(choice)
    monkeypatch.setattr(module.sys, "argv", ["mesh_session_manage.py", "--action", "attach"])
    monkeypatch.setattr(module, "load_router_env", lambda: ("http://router", "token"))
    monkeypatch.setattr(module, "build_session_choices", lambda *args, **kwargs: [choice])
    monkeypatch.setattr(module, "select_target", lambda *args, **kwargs: target)
    monkeypatch.setattr(module, "select_action", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("select_action should not run")))
    monkeypatch.setattr(module.sys, "stdin", io.StringIO())
    monkeypatch.setattr(module.sys.stdin, "isatty", lambda: False)
    out = io.StringIO()
    err = io.StringIO()
    monkeypatch.setattr(module.sys, "stdout", out)
    monkeypatch.setattr(module.sys, "stderr", err)

    rc = module.main()

    assert rc == 0
    payload = json.loads(out.getvalue())
    assert payload["action"] == "attach"
    assert payload["selection"]["role"] == "boss"
    assert payload["attach"]["mode"] == "ssh_tmux"
    assert err.getvalue() == ""


def test_main_kill_outputs_payload_and_calls_router(monkeypatch):
    module = _load_module()
    choice = _choice(module)
    target = module._target_from_choice(choice)
    calls = []

    def fake_post(router_url: str, auth_token: str, path: str, payload: dict):
        calls.append((path, payload))
        return {}

    monkeypatch.setattr(module.sys, "argv", ["mesh_session_manage.py"])
    monkeypatch.setattr(module, "load_router_env", lambda: ("http://router", "token"))
    monkeypatch.setattr(module, "build_session_choices", lambda *args, **kwargs: [choice])
    monkeypatch.setattr(module, "select_target", lambda *args, **kwargs: target)
    monkeypatch.setattr(module, "select_action", lambda *args, **kwargs: module.ManageAction("kill", "Kill", ""))
    monkeypatch.setattr(module, "router_post_json", fake_post)
    monkeypatch.setattr(module.sys, "stdin", io.StringIO())
    monkeypatch.setattr(module.sys.stdin, "isatty", lambda: False)
    out = io.StringIO()
    err = io.StringIO()
    monkeypatch.setattr(module.sys, "stdout", out)
    monkeypatch.setattr(module.sys, "stderr", err)

    rc = module.main()

    assert rc == 0
    payload = json.loads(out.getvalue())
    assert payload["action"] == "kill"
    assert payload["result"]["state"] == "closed"
    assert calls == [
        ("/sessions/signal", {"session_id": choice.session_id, "signal": "terminate"}),
        ("/sessions/close", {"session_id": choice.session_id, "state": "closed"}),
    ]
    assert err.getvalue() == ""


def test_main_quit_outputs_selection(monkeypatch):
    module = _load_module()
    choice = _choice(module)
    target = module._target_from_choice(choice)
    monkeypatch.setattr(module.sys, "argv", ["mesh_session_manage.py"])
    monkeypatch.setattr(module, "load_router_env", lambda: ("http://router", "token"))
    monkeypatch.setattr(module, "build_session_choices", lambda *args, **kwargs: [choice])
    monkeypatch.setattr(module, "select_target", lambda *args, **kwargs: target)
    monkeypatch.setattr(module, "select_action", lambda *args, **kwargs: module.ManageAction("quit", "Quit", ""))
    monkeypatch.setattr(module.sys, "stdin", io.StringIO())
    monkeypatch.setattr(module.sys.stdin, "isatty", lambda: False)
    out = io.StringIO()
    monkeypatch.setattr(module.sys, "stdout", out)

    rc = module.main()

    assert rc == 0
    payload = json.loads(out.getvalue())
    assert payload["action"] == "quit"
    assert payload["selection"]["session_id"] == choice.session_id


def test_action_by_key_resolves_known_action(monkeypatch):
    module = _load_module()

    action = module.action_by_key("kill")

    assert action.key == "kill"


def test_available_actions_for_layout_target():
    module = _load_module()
    target = module.ManageTarget(target_kind="layout", repo="repo", repo_name="repo", ui_group_id="group")

    actions = module.available_actions(target)

    assert [action.title for action in actions][:2] == ["Attach Layout", "Kill Layout"]


def test_available_actions_for_panel_target():
    module = _load_module()
    target = module._target_from_choice(_choice(module))

    actions = module.available_actions(target)

    assert [action.title for action in actions][:2] == ["Attach Panel", "Kill Panel"]


def test_main_attach_layout_outputs_payload(monkeypatch):
    module = _load_module()
    choice = _choice(module, role="boss")
    target = module._layout_target([choice])
    monkeypatch.setattr(module.sys, "argv", ["mesh_session_manage.py", "--action", "attach"])
    monkeypatch.setattr(module, "load_router_env", lambda: ("http://router", "token"))
    monkeypatch.setattr(module, "build_session_choices", lambda *args, **kwargs: [choice])
    monkeypatch.setattr(module, "select_target", lambda *args, **kwargs: target)
    monkeypatch.setattr(module, "select_action", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("select_action should not run")))
    monkeypatch.setattr(module.sys, "stdin", io.StringIO())
    monkeypatch.setattr(module.sys.stdin, "isatty", lambda: False)
    out = io.StringIO()
    monkeypatch.setattr(module.sys, "stdout", out)

    rc = module.main()

    assert rc == 0
    payload = json.loads(out.getvalue())
    assert payload["action"] == "attach"
    assert payload["ui"]["repo_name"] == "snake-game"
    assert payload["selection"]["target_kind"] == "layout"
