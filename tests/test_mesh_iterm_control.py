from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "mesh_iterm_control.py"
    spec = importlib.util.spec_from_file_location("mesh_iterm_control", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _FakeLine:
    def __init__(self, string: str):
        self.string = string


class _FakeScreen:
    def __init__(self, lines: list[str]):
        self._lines = [_FakeLine(line) for line in lines]

    @property
    def number_of_lines(self) -> int:
        return len(self._lines)

    def line(self, index: int):
        return self._lines[index]


class _FakeSession:
    def __init__(self, *, role: str = "", repo: str = "", marked: bool = False, ui_group_id: str = ""):
        self.variables = {
            "user.mesh_ui_tab": "1" if marked else "",
            "user.mesh_repo": repo,
            "user.mesh_role": role,
            "user.mesh_ui_group_id": ui_group_id,
        }
        self.sent: list[str] = []
        self.activated = False
        self.screen = _FakeScreen([])

    async def async_get_variable(self, name: str):
        return self.variables.get(name, "")

    async def async_send_text(self, text: str):
        self.sent.append(text)

    async def async_activate(self):
        self.activated = True

    async def async_get_screen_contents(self):
        return self.screen


class _FakeTab:
    def __init__(self, sessions):
        self.sessions = sessions


class _FakeWindow:
    def __init__(self, tabs):
        self.tabs = tabs


def test_key_text_maps_common_keys():
    module = _load_module()

    assert module._key_text("enter") == "\r"
    assert module._key_text("up") == "\x1b[A"
    assert module._key_text("ctrl-c") == "\x03"


def test_iterm_retry_enabled_reads_env(monkeypatch):
    module = _load_module()

    monkeypatch.delenv("MESH_ITERM_RETRY", raising=False)
    assert module._iterm_retry_enabled() is False

    monkeypatch.setenv("MESH_ITERM_RETRY", "yes")
    assert module._iterm_retry_enabled() is True


def test_emit_writes_output_file(tmp_path):
    module = _load_module()
    target = tmp_path / "term.txt"

    module._emit("one", str(target))

    assert target.read_text(encoding="utf-8") == "one\n"


def test_ui_command_env_key_normalizes_role():
    module = _load_module()

    assert module._ui_command_env_key("worker-gemini") == "MESH_UI_CMD_WORKER_GEMINI"


def test_role_launch_command_quotes_repo_path():
    module = _load_module()

    command = module._role_launch_command("/tmp/demo repo", "gemini")

    assert command == "cd '/tmp/demo repo' && exec gemini"


def test_format_mesh_msg_is_single_line_and_quoted():
    module = _load_module()

    message = module._format_mesh_msg(
        id="m1",
        from_role="boss",
        task="line one\nline two",
        write_allowed="false",
    )

    assert "\n" not in message
    assert message.startswith("MESH_MSG ")
    assert "task='line one line two'" in message
    assert message.endswith(" END_MESH_MSG")


def test_turn_limit_text_uses_minimum_one_turn():
    module = _load_module()

    assert "massimo 1 risposta" in module._turn_limit_text(0)


def test_auto_approval_choice_handles_known_prompts():
    module = _load_module()

    assert module._auto_approval_choice("Apply this change?\n1. Yes, allow once") == ("1", "apply change once")
    assert module._auto_approval_choice("Allow execution of 'ls'?\n2. Allow for this session") == (
        "2",
        "allow command for session",
    )
    assert module._auto_approval_choice("Do you trust the files in this folder?\n1. Yes\n2. No") == (
        "1",
        "trust folder",
    )
    assert module._auto_approval_choice("Doyoutrustthecontentsofthisdirectory?\n› 1. Yes, continue") == (
        "1",
        "trust folder",
    )


def test_auto_approval_choice_ignores_plain_screen():
    module = _load_module()

    assert module._auto_approval_choice("Type your message") == ("", "")


def test_wait_for_screen_any_auto_approves_before_broad_marker():
    module = _load_module()
    session = _FakeSession(role="president", repo="/media/sam/1TB/demo", marked=True)
    session.screen = _FakeScreen(["Doyoutrustthecontentsofthisdirectory?", "› 1. Yes, continue"])

    async def _send_text(text: str):
        session.sent.append(text)
        if text == "\r":
            session.screen = _FakeScreen(["Codex ready", "›"])

    session.async_send_text = _send_text

    marker = asyncio.run(
        module._wait_for_screen_any(
            session,
            role="president",
            markers=("›",),
            timeout=3.0,
            poll_interval=0.1,
            description="Codex prompt",
            auto_approve_prompts=True,
        )
    )

    assert marker == "›"
    assert session.sent == ["1", "\r"]


def test_mesh_sessions_filters_marked_repo_and_role():
    module = _load_module()
    target = _FakeSession(role="boss", repo="/media/sam/1TB/demo", marked=True)
    other_role = _FakeSession(role="president", repo="/media/sam/1TB/demo", marked=True)
    other_repo = _FakeSession(role="boss", repo="/media/sam/1TB/other", marked=True)
    plain = _FakeSession()
    app = type(
        "App",
        (),
        {"windows": [type("Window", (), {"tabs": [_FakeTab([target, other_role, other_repo, plain])]})()]},
    )()

    panes = asyncio.run(module._mesh_sessions(app, "/media/sam/1TB/demo"))

    assert [pane.role for pane in panes] == ["boss", "president"]


def test_mesh_sessions_filters_ui_group_id():
    module = _load_module()
    target = _FakeSession(role="boss", repo="/media/sam/1TB/demo", marked=True, ui_group_id="group-1")
    other_group = _FakeSession(role="boss", repo="/media/sam/1TB/demo", marked=True, ui_group_id="group-2")
    app = type(
        "App",
        (),
        {"windows": [type("Window", (), {"tabs": [_FakeTab([target, other_group])]})()]},
    )()

    panes = asyncio.run(module._mesh_sessions(app, "/media/sam/1TB/demo", "group-1"))

    assert [pane.ui_group_id for pane in panes] == ["group-1"]


def test_find_mesh_pane_returns_unique_match():
    module = _load_module()
    boss = _FakeSession(role="boss", repo="/media/sam/1TB/demo", marked=True)
    president = _FakeSession(role="president", repo="/media/sam/1TB/demo", marked=True)
    app = type(
        "App",
        (),
        {"windows": [type("Window", (), {"tabs": [_FakeTab([boss, president])]})()]},
    )()

    pane = asyncio.run(module._find_mesh_pane(app, "/media/sam/1TB/demo", "president"))

    assert pane.role == "president"
    assert pane.session is president


def test_screen_tail_keeps_recent_non_empty_lines():
    module = _load_module()
    session = _FakeSession(role="boss", repo="/media/sam/1TB/demo", marked=True)
    session.screen = _FakeScreen(["", "one", "two\x00", "   ", "three"])

    text = asyncio.run(module._screen_tail(session, lines=2))

    assert text == "two\nthree"


def test_run_send_text_activates_pane_before_sending():
    module = _load_module()
    boss = _FakeSession(role="boss", repo="/media/sam/1TB/demo", marked=True)
    app = type(
        "App",
        (),
        {"windows": [type("Window", (), {"tabs": [_FakeTab([boss])]})()]},
    )()
    args = type("Args", (), {"cmd": "send-text", "repo": "/media/sam/1TB/demo", "role": "boss", "text": "ciao"})()

    async def _wrapped():
        import types

        async def _async_get_app(_conn):
            return app

        fake_iterm2 = types.SimpleNamespace(async_get_app=_async_get_app)
        previous = sys.modules.get("iterm2")
        try:
            sys.modules["iterm2"] = fake_iterm2
            return await module._run(None, args)
        finally:
            if previous is None:
                sys.modules.pop("iterm2", None)
            else:
                sys.modules["iterm2"] = previous

    assert asyncio.run(_wrapped()) == 0
    assert boss.activated is True
    assert boss.sent == ["ciao"]


def test_run_send_key_activates_pane_before_sending():
    module = _load_module()
    president = _FakeSession(role="president", repo="/media/sam/1TB/demo", marked=True)
    app = type(
        "App",
        (),
        {"windows": [type("Window", (), {"tabs": [_FakeTab([president])]})()]},
    )()
    args = type("Args", (), {"cmd": "send-key", "repo": "/media/sam/1TB/demo", "role": "president", "key": "enter"})()

    async def _wrapped():
        import types

        async def _async_get_app(_conn):
            return app

        fake_iterm2 = types.SimpleNamespace(async_get_app=_async_get_app)
        previous = sys.modules.get("iterm2")
        try:
            sys.modules["iterm2"] = fake_iterm2
            return await module._run(None, args)
        finally:
            if previous is None:
                sys.modules.pop("iterm2", None)
            else:
                sys.modules["iterm2"] = previous

    assert asyncio.run(_wrapped()) == 0
    assert president.activated is True
    assert president.sent == ["\r"]


def test_run_send_line_appends_newline_and_activates():
    module = _load_module()
    boss = _FakeSession(role="boss", repo="/media/sam/1TB/demo", marked=True)
    app = type(
        "App",
        (),
        {"windows": [type("Window", (), {"tabs": [_FakeTab([boss])]})()]},
    )()
    args = type("Args", (), {"cmd": "send-line", "repo": "/media/sam/1TB/demo", "role": "boss", "text": "/GBG status"})()

    async def _wrapped():
        import types

        async def _async_get_app(_conn):
            return app

        fake_iterm2 = types.SimpleNamespace(async_get_app=_async_get_app)
        previous = sys.modules.get("iterm2")
        try:
            sys.modules["iterm2"] = fake_iterm2
            return await module._run(None, args)
        finally:
            if previous is None:
                sys.modules.pop("iterm2", None)
            else:
                sys.modules["iterm2"] = previous

    assert asyncio.run(_wrapped()) == 0
    assert boss.activated is True
    assert boss.sent == ["/GBG status", "\r"]
