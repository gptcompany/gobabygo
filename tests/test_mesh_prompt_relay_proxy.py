from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "mesh_prompt_relay_proxy.py"
    spec = importlib.util.spec_from_file_location("mesh_prompt_relay_proxy", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_clean_submitted_line_skips_slash_commands():
    module = _load_module()

    assert module._clean_submitted_line("/help", ignore_slash_commands=True) is None
    assert module._clean_submitted_line("/help", ignore_slash_commands=False) == "/help"


def test_input_tracker_emits_prompt_on_enter_and_handles_backspace():
    module = _load_module()
    tracker = module._InputTracker(ignore_slash_commands=True)

    assert tracker.feed(b"ciaox\x7f\n") == ["ciao"]


def test_input_tracker_skips_slash_commands_on_submit():
    module = _load_module()
    tracker = module._InputTracker(ignore_slash_commands=True)

    assert tracker.feed(b"/model\n") == ["/model"]
    assert tracker.feed(b"saluta il president\n") == ["saluta il president"]


def test_relay_prompt_uses_mesh_send(monkeypatch, tmp_path):
    module = _load_module()
    mesh_script = tmp_path / "mesh"
    mesh_script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    mesh_script.chmod(0o755)
    calls = {}

    def fake_run(command, **kwargs):
        calls["command"] = command
        calls["kwargs"] = kwargs
        class Result:
            returncode = 0
        return Result()

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    args = module.argparse.Namespace(
        mesh_script=str(mesh_script),
        target_role="president",
        ui_group_id="demo-ui-1",
        source_role="boss",
        message_prefix="[boss relay] ",
        ignore_slash_commands=True,
        child_command="ccs gemini",
    )

    module._relay_prompt(args, "saluta il president")

    assert calls["command"] == [
        str(mesh_script),
        "send",
        "president",
        "--ui-group-id",
        "demo-ui-1",
        "[boss relay] saluta il president",
    ]


def test_relay_prompt_uses_term_exec_transport(monkeypatch, tmp_path):
    module = _load_module()
    mesh_script = tmp_path / "mesh"
    mesh_script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    mesh_script.chmod(0o755)
    calls = {}

    def fake_run(command, **kwargs):
        calls["command"] = command
        calls["kwargs"] = kwargs
        class Result:
            returncode = 0
        return Result()

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    args = module.argparse.Namespace(
        mesh_script=str(mesh_script),
        transport="term_exec",
        target_role="president",
        target_repo="/tmp/gbg-snake-proof",
        ui_group_id="demo-ui-1",
        source_role="boss",
        message_prefix="",
        ignore_slash_commands=True,
        child_command="claude",
    )

    module._relay_prompt(args, "boss summary")

    assert calls["command"] == [
        str(mesh_script),
        "term",
        "exec",
        "/tmp/gbg-snake-proof",
        "president",
        "boss summary",
        "--ui-group-id",
        "demo-ui-1",
    ]


def test_format_local_ack_renders_target_role():
    module = _load_module()

    assert module._format_local_ack(
        "Inoltrato a {target_role}.",
        target_role="president",
        prompt="saluta il president",
    ) == "\r\n● Inoltrato a president.\r\n"


def test_looks_ready_prompt_requires_empty_terminal_prompt_line():
    module = _load_module()

    assert module._looks_ready_prompt("ciao\n❯\n") is True
    assert module._looks_ready_prompt("❯ saluta il president\n") is False
    assert module._looks_ready_prompt("risposta\n❯ altro\n") is False
