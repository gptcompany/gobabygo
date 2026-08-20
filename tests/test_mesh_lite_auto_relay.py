from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "mesh_lite_auto_relay.py"
    spec = importlib.util.spec_from_file_location("mesh_lite_auto_relay", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_extract_screen_summary_requires_ready_prompt() -> None:
    module = _load_module()

    text = "\n".join(
        [
            "• Ran git status --short --branch",
            "PRESIDENT_UPDATE: commit iniziale creato su main",
            "❯ altro",
        ]
    )

    assert (
        module._extract_screen_summary(
            text,
            require_prefix="PRESIDENT_UPDATE:",
            require_activity=True,
            max_chars=200,
        )
        == ""
    )


def test_target_screen_accepts_input_requires_real_prompt() -> None:
    module = _load_module()

    assert module._target_screen_accepts_input("[mesh:president] spawn provider=codex repo=demo") is False
    assert module._target_screen_accepts_input(">   Type your message or @path/to/file") is True
    assert module._target_screen_accepts_input("› Find and fix a bug in @filename") is True


def test_extract_screen_summary_accepts_gemini_ready_prompt() -> None:
    module = _load_module()

    text = "\n".join(
        [
            "PRESIDENT_UPDATE: 59fc1aae3450d7178d540d90ea46a198d3683805",
            ">   Type your message or @path/to/file",
        ]
    )

    assert (
        module._extract_screen_summary(
            text,
            require_prefix="PRESIDENT_UPDATE:",
            require_activity=False,
            max_chars=200,
        )
        == "59fc1aae3450d7178d540d90ea46a198d3683805"
    )


def test_extract_screen_summary_accepts_codex_ready_prompt() -> None:
    module = _load_module()

    text = "\n".join(
        [
            "• Ho aggiunto TEST_HANDOFF_CODEX_2026_05_01 in README.md.",
            "PRESIDENT_UPDATE: fe471ad4b5f24cad53b2ff2ada5ebd58ea6854e2",
            "› Find and fix a bug in @filename",
        ]
    )

    assert (
        module._extract_screen_summary(
            text,
            require_prefix="PRESIDENT_UPDATE:",
            require_activity=True,
            max_chars=200,
        )
        == "fe471ad4b5f24cad53b2ff2ada5ebd58ea6854e2"
    )


def test_extract_screen_summary_requires_activity_when_enabled() -> None:
    module = _load_module()

    text = "\n".join(
        [
            "PRESIDENT_UPDATE: commit iniziale creato su main",
            "❯",
        ]
    )

    assert (
        module._extract_screen_summary(
            text,
            require_prefix="PRESIDENT_UPDATE:",
            require_activity=True,
            max_chars=200,
        )
        == ""
    )


def test_extract_screen_summary_returns_prefixed_update_when_ready() -> None:
    module = _load_module()

    text = "\n".join(
        [
            "• Ran git log --oneline -1",
            "└ 450bb14 Initial commit",
            "PRESIDENT_UPDATE: commit iniziale creato su main con README.md (450bb14)",
            "❯",
        ]
    )

    assert (
        module._extract_screen_summary(
            text,
            require_prefix="PRESIDENT_UPDATE:",
            require_activity=True,
            max_chars=200,
        )
        == "commit iniziale creato su main con README.md (450bb14)"
    )
