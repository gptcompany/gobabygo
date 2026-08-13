"""Shared loading and structural validation for pipeline templates."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


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
