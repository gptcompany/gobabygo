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
    def __init__(self, *, role: str = "", repo: str = "", marked: bool = False):
        self.variables = {
            "user.mesh_ui_tab": "1" if marked else "",
            "user.mesh_repo": repo,
            "user.mesh_role": role,
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
