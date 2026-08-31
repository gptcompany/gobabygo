from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "speckit-ledger.yml"
REUSABLE = ROOT / ".github" / "workflows" / "speckit-ledger-reusable.yml"


def load_workflow() -> dict:
    return yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def test_workflow_separates_read_only_check_and_authoritative_apply() -> None:
    workflow = load_workflow()

    assert "pull_request_target" not in workflow["on"]
    assert set(workflow["jobs"]) == {"check", "apply"}
    assert workflow["jobs"]["check"]["permissions"] == {
        "contents": "read",
        "issues": "read",
    }
    assert workflow["jobs"]["apply"]["permissions"] == {
        "contents": "read",
        "issues": "write",
    }
    assert workflow["concurrency"]["group"] == "speckit-ledger-${{ github.repository }}"
    assert workflow["concurrency"]["cancel-in-progress"] == "false"


def test_workflow_uses_action_gate_default_branch_and_no_git_credentials() -> None:
    workflow = load_workflow()
    check_steps = workflow["jobs"]["check"]["steps"]
    apply_steps = workflow["jobs"]["apply"]["steps"]
    check_run = next(
        step["run"] for step in check_steps if step.get("name") == "Check ledger plan"
    )
    apply_step = next(step for step in apply_steps if step.get("name") == "Apply ledger")

    assert "plan --all --json" in check_run
    assert "apply --all --json" in apply_step["run"]
    assert 'args=(apply --json -- "$FEATURE")' in apply_step["run"]
    assert apply_step["env"]["MESH_SPECKIT_LEDGER_APPLY"] == "1"
    assert apply_step["env"]["MESH_DEFAULT_BRANCH"] == (
        "${{ github.event.repository.default_branch }}"
    )
    assert "github.ref_name == github.event.repository.default_branch" in workflow[
        "jobs"
    ]["apply"]["if"]
    for steps in (check_steps, apply_steps):
        checkout = next(step for step in steps if step.get("name") == "Checkout")
        assert checkout["with"]["persist-credentials"] == "false"


def test_workflow_retains_machine_readable_reports() -> None:
    workflow = load_workflow()
    text = WORKFLOW.read_text(encoding="utf-8")

    assert text.count("tee speckit-ledger-report.json") == 2
    assert text.count("actions/upload-artifact@v6") == 2
    assert workflow["jobs"]["check"]["steps"][-1]["if"] == "always()"
    assert workflow["jobs"]["apply"]["steps"][-1]["if"] == "always()"


def test_reusable_workflow_checks_out_caller_and_pinned_runtime() -> None:
    workflow = yaml.load(REUSABLE.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    call = workflow["on"]["workflow_call"]
    assert set(call["inputs"]) == {"mode", "runtime_ref"}
    job = workflow["jobs"]["reconcile"]
    assert "permissions" not in job
    checkouts = [
        step for step in job["steps"] if step.get("uses") == "actions/checkout@v6"
    ]
    assert len(checkouts) == 2
    assert checkouts[0]["with"]["persist-credentials"] == "false"
    assert checkouts[1]["with"] == {
        "repository": "gptcompany/gobabygo",
        "ref": "${{ inputs.runtime_ref }}",
        "path": ".mesh-ledger-runtime",
        "persist-credentials": "false",
    }
    text = REUSABLE.read_text(encoding="utf-8")
    assert '[[ "$RUNTIME_REF" =~ ^[0-9a-f]{40}$ ]]' in text
    assert '"$MODE" --all --repo-root "$GITHUB_WORKSPACE" --json' in text
