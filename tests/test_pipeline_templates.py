"""Tests for router-independent pipeline template loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.pipeline_templates import (
    default_pipeline_template_file,
    load_pipeline_templates,
    normalized_pipeline_steps,
)


def test_default_template_file_loads_without_router() -> None:
    path = default_pipeline_template_file()

    loaded = load_pipeline_templates(path)

    assert path.name == "pipeline_templates.yaml"
    assert "speckit" in loaded["templates"]


def test_normalized_steps_make_sequential_dependency_explicit() -> None:
    steps = normalized_pipeline_steps(
        "demo",
        {
            "steps": [
                {"name": "specify"},
                {"name": "plan"},
                {"name": "review", "depends_on_steps": [0]},
            ]
        },
    )

    assert [step["depends_on_steps"] for step in steps] == [[], [0], [0]]


@pytest.mark.parametrize(
    ("steps", "error"),
    [
        ([{"name": "same"}, {"name": "same"}], "duplicate step name"),
        ([{"name": "first", "depends_on_steps": [0]}], "earlier step"),
        ([{"name": "first"}, {"name": "second", "depends_on_steps": [2]}], "earlier step"),
        ([{"name": "first"}, {"name": "second", "depends_on_steps": [0, 0]}], "repeats dependency"),
        ([{"name": "first"}, {"name": "second", "depends_on_steps": True}], "must be a list"),
    ],
)
def test_normalized_steps_reject_invalid_dag(
    steps: list[dict[str, object]], error: str
) -> None:
    with pytest.raises(ValueError, match=error):
        normalized_pipeline_steps("demo", {"steps": steps})


def test_load_rejects_missing_templates_mapping(tmp_path: Path) -> None:
    path = tmp_path / "pipeline.yaml"
    path.write_text("version: 1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="templates.*mapping"):
        load_pipeline_templates(path)
