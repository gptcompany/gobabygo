import sys

from scripts.mesh_lite.iterm import (
    _apple_string_expr,
    _codex_needs_submit_retry,
    _list_sessions_via_osascript,
    get_session,
)


def test_apple_string_expr_serializes_multiline_text() -> None:
    expr = _apple_string_expr('hello "mesh"\nsecond line')

    assert expr == '"hello \\"mesh\\"" & linefeed & "second line"'


def test_get_session_falls_back_to_osascript_when_iterm2_missing(monkeypatch) -> None:
    record_delim = chr(30)
    field_delim = chr(31)
    monkeypatch.setitem(sys.modules, "iterm2", None)
    monkeypatch.setattr(
        "scripts.mesh_lite.iterm._osascript_raw",
        lambda _script: f"S2{field_delim}1{field_delim}2{field_delim}3{field_delim}/dev/ttys002{record_delim}",
    )

    session = get_session("S2")

    assert session is not None
    assert session.session_id == "S2"
    assert session.tty == "/dev/ttys002"
    assert session.window_index == 1


def test_list_sessions_via_osascript_keeps_last_session_with_empty_tty(monkeypatch) -> None:
    field_delim = chr(31)
    record_delim = chr(30)
    monkeypatch.setattr(
        "scripts.mesh_lite.iterm._osascript_raw",
        lambda _script: f"S2{field_delim}1{field_delim}2{field_delim}3{field_delim}{record_delim}",
    )

    sessions = _list_sessions_via_osascript()

    assert len(sessions) == 1
    assert sessions[0].session_id == "S2"
    assert sessions[0].tty == ""


def test_codex_needs_submit_retry_when_prompt_is_still_pending() -> None:
    screen = "› Reply with exactly PRESIDENT_ACK.\n  gpt-5.4 high · /tmp/demo"

    assert _codex_needs_submit_retry(screen, "Reply with exactly PRESIDENT_ACK.") is True


def test_codex_needs_submit_retry_skips_when_activity_is_visible() -> None:
    screen = "› Reply with exactly PRESIDENT_ACK.\n• PRESIDENT_ACK\n  gpt-5.4 high · /tmp/demo"

    assert _codex_needs_submit_retry(screen, "Reply with exactly PRESIDENT_ACK.") is False
