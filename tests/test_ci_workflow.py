"""Structural checks for the repository CI workflow."""

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def _load(path: Path) -> dict:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def test_official_actions_use_node24_majors() -> None:
    text = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(WORKFLOWS.glob("*.yml"))
    )

    assert "actions/checkout@v4" not in text
    assert "actions/setup-python@v5" not in text
    assert "actions/upload-artifact@v4" not in text
    assert "actions/checkout@v6" in text
    assert "actions/setup-python@v6" in text
    assert "actions/upload-artifact@v6" in text


def test_ci_pins_and_verifies_actionlint_download() -> None:
    workflow = _load(WORKFLOWS / "ci.yml")
    steps = workflow["jobs"]["test"]["steps"]
    lint = next(step for step in steps if step.get("name") == "Lint GitHub Actions workflows")

    assert lint["env"] == {
        "ACTIONLINT_VERSION": "1.7.12",
        "ACTIONLINT_SHA256": (
            "8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8"
        ),
    }
    assert "sha256sum --check --status" in lint["run"]
    assert '"$binary" .github/workflows/*.yml' in lint["run"]
    assert "curl" in lint["run"]
    assert "| sh\n" not in lint["run"]
    assert "| bash" not in lint["run"]


def test_actionlint_knows_the_registered_self_hosted_label() -> None:
    config = _load(ROOT / ".github" / "actionlint.yaml")

    assert config == {"self-hosted-runner": {"labels": ["muletto"]}}
