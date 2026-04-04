import sys

from scripts.mesh_lite.iterm import _apple_string_expr, get_session


def test_apple_string_expr_serializes_multiline_text() -> None:
    expr = _apple_string_expr('hello "mesh"\nsecond line')

    assert expr == '"hello \\"mesh\\"" & linefeed & "second line"'


def test_get_session_falls_back_to_osascript_when_iterm2_missing(monkeypatch) -> None:
    record_delim = chr(30)
    field_delim = chr(31)
    monkeypatch.setitem(sys.modules, "iterm2", None)
    monkeypatch.setattr(
        "scripts.mesh_lite.iterm._osascript",
        lambda _script: f"S2{field_delim}1{field_delim}2{field_delim}3{field_delim}/dev/ttys002{record_delim}",
    )

    session = get_session("S2")

    assert session is not None
    assert session.session_id == "S2"
    assert session.tty == "/dev/ttys002"
    assert session.window_index == 1
