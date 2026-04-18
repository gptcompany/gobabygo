from __future__ import annotations

import asyncio
import importlib.util
import json
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


def test_role_launch_command_quotes_repo_path(monkeypatch):
    module = _load_module()
    monkeypatch.setenv("PATH", "/nonexistent")

    command = module._role_launch_command("/tmp/demo repo", "gemini")

    assert command.startswith("exec /bin/zsh -lc ")
    assert "/tmp/demo repo" in command
    assert "source ~/.zshrc" in command
    assert "exec gemini" in command


def test_role_launch_command_uses_zsh_from_path(tmp_path, monkeypatch):
    module = _load_module()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    zsh = bin_dir / "zsh"
    zsh.write_text("#!/bin/sh\n", encoding="utf-8")
    zsh.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))

    command = module._role_launch_command("/tmp/demo", "codex --model test")

    assert command == f"exec {zsh} -lc 'source ~/.zshrc >/dev/null 2>&1; cd /tmp/demo && exec codex --model test'"


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


def test_write_handoff_json_creates_repo_relative_artifact(tmp_path):
    module = _load_module()
    repo = tmp_path / "demo"
    repo.mkdir()

    rel_path = module._write_handoff_json(
        str(repo),
        ".mesh/runs",
        "ABC123",
        "01-discuss.json",
        {"phase": "speckit.discuss", "marker": "DONE"},
    )

    assert rel_path == ".mesh/runs/ABC123/01-discuss.json"
    data = json.loads((repo / rel_path).read_text(encoding="utf-8"))
    assert data["schema"] == "mesh.speckit.handoff.v1"
    assert data["run_id"] == "ABC123"
    assert data["phase"] == "speckit.discuss"
    assert data["marker"] == "DONE"


def test_write_handoff_json_can_be_disabled(tmp_path):
    module = _load_module()
    repo = tmp_path / "demo"
    repo.mkdir()

    rel_path = module._write_handoff_json(
        str(repo),
        ".mesh/runs",
        "ABC123",
        "01-discuss.json",
        {"phase": "speckit.discuss"},
        enabled=False,
    )

    assert rel_path == ""
    assert not (repo / ".mesh").exists()


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


def test_auto_approval_signature_distinguishes_edit_files():
    module = _load_module()

    index_prompt = "Action Required\n?  Edit index.html: <p> => <p>\nApply this change?\n1. Allow once"
    style_prompt = "Action Required\n?  Edit style.css: * { => * {\nApply this change?\n1. Allow once"
    snake_reset_prompt = "Action Required\n?  Edit snake.js: function reset() {... => function reset() {...\nApply this change?\n1. Allow once"
    snake_apple_prompt = "Action Required\n?  Edit snake.js: if (head.x === apple.x) => if (head.x === apple.x)\nApply this change?\n1. Allow once"

    assert module._auto_approval_signature(index_prompt, "1", "apply change once") != module._auto_approval_signature(
        style_prompt,
        "1",
        "apply change once",
    )
    assert module._auto_approval_signature(
        snake_reset_prompt,
        "1",
        "apply change once",
    ) != module._auto_approval_signature(snake_apple_prompt, "1", "apply change once")


def test_auto_approval_edit_path_allowlist():
    module = _load_module()
    prompt = "Action Required\n?  Edit style.css: * { => * {\nApply this change?\n1. Allow once"

    assert module._auto_approval_edit_path(prompt) == "style.css"
    assert module._edit_path_allowed("style.css", ("style.css",)) is True
    assert module._edit_path_allowed("style.css", ("index.html", "snake.js")) is False


def test_parse_allowed_edit_paths_from_president_output():
    module = _load_module()

    text = (
        "Include one line exactly like ALLOWED_EDIT_PATHS: path1, path2 for repo-relative files.\n"
        "Plan:\nALLOWED_EDIT_PATHS: index.html, ./snake.js\nSPECKIT_RUN_PRESIDENT_ASSIGNED_ABC"
    )

    assert module._parse_allowed_edit_paths(text) == ("index.html", "snake.js")


def test_effective_edit_allowlist_intersects_operator_and_president_paths():
    module = _load_module()

    assert module._effective_edit_allowlist(("index.html", "snake.js"), ("snake.js", "style.css")) == ("snake.js",)
    assert module._effective_edit_allowlist((), ("index.html",)) == ("index.html",)
    assert module._effective_edit_allowlist(("index.html",), ()) == ("index.html",)


def test_maybe_auto_approve_rejects_edit_outside_allowlist():
    module = _load_module()
    session = _FakeSession(role="worker-gemini", repo="/media/sam/1TB/demo", marked=True)
    prompt = "Action Required\n?  Edit style.css: * { => * {\nApply this change?\n1. Allow once\n4. No, suggest changes"

    changed = asyncio.run(
        module._maybe_auto_approve_prompt(
            session,
            prompt,
            role="worker-gemini",
            enabled=True,
            seen=set(),
            allowed_edit_paths=("index.html", "snake.js"),
        )
    )

    assert changed is True
    assert session.sent == ["4", "\r"]


def test_maybe_auto_approve_rejects_edit_in_non_worker_role():
    module = _load_module()
    session = _FakeSession(role="boss", repo="/media/sam/1TB/demo", marked=True)
    prompt = "Action Required\n?  Edit index.html: <p> => <p>\nApply this change?\n1. Allow once\n4. No, suggest changes"

    changed = asyncio.run(
        module._maybe_auto_approve_prompt(
            session,
            prompt,
            role="boss",
            enabled=True,
            seen=set(),
            allowed_edit_paths=module.NO_AUTO_EDIT_PATHS,
        )
    )

    assert changed is True
    assert session.sent == ["4", "\r"]


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
