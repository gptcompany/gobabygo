from __future__ import annotations

from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def test_cloudevents_stays_on_legacy_http_compatible_major() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]

    assert "cloudevents>=1.12.0,<2.0.0" in project["dependencies"]
