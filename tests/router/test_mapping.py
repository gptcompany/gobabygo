"""Tests for the YAML-based mapping engine."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.router.bridge.mapping import MappingEngine

# Use the real mapping files from the project
_MAPPING_DIR = Path(__file__).resolve().parent.parent.parent / "mapping"
_RULES_PATH = _MAPPING_DIR / "command_rules.yaml"
_OVERRIDES_PATH = _MAPPING_DIR / "command_overrides.yaml"


@pytest.fixture
def engine():
    """MappingEngine with the project's real YAML config files."""
    return MappingEngine(_RULES_PATH, _OVERRIDES_PATH)


@pytest.fixture
def engine_no_overrides():
    """MappingEngine without overrides file."""
    return MappingEngine(_RULES_PATH, None)


class TestMappingEngineResolve:
    def test_research_command(self, engine):
        result = engine.resolve("gsd:research-phase")
        assert result.step == "research"
        assert result.matched_rule is not None

    def test_research_bare_command(self, engine):
        result = engine.resolve("research")
        assert result.step == "research"

    def test_discuss_command(self, engine):
        result = engine.resolve("gsd:discuss-phase")
        assert result.step == "discuss"

    def test_plan_command(self, engine):
        result = engine.resolve("gsd:plan-phase")
        assert result.step == "plan"

    def test_list_phase_assumptions(self, engine):
        result = engine.resolve("gsd:list-phase-assumptions")
        assert result.step == "plan"

    def test_execute_command(self, engine):
        result = engine.resolve("gsd:execute-phase")
        assert result.step == "implement"

    def test_execute_sync_command(self, engine):
        result = engine.resolve("gsd:execute-phase-sync")
        assert result.step == "implement"

    def test_validate_command(self, engine):
        result = engine.resolve("validate")
        assert result.step == "review"

    def test_verify_work_command(self, engine):
        result = engine.resolve("gsd:verify-work")
        assert result.step == "review"

    def test_confidence_gate_command(self, engine):
        result = engine.resolve("confidence-gate")
        assert result.step == "review"

    def test_plan_fix_command(self, engine):
        result = engine.resolve("gsd:plan-fix")
        assert result.step == "remediation"

    def test_speckit_autofix(self, engine):
        result = engine.resolve("speckit.autofix")
        assert result.step == "remediation"

    def test_implement_plan_command(self, engine):
        result = engine.resolve("gsd:implement-plan")
        assert result.step == "implement"

    def test_implement_fix_command(self, engine):
        result = engine.resolve("gsd:implement-fix")
        assert result.step == "implement"

    def test_implement_phase_sync_command(self, engine):
        result = engine.resolve("gsd:implement-phase-sync")
        assert result.step == "implement"

    def test_unknown_command_returns_none(self, engine):
        result = engine.resolve("gsd:some-unknown-command")
        assert result.step is None
        assert result.override is None
        assert result.matched_rule is None

    def test_empty_command_returns_none(self, engine):
        result = engine.resolve("")
        assert result.step is None


class TestMappingOverrides:
    def test_pipeline_gsd_override(self, engine):
        result = engine.resolve("pipeline:gsd")
        assert result.override is not None
        assert result.override["emit_parent_run"] is True
        assert result.override["open_phase_task"] is True

    def test_pipeline_gsd_also_resolves_step(self, engine):
        result = engine.resolve("pipeline:gsd")
        assert result.step == "implement"

    def test_execute_phase_sync_override(self, engine):
        result = engine.resolve("gsd:execute-phase-sync")
        assert result.override is not None
        assert result.override["requires_plan"] is True

    def test_override_takes_precedence(self, engine):
        """Override is returned even when rule also matches."""
        result = engine.resolve("pipeline:gsd")
        assert result.override is not None
        assert result.step is not None  # rule also matched


class TestMappingEngineLoad:
    def test_load_with_real_yaml(self, engine):
        result = engine.resolve("gsd:research-phase")
        assert result.step == "research"

    def test_missing_overrides_file(self, engine_no_overrides):
        result = engine_no_overrides.resolve("pipeline:gsd")
        assert result.override is None
        assert result.step == "implement"  # rule still works

    def test_reload_picks_up_changes(self, tmp_path):
        rules_file = tmp_path / "rules.yaml"
        rules_file.write_text(yaml.dump({
            "version": 1,
            "rules": [{"match": "^test$", "step": "plan"}],
        }))
        engine = MappingEngine(rules_file)
        assert engine.resolve("test").step == "plan"

        # Change the file
        rules_file.write_text(yaml.dump({
            "version": 1,
            "rules": [{"match": "^test$", "step": "review"}],
        }))
        engine.reload()
        assert engine.resolve("test").step == "review"

    def test_missing_rules_file(self, tmp_path):
        engine = MappingEngine(tmp_path / "nonexistent.yaml")
        result = engine.resolve("anything")
        assert result.step is None

    def test_rules_matched_in_order(self, tmp_path):
        """First matching rule wins."""
        rules_file = tmp_path / "rules.yaml"
        rules_file.write_text(yaml.dump({
            "version": 1,
            "rules": [
                {"match": "^gsd:plan", "step": "plan"},
                {"match": "^gsd:plan-fix", "step": "remediation"},
            ],
        }))
        engine = MappingEngine(rules_file)
        # "gsd:plan-fix" matches first rule "^gsd:plan"
        result = engine.resolve("gsd:plan-fix")
        assert result.step == "plan"  # first match wins
