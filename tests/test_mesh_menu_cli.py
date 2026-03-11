from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "mesh_menu_cli.py"
    sys.path.insert(0, str(script_path.parent))
    spec = importlib.util.spec_from_file_location("mesh_menu_cli", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_build_default_actions_are_repo_aware():
    module = _load_module()

    actions = module.build_default_actions_for_repo("/tmp/snake-game", "snake-game")

    assert [action.argv for action in actions] == [
        ("attach",),
        ("sessions",),
        ("ui", "/tmp/snake-game"),
        ("start",),
        ("attach", "--all"),
        (),
    ]
    assert actions[0].title == "Attach live session (snake-game)"
    assert actions[-1].title == "Quit"


def test_select_action_prefers_questionary_when_available(monkeypatch):
    module = _load_module()
    actions = module.build_default_actions_for_repo("/tmp/snake-game", "snake-game")
    monkeypatch.setattr(module, "_questionary_select_action", lambda actions: actions[2])

    selected = module.select_action(actions, interactive=True)

    assert selected.argv == ("ui", "/tmp/snake-game")


def test_select_action_falls_back_to_numeric_prompt(monkeypatch, capsys):
    module = _load_module()
    actions = module.build_default_actions_for_repo("/tmp/snake-game", "snake-game")

    def fail_questionary(_actions):
        raise RuntimeError("questionary unavailable")

    monkeypatch.setattr(module, "_questionary_select_action", fail_questionary)

    selected = module.select_action(actions, prompt_fn=lambda _: "5", interactive=True)

    assert selected.argv == ("attach", "--all")
    assert "Attach live session (snake-game)" in capsys.readouterr().err


def test_main_outputs_selected_command(monkeypatch):
    module = _load_module()
    monkeypatch.setattr(module.sys, "argv", ["mesh_menu_cli.py"])
    monkeypatch.setattr(module, "detect_repo_context", lambda cwd=None: ("/tmp/snake-game", "snake-game"))
    monkeypatch.setattr(
        module,
        "select_action",
        lambda actions, **kwargs: actions[0],
    )
    monkeypatch.setattr(module.sys, "stdin", io.StringIO())
    monkeypatch.setattr(module.sys.stdin, "isatty", lambda: False)
    out = io.StringIO()
    err = io.StringIO()
    monkeypatch.setattr(module.sys, "stdout", out)
    monkeypatch.setattr(module.sys, "stderr", err)

    rc = module.main()

    assert rc == 0
    payload = json.loads(out.getvalue())
    assert payload["argv"] == ["attach"]
    assert payload["action"]["title"] == "Attach live session (snake-game)"
    assert err.getvalue() == ""


def test_main_can_return_quit(monkeypatch):
    module = _load_module()
    monkeypatch.setattr(module.sys, "argv", ["mesh_menu_cli.py"])
    monkeypatch.setattr(module, "detect_repo_context", lambda cwd=None: ("/tmp/snake-game", "snake-game"))
    monkeypatch.setattr(
        module,
        "select_action",
        lambda actions, **kwargs: actions[-1],
    )
    monkeypatch.setattr(module.sys, "stdin", io.StringIO())
    monkeypatch.setattr(module.sys.stdin, "isatty", lambda: False)
    out = io.StringIO()
    monkeypatch.setattr(module.sys, "stdout", out)

    rc = module.main()

    assert rc == 0
    payload = json.loads(out.getvalue())
    assert payload["argv"] == []


def test_main_can_write_payload_to_file(tmp_path, monkeypatch):
    module = _load_module()
    output_path = tmp_path / "menu.json"
    monkeypatch.setattr(module.sys, "argv", ["mesh_menu_cli.py", "--output", str(output_path)])
    monkeypatch.setattr(module, "detect_repo_context", lambda cwd=None: ("/tmp/snake-game", "snake-game"))
    monkeypatch.setattr(
        module,
        "select_action",
        lambda actions, **kwargs: actions[1],
    )
    monkeypatch.setattr(module.sys, "stdin", io.StringIO())
    monkeypatch.setattr(module.sys.stdin, "isatty", lambda: False)
    out = io.StringIO()
    monkeypatch.setattr(module.sys, "stdout", out)

    rc = module.main()

    assert rc == 0
    assert out.getvalue() == ""
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["argv"] == ["sessions"]
