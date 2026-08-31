"""Shared loading and structural validation for pipeline templates."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


_REVIEW_LEVELS = ("delta", "invariant", "release")
_REVIEW_VERDICTS = ("PASS", "CHANGES_REQUIRED")
_REVIEW_LOOP_EXITS = ("REPLAN", "ESCALATE", "BACKLOG")


def default_pipeline_template_file() -> Path:
    """Return the repository's canonical pipeline template file."""
    return Path(__file__).resolve().parents[1] / "mapping" / "pipeline_templates.yaml"


def load_pipeline_templates(path: str | Path) -> dict[str, Any]:
    """Load the template document without invoking router services."""
    file_path = Path(path).expanduser()
    if not file_path.exists():
        raise FileNotFoundError(f"Template file not found: {file_path}")
    try:
        with file_path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid template YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("Template YAML root must be a mapping")
    templates = data.get("templates")
    if not isinstance(templates, dict):
        raise ValueError("Template YAML must contain 'templates' mapping")
    return data


def normalized_review_convergence(document: dict[str, Any]) -> dict[str, Any]:
    """Validate and copy the canonical bounded review policy."""
    raw = document.get("review_convergence")
    if not isinstance(raw, dict):
        raise ValueError("Template YAML must contain 'review_convergence' mapping")

    policy = dict(raw)
    if policy.get("schema") != "mesh.review.v1":
        raise ValueError("review_convergence.schema must be 'mesh.review.v1'")
    expected_sequences = {
        "levels": _REVIEW_LEVELS,
        "verdicts": _REVIEW_VERDICTS,
        "loop_exits": _REVIEW_LOOP_EXITS,
    }
    for key, expected in expected_sequences.items():
        value = policy.get(key)
        if not isinstance(value, list) or tuple(value) != expected:
            raise ValueError(f"review_convergence.{key} must be {list(expected)}")

    rounds = policy.get("max_correction_rounds")
    if isinstance(rounds, bool) or not isinstance(rounds, int) or rounds < 1:
        raise ValueError("review_convergence.max_correction_rounds must be a positive integer")

    mutation = policy.get("mutation_budget")
    if not isinstance(mutation, dict):
        raise ValueError("review_convergence.mutation_budget must be a mapping")
    default_budget = mutation.get("default_per_critical_invariant")
    if (
        isinstance(default_budget, bool)
        or not isinstance(default_budget, int)
        or default_budget < 0
    ):
        raise ValueError(
            "review_convergence.mutation_budget.default_per_critical_invariant "
            "must be a non-negative integer"
        )

    expected_mappings = {
        "triage": {
            "in_scope_high_medium": "block",
            "release_boundary_high_medium": "block",
            "adjacent_out_of_scope": (
                "backlog_unless_acceptance_or_release_safety_is_invalidated"
            ),
        },
        "release": {
            "pass_requires_level": "release",
            "review_per_frozen_candidate": 1,
            "deploy_authority": "explicit_operator_decision",
        },
    }
    for key, expected in expected_mappings.items():
        if policy.get(key) != expected:
            raise ValueError(f"review_convergence.{key} must be {expected}")
    if mutation.get("expansion_requires") != "concrete_uncovered_failure_mode":
        raise ValueError(
            "review_convergence.mutation_budget.expansion_requires must be "
            "'concrete_uncovered_failure_mode'"
        )

    return policy


def normalized_pipeline_steps(
    template_name: str,
    template: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return copied steps with a validated, explicit dependency DAG."""
    raw_steps = template.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ValueError(f"template '{template_name}' has no steps")

    normalized: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for index, raw_step in enumerate(raw_steps):
        if not isinstance(raw_step, dict):
            raise ValueError(f"template '{template_name}' step #{index} is not a mapping")

        step = dict(raw_step)
        name = str(step.get("name", f"step-{index}")).strip()
        if not name:
            raise ValueError(f"template '{template_name}' step #{index} has an empty name")
        if name in seen_names:
            raise ValueError(f"template '{template_name}' has duplicate step name '{name}'")
        seen_names.add(name)
        step["name"] = name

        raw_dependencies = step.get("depends_on_steps", [])
        if raw_dependencies is None:
            raw_dependencies = []
        if not isinstance(raw_dependencies, list):
            raise ValueError(f"depends_on_steps must be a list in step '{name}'")

        dependencies: list[int] = []
        for raw_dependency in raw_dependencies:
            if isinstance(raw_dependency, bool):
                raise ValueError(
                    f"invalid depends_on_steps value '{raw_dependency}' in step '{name}'"
                )
            try:
                dependency = int(raw_dependency)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"invalid depends_on_steps value '{raw_dependency}' in step '{name}'"
                ) from exc
            if dependency < 0 or dependency >= index:
                raise ValueError(
                    f"step '{name}' dependency index {dependency} must reference an earlier step"
                )
            if dependency in dependencies:
                raise ValueError(
                    f"step '{name}' repeats dependency index {dependency}"
                )
            dependencies.append(dependency)

        if index > 0 and not dependencies:
            dependencies = [index - 1]
        step["depends_on_steps"] = dependencies
        normalized.append(step)

    return normalized
