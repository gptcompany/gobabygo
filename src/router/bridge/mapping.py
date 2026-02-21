"""YAML-based mapping engine for GSD command → semantic step resolution.

Three-layer mapping:
- Layer A (auto): every command emits started/completed/failed (handled by emitter)
- Layer B (rules): regex pattern → semantic step (this module)
- Layer C (overrides): exact command → explicit config (this module)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class MappingResult:
    """Result of resolving a command name through the mapping engine."""

    step: str | None = None
    override: dict | None = None
    matched_rule: str | None = None


class MappingEngine:
    """Resolves GSD command names to semantic steps via YAML rules.

    Priority:
    1. Overrides (exact command match) — Layer C
    2. Rules (regex pattern, first match wins) — Layer B
    3. No match → MappingResult(step=None) — Layer A still works
    """

    def __init__(
        self,
        rules_path: str | Path,
        overrides_path: str | Path | None = None,
    ) -> None:
        self._rules_path = Path(rules_path)
        self._overrides_path = Path(overrides_path) if overrides_path else None
        self._rules: list[dict] = []
        self._overrides: list[dict] = []
        self._compiled_rules: list[tuple[re.Pattern, dict]] = []
        self._override_map: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        """Load and compile rules and overrides from YAML files."""
        # Load rules
        if self._rules_path.exists():
            with open(self._rules_path) as f:
                data = yaml.safe_load(f) or {}
            self._rules = data.get("rules", [])
        else:
            self._rules = []

        # Compile regex patterns
        self._compiled_rules = []
        for rule in self._rules:
            pattern = rule.get("match", "")
            try:
                compiled = re.compile(pattern)
                self._compiled_rules.append((compiled, rule))
            except re.error:
                pass  # skip invalid patterns

        # Load overrides
        if self._overrides_path and self._overrides_path.exists():
            with open(self._overrides_path) as f:
                data = yaml.safe_load(f) or {}
            self._overrides = data.get("overrides", [])
        else:
            self._overrides = []

        # Build override lookup map
        self._override_map = {}
        for ov in self._overrides:
            cmd = ov.get("command", "")
            if cmd:
                self._override_map[cmd] = ov

    def resolve(self, command_name: str) -> MappingResult:
        """Resolve a command name to a semantic step.

        Args:
            command_name: The GSD command (e.g. "gsd:plan-phase").

        Returns:
            MappingResult with step, override, and matched_rule.
        """
        if not command_name:
            return MappingResult()

        # Layer C: check overrides first (exact match)
        if command_name in self._override_map:
            ov = self._override_map[command_name]
            # Also try rules to get the step
            step = None
            matched_rule = None
            for pattern, rule in self._compiled_rules:
                if pattern.search(command_name):
                    step = rule.get("step")
                    matched_rule = rule.get("match")
                    break
            return MappingResult(
                step=step,
                override=ov,
                matched_rule=matched_rule,
            )

        # Layer B: check rules (first regex match wins)
        for pattern, rule in self._compiled_rules:
            if pattern.search(command_name):
                return MappingResult(
                    step=rule.get("step"),
                    override=None,
                    matched_rule=rule.get("match"),
                )

        # No match — Layer A auto events still work
        return MappingResult()

    def reload(self) -> None:
        """Reload YAML files from disk (hot-reload support)."""
        self._load()
