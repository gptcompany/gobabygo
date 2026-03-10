"""Validation for built-in pipeline templates shipped with the repo."""

from __future__ import annotations

from pathlib import Path

import yaml


def _load_templates() -> dict[str, dict]:
    path = Path(__file__).resolve().parents[1] / "mapping" / "pipeline_templates.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    templates = data.get("templates")
    assert isinstance(templates, dict)
    return templates


def test_canonical_templates_are_session_only() -> None:
    templates = _load_templates()
    for name in ("confidence_gate_team", "gsd", "speckit", "speckit_codex", "gemini_team_demo"):
        steps = templates[name]["steps"]
        assert steps
        assert all(step.get("execution_mode") == "session" for step in steps)


def test_canonical_templates_define_runtime_roles() -> None:
    templates = _load_templates()
    valid_roles = {"president", "lead", "worker"}
    for name in ("confidence_gate_team", "gsd", "speckit", "speckit_codex", "gemini_team_demo"):
        steps = templates[name]["steps"]
        assert all(str(step.get("role", "")).strip() in valid_roles for step in steps)


def test_gsd_and_speckit_are_multi_model_team_templates() -> None:
    templates = _load_templates()
    for name in ("gsd", "speckit"):
        steps = templates[name]["steps"]
        clis = {str(step.get("target_cli", "")).strip() for step in steps}
        roles = {str(step.get("role", "")).strip() for step in steps}
        assert {"claude", "codex", "gemini"} <= clis
        assert {"president", "lead", "worker"} <= roles


def test_gemini_team_demo_is_gemini_only() -> None:
    templates = _load_templates()
    steps = templates["gemini_team_demo"]["steps"]
    assert steps
    assert {str(step.get("target_cli", "")).strip() for step in steps} == {"gemini"}
    assert {str(step.get("role", "")).strip() for step in steps} == {"president", "lead", "worker"}
