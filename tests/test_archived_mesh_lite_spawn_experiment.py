import asyncio
from types import SimpleNamespace

from archived import mesh_lite_spawn_experiment as exp


class _FakeSession:
    def __init__(self) -> None:
        self.split_calls = 0

    async def async_split_pane(self, vertical: bool = True):
        self.split_calls += 1
        return _FakeSession()


def test_create_panes_for_roles_uses_sessions_fallback_when_current_session_missing() -> None:
    first = _FakeSession()
    tab = SimpleNamespace(current_session=None, sessions=[first])

    result = asyncio.run(exp._create_panes_for_roles(tab, ["boss", "president"]))

    assert len(result) == 2
    assert result[0] is first


def test_create_panes_for_roles_raises_when_tab_has_no_initial_session() -> None:
    tab = SimpleNamespace(current_session=None, sessions=[])

    try:
        asyncio.run(exp._create_panes_for_roles(tab, ["boss"]))
    except RuntimeError as exc:
        assert "no initial session" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")
