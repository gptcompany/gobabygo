"""Process-level smoke coverage for the installed review convergence contract."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
MESH = ROOT / "scripts" / "mesh"


def _mesh(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(MESH), *args],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_speckit_review_policy_reaches_cli_and_coordinator_contract(
    tmp_path: Path,
) -> None:
    projection = _mesh(
        "live",
        "workflow",
        "show",
        "speckit",
        "--scope",
        "coordinator",
        "--json",
        cwd=tmp_path,
    )
    assert projection.returncode == 0, projection.stderr
    policy = json.loads(projection.stdout)["review_convergence"]
    assert policy["levels"] == ["delta", "invariant", "release"]
    assert policy["max_correction_rounds"] == 2
    assert policy["release"]["pass_requires_level"] == "release"
    assert policy["release"]["deploy_authority"] == "explicit_operator_decision"

    prompt = _mesh(
        "live",
        "coordinator-prompt",
        "--all",
        "--session",
        "claude-review-convergence-e2e",
        "--workflow",
        "speckit",
        cwd=tmp_path,
    )
    assert prompt.returncode == 0, prompt.stderr
    assert "REVIEW_LEVEL: DELTA|INVARIANT|RELEASE" in prompt.stdout
    assert "Only a RELEASE PASS satisfies the final review gate" in prompt.stdout
    assert "REVIEW_LOOP_DECISION: REPLAN|ESCALATE|BACKLOG" in prompt.stdout
    assert "never authorization to deploy" in prompt.stdout
