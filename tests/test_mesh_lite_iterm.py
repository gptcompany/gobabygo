from scripts.mesh_lite.iterm import _apple_string_expr


def test_apple_string_expr_serializes_multiline_text() -> None:
    expr = _apple_string_expr('hello "mesh"\nsecond line')

    assert expr == '"hello \\"mesh\\"" & linefeed & "second line"'
