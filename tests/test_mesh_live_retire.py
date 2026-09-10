from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


def _load_module():
    script = Path(__file__).resolve().parents[1] / "scripts" / "mesh_live_retire.py"
    sys.path.insert(0, str(script.parent))
    try:
        spec = importlib.util.spec_from_file_location("mesh_live_retire", script)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


def _preflight(module, handoff: Path):
    return module.RetirementPreflight(
        session="codex-old",
        pane_id="%8",
        pane_pid=100,
        pane_path="/repo",
        command="codex",
        attached=0,
        panes=1,
        git_clean=True,
        screen_state="idle",
        capture_fingerprint="a" * 64,
        handoff_path=str(handoff),
        handoff_sha256="b" * 64,
        reason="superseded by tracked delegation",
    )


def test_retire_plan_never_writes_state_or_terminates(monkeypatch, tmp_path: Path) -> None:
    module = _load_module()
    handoff = tmp_path / "handoff.md"
    handoff.write_text("cancelled", encoding="utf-8")
    preflight = _preflight(module, handoff)
    monkeypatch.setattr(module, "inspect_retirement", lambda *args: preflight)
    calls: list[list[str]] = []
    monkeypatch.setattr(module, "_run", lambda args, **kwargs: calls.append(args))

    result = module.retire_worker(
        "codex-old", str(handoff), preflight.reason,
        apply=False, confirm="", state_file=str(tmp_path / "state.json"),
    )

    assert result["status"] == "planned"
    assert calls == []
    assert not (tmp_path / "state.json").exists()


def test_retire_apply_persists_attempt_before_exact_termination(monkeypatch, tmp_path: Path) -> None:
    module = _load_module()
    handoff = tmp_path / "handoff.md"
    handoff.write_text("cancelled", encoding="utf-8")
    preflight = _preflight(module, handoff)
    monkeypatch.setattr(module, "inspect_retirement", lambda *args: preflight)
    events: list[str] = []
    original_save = module._save_state

    def save(path, state):
        events.append(str(state["retirements"]["codex-old"]["status"]))
        original_save(path, state)

    monkeypatch.setattr(module, "_save_state", save)
    monkeypatch.setattr(
        module,
        "_run",
        lambda args, **kwargs: (
            events.append("kill")
            or subprocess.CompletedProcess(args, 0, "", "")
        ),
    )
    state_path = tmp_path / "state.json"

    result = module.retire_worker(
        "codex-old", str(handoff), preflight.reason,
        apply=True, confirm="codex-old", state_file=str(state_path),
    )

    assert result["status"] == "applied"
    assert events == ["attempted", "kill", "applied"]
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert payload["retirements"]["codex-old"]["status"] == "applied"


def test_retire_apply_requires_redundant_exact_confirmation(monkeypatch, tmp_path: Path) -> None:
    module = _load_module()
    handoff = tmp_path / "handoff.md"
    handoff.write_text("cancelled", encoding="utf-8")
    preflight = _preflight(module, handoff)
    monkeypatch.setattr(module, "inspect_retirement", lambda *args: preflight)
    called = False

    def unexpected(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("state or tmux must not be touched")

    monkeypatch.setattr(module, "_load_state", unexpected)
    with pytest.raises(module.RetirementRefused, match="--confirm"):
        module.retire_worker(
            "codex-old", str(handoff), preflight.reason,
            apply=True, confirm="other", state_file=str(tmp_path / "state.json"),
        )
    assert not called


def test_retire_refuses_changed_second_preflight_without_termination(monkeypatch, tmp_path: Path) -> None:
    module = _load_module()
    handoff = tmp_path / "handoff.md"
    handoff.write_text("cancelled", encoding="utf-8")
    first = _preflight(module, handoff)
    second = module.RetirementPreflight(**{**first.__dict__, "capture_fingerprint": "c" * 64})
    values = iter((first, second))
    monkeypatch.setattr(module, "inspect_retirement", lambda *args: next(values))
    calls: list[list[str]] = []
    monkeypatch.setattr(module, "_run", lambda args, **kwargs: calls.append(args))
    state_path = tmp_path / "state.json"

    with pytest.raises(module.RetirementRefused, match="changed"):
        module.retire_worker(
            "codex-old", str(handoff), first.reason,
            apply=True, confirm="codex-old", state_file=str(state_path),
        )

    assert calls == []
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert payload["retirements"]["codex-old"]["status"] == "refused_changed"


def test_retire_preflight_rejects_unattached_dirty_or_nonidle_worker(monkeypatch, tmp_path: Path) -> None:
    module = _load_module()
    handoff = tmp_path / "handoff.md"
    handoff.write_text("cancelled", encoding="utf-8")
    fields = {
        "session": "codex-old", "pane_id": "%8", "pane_pid": "100", "pane_path": "/repo",
        "command": "codex", "attached": "1", "panes": "1", "dead": "0",
    }
    monkeypatch.setattr(module, "_inspect_tmux", lambda session: (fields, "screen"))
    monkeypatch.setattr(module, "_require_no_children", lambda pid: None)
    monkeypatch.setattr(module, "_require_clean_git", lambda path: True)
    monkeypatch.setattr(module, "session_screen_state", lambda session: "idle")

    with pytest.raises(module.RetirementRefused, match="detached"):
        module.inspect_retirement("codex-old", str(handoff), "superseded by tracked delegation")


def test_retire_handoff_rejects_symlink(tmp_path: Path) -> None:
    module = _load_module()
    target = tmp_path / "target.md"
    target.write_text("preserved", encoding="utf-8")
    link = tmp_path / "handoff.md"
    link.symlink_to(target)

    with pytest.raises(module.RetirementRefused, match="handoff"):
        module._read_handoff(str(link))
