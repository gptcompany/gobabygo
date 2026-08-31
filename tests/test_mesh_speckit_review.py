"""Tests for the transactional Spec Kit review ledger."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import mesh_speckit_review as review


ROOT = Path(__file__).resolve().parents[1]
MESH = ROOT / "scripts" / "mesh"


SCOPE_A = "commit:" + ("a" * 40)
SCOPE_B = "commit:" + ("b" * 40)
SCOPE_C = "commit:" + ("c" * 40)
DELTA_1 = "diff-sha256:" + ("d" * 64)
DELTA_2 = "diff-sha256:" + ("e" * 64)


def _feature(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    feature = repo / "specs" / "001-review-ledger"
    feature.mkdir(parents=True)
    (feature / "github-ledger.json").write_text(
        json.dumps(
            {
                "schema": "mesh.speckit.github-ledger.v1",
                "feature_id": "review-ledger-001",
                "repository": "example/project",
                "enabled": True,
            }
        ),
        encoding="utf-8",
    )
    (feature / "tasks.md").write_text(
        "- [ ] T001 Implement bounded review ledger\n- [ ] T002 Another task\n",
        encoding="utf-8",
    )
    return repo, feature


def _init(repo: Path, feature: Path, revision: int = 0) -> dict:
    return review.initialize_task(
        repo,
        feature,
        "T001",
        scope=SCOPE_A,
        writer_session="agy-project",
        invariants=["release requires RELEASE PASS", "at most two corrections"],
        mutation_budget=1,
        expected_revision=revision,
    )


def _open(
    repo: Path,
    feature: Path,
    revision: int,
    *,
    level: str,
    scope: str,
    invariant: str = "",
) -> dict:
    return review.open_review(
        repo,
        feature,
        "T001",
        level=level,
        scope=scope,
        reviewer_session="codex-project",
        delegation_id=f"review-{revision}",
        invariant=invariant,
        expected_revision=revision,
    )


def _record(
    repo: Path,
    feature: Path,
    revision: int,
    verdict: str,
    *,
    high: int = 0,
    medium: int = 0,
    safety: bool = False,
) -> dict:
    evidence = feature / f"review-{revision}.md"
    evidence.write_text(f"review evidence revision {revision}\n", encoding="utf-8")
    return review.record_review(
        repo,
        feature,
        "T001",
        verdict=verdict,
        evidence_file=evidence,
        blocking_high=high,
        blocking_medium=medium,
        invalidates_safety=safety,
        mutations_run=1,
        expected_revision=revision,
    )


def test_release_pass_is_terminal_and_durable(tmp_path: Path) -> None:
    repo, feature = _feature(tmp_path)
    assert _init(repo, feature)["revision"] == 1
    assert _open(repo, feature, 1, level="RELEASE", scope=SCOPE_A)["revision"] == 2

    result = _record(repo, feature, 2, "PASS")

    assert result["status"] == "RELEASE_PASSED"
    state = review.review_status(repo, feature, "T001")
    assert state["revision"] == 3
    assert state["status"] == "RELEASE_PASSED"
    assert state["correction_round"] == 0
    evidence = state["events"][-1]["data"]["evidence"]
    assert evidence["path"] == "review-2.md"
    assert len(evidence["sha256"]) == 64
    assert (feature / "review-ledger.json").is_file()
    assert review.review_check(repo, feature, "T001")["release_passed"] is True


def test_pass_rejects_blocking_findings_without_mutation(tmp_path: Path) -> None:
    repo, feature = _feature(tmp_path)
    _init(repo, feature)
    _open(repo, feature, 1, level="RELEASE", scope=SCOPE_A)

    with pytest.raises(review.ReviewLedgerError, match="PASS is forbidden"):
        _record(repo, feature, 2, "PASS", high=1)

    assert review.review_status(repo, feature, "T001")["revision"] == 2


def test_two_corrections_then_escalation_and_no_third_round(tmp_path: Path) -> None:
    repo, feature = _feature(tmp_path)
    _init(repo, feature)
    _open(repo, feature, 1, level="RELEASE", scope=SCOPE_A)
    assert _record(repo, feature, 2, "CHANGES_REQUIRED", high=1)["status"] == "CHANGES_REQUIRED"

    first = review.open_correction(
        repo, feature, "T001", delegation_id="fix-1", expected_revision=3
    )
    assert first["round"] == 1
    _open(repo, feature, 4, level="DELTA", scope=DELTA_1)
    _record(repo, feature, 5, "CHANGES_REQUIRED", medium=1)
    second = review.open_correction(
        repo, feature, "T001", delegation_id="fix-2", expected_revision=6
    )
    assert second["round"] == 2
    _open(repo, feature, 7, level="DELTA", scope=DELTA_2)
    exhausted = _record(repo, feature, 8, "CHANGES_REQUIRED", safety=True)
    assert exhausted["status"] == "REVIEW_BUDGET_EXHAUSTED"

    with pytest.raises(review.ReviewLedgerError, match="cannot open correction"):
        review.open_correction(
            repo, feature, "T001", delegation_id="fix-3", expected_revision=9
        )
    with pytest.raises(review.ReviewLedgerError, match="BACKLOG is forbidden"):
        review.decide_exhausted(
            repo,
            feature,
            "T001",
            decision="BACKLOG",
            reason="defer it",
            expected_revision=9,
        )
    result = review.decide_exhausted(
        repo,
        feature,
        "T001",
        decision="ESCALATE",
        reason="money safety remains unresolved",
        expected_revision=9,
    )
    assert result["status"] == "ESCALATED"


def test_delta_pass_requires_new_candidate_before_release(tmp_path: Path) -> None:
    repo, feature = _feature(tmp_path)
    _init(repo, feature)
    _open(repo, feature, 1, level="RELEASE", scope=SCOPE_A)
    _record(repo, feature, 2, "CHANGES_REQUIRED", medium=1)
    review.open_correction(
        repo, feature, "T001", delegation_id="fix-1", expected_revision=3
    )
    _open(repo, feature, 4, level="DELTA", scope=DELTA_1)
    accepted = _record(repo, feature, 5, "PASS")
    assert accepted["status"] == "CANDIDATE_UPDATE_REQUIRED"

    with pytest.raises(review.ReviewLedgerError, match="cannot open review"):
        _open(repo, feature, 6, level="RELEASE", scope=SCOPE_A)
    with pytest.raises(review.ReviewLedgerError, match="must differ"):
        review.update_candidate(
            repo, feature, "T001", scope=SCOPE_A, expected_revision=6
        )
    review.update_candidate(repo, feature, "T001", scope=SCOPE_B, expected_revision=6)
    _open(repo, feature, 7, level="RELEASE", scope=SCOPE_B)
    assert _record(repo, feature, 8, "PASS")["status"] == "RELEASE_PASSED"


def test_invariant_and_reviewer_identity_are_enforced(tmp_path: Path) -> None:
    repo, feature = _feature(tmp_path)
    _init(repo, feature)

    with pytest.raises(review.ReviewLedgerError, match="differ from writer"):
        review.open_review(
            repo,
            feature,
            "T001",
            level="RELEASE",
            scope=SCOPE_A,
            reviewer_session="agy-project",
            delegation_id="self-review",
            invariant="",
            expected_revision=1,
        )
    with pytest.raises(review.ReviewLedgerError, match="declared invariant"):
        _open(
            repo,
            feature,
            1,
            level="INVARIANT",
            scope=SCOPE_A,
            invariant="invented invariant",
        )
    opened = _open(
        repo,
        feature,
        1,
        level="INVARIANT",
        scope=SCOPE_A,
        invariant="at most two corrections",
    )
    assert opened["invariant"] == "at most two corrections"


def test_revision_cas_and_external_drift_fail_closed(tmp_path: Path) -> None:
    repo, feature = _feature(tmp_path)
    _init(repo, feature)

    with pytest.raises(review.ReviewLedgerError, match="revision mismatch"):
        _open(repo, feature, 0, level="RELEASE", scope=SCOPE_A)

    ledger_path = feature / "review-ledger.json"
    original = ledger_path.read_text(encoding="utf-8")
    payload = json.loads(original)
    payload["unknown"] = True
    ledger_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(review.ReviewLedgerError, match="root fields"):
        review.review_status(repo, feature)


def test_semantically_corrupt_ledger_fails_closed(tmp_path: Path) -> None:
    repo, feature = _feature(tmp_path)
    _init(repo, feature)
    ledger_path = feature / "review-ledger.json"
    payload = json.loads(ledger_path.read_text(encoding="utf-8"))
    payload["tasks"]["T001"]["status"] = "REVIEW_OPEN"
    ledger_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(review.ReviewLedgerError, match="active review status mismatch"):
        review.review_status(repo, feature, "T001")


def test_lock_contention_fails_without_writing(tmp_path: Path) -> None:
    repo, feature_path = _feature(tmp_path)
    feature = review.load_feature(repo, feature_path)

    with review._ledger_lock(feature):
        with pytest.raises(review.ReviewLedgerError, match="another review ledger"):
            _init(repo, feature_path)

    assert not (feature_path / "review-ledger.json").exists()


def test_atomic_replace_failure_preserves_previous_ledger(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo, feature = _feature(tmp_path)
    _init(repo, feature)
    ledger = feature / "review-ledger.json"
    previous = ledger.read_bytes()

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(review.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated replace failure"):
        _open(repo, feature, 1, level="RELEASE", scope=SCOPE_A)

    assert ledger.read_bytes() == previous
    assert not list(feature.glob(".review-ledger.json.*.tmp"))


def test_mutation_budget_requires_explicit_reasoned_expansion(tmp_path: Path) -> None:
    repo, feature = _feature(tmp_path)
    _init(repo, feature)
    expanded = review.expand_mutation_budget(
        repo,
        feature,
        "T001",
        new_budget=2,
        reason="new rollback failure mode is not covered",
        expected_revision=1,
    )
    assert expanded["mutation_budget"] == 2
    _open(repo, feature, 2, level="RELEASE", scope=SCOPE_A)
    (feature / "budget-review.md").write_text("bounded review\n", encoding="utf-8")
    result = review.record_review(
        repo,
        feature,
        "T001",
        verdict="PASS",
        evidence_file=feature / "budget-review.md",
        blocking_high=0,
        blocking_medium=0,
        invalidates_safety=False,
        mutations_run=2,
        expected_revision=3,
    )
    assert result["status"] == "RELEASE_PASSED"


def test_duplicate_review_and_mutation_overflow_preserve_revision(tmp_path: Path) -> None:
    repo, feature = _feature(tmp_path)
    _init(repo, feature)
    _open(repo, feature, 1, level="INVARIANT", scope=SCOPE_A, invariant="at most two corrections")
    _record(repo, feature, 2, "PASS")

    with pytest.raises(review.ReviewLedgerError, match="already recorded"):
        _open(
            repo,
            feature,
            3,
            level="INVARIANT",
            scope=SCOPE_A,
            invariant="at most two corrections",
        )
    assert review.review_status(repo, feature, "T001")["revision"] == 3

    _open(repo, feature, 3, level="RELEASE", scope=SCOPE_A)
    (feature / "overflow-review.md").write_text("review\n", encoding="utf-8")
    with pytest.raises(review.ReviewLedgerError, match="exceed frozen budget"):
        review.record_review(
            repo,
            feature,
            "T001",
            verdict="PASS",
            evidence_file=feature / "overflow-review.md",
            blocking_high=0,
            blocking_medium=0,
            invalidates_safety=False,
            mutations_run=2,
            expected_revision=4,
        )
    assert review.review_status(repo, feature, "T001")["revision"] == 4


def test_replan_starts_new_cycle_without_losing_event_history(tmp_path: Path) -> None:
    repo, feature = _feature(tmp_path)
    _init(repo, feature)
    _open(repo, feature, 1, level="RELEASE", scope=SCOPE_A)
    _record(repo, feature, 2, "CHANGES_REQUIRED", medium=1)
    review.open_correction(
        repo, feature, "T001", delegation_id="fix-1", expected_revision=3
    )
    _open(repo, feature, 4, level="DELTA", scope=DELTA_1)
    _record(repo, feature, 5, "CHANGES_REQUIRED", medium=1)
    review.open_correction(
        repo, feature, "T001", delegation_id="fix-2", expected_revision=6
    )
    _open(repo, feature, 7, level="DELTA", scope=DELTA_2)
    _record(repo, feature, 8, "CHANGES_REQUIRED", medium=1)
    review.decide_exhausted(
        repo,
        feature,
        "T001",
        decision="REPLAN",
        reason="task boundary is wrong",
        expected_revision=9,
    )
    before = review.review_status(repo, feature, "T001")

    restarted = review.initialize_task(
        repo,
        feature,
        "T001",
        scope=SCOPE_C,
        writer_session="agy-project",
        invariants=["new bounded invariant"],
        mutation_budget=1,
        expected_revision=10,
    )

    after = review.review_status(repo, feature, "T001")
    assert restarted["status"] == "READY_FOR_REVIEW"
    assert after["cycle"] == 2
    assert after["correction_round"] == 0
    assert len(after["events"]) == len(before["events"]) + 1


def test_replan_can_stop_after_initial_failed_review(tmp_path: Path) -> None:
    repo, feature = _feature(tmp_path)
    _init(repo, feature)
    _open(repo, feature, 1, level="RELEASE", scope=SCOPE_A)
    _record(repo, feature, 2, "CHANGES_REQUIRED", medium=1)

    result = review.decide_exhausted(
        repo,
        feature,
        "T001",
        decision="REPLAN",
        reason="the task boundary invalidates the acceptance model",
        expected_revision=3,
    )

    assert result["status"] == "REPLAN_REQUIRED"
    assert review.review_status(repo, feature, "T001")["correction_round"] == 0


def test_evidence_must_be_a_real_feature_report(tmp_path: Path) -> None:
    repo, feature = _feature(tmp_path)
    _init(repo, feature)
    _open(repo, feature, 1, level="RELEASE", scope=SCOPE_A)

    outside = tmp_path / "outside-review.md"
    outside.write_text("not bounded to the feature\n", encoding="utf-8")
    with pytest.raises(review.ReviewLedgerError, match="inside the feature"):
        review.record_review(
            repo,
            feature,
            "T001",
            verdict="PASS",
            evidence_file=outside,
            blocking_high=0,
            blocking_medium=0,
            invalidates_safety=False,
            mutations_run=0,
            expected_revision=2,
        )


def test_evidence_rejects_symlinked_path_components(tmp_path: Path) -> None:
    repo, feature = _feature(tmp_path)
    _init(repo, feature)
    _open(repo, feature, 1, level="RELEASE", scope=SCOPE_A)
    reports = feature / "reports"
    reports.mkdir()
    (reports / "review.md").write_text("review\n", encoding="utf-8")
    (feature / "linked-reports").symlink_to(reports, target_is_directory=True)

    with pytest.raises(review.ReviewLedgerError, match="must not contain symlinks"):
        review.record_review(
            repo,
            feature,
            "T001",
            verdict="PASS",
            evidence_file=feature / "linked-reports" / "review.md",
            blocking_high=0,
            blocking_medium=0,
            invalidates_safety=False,
            mutations_run=0,
            expected_revision=2,
        )


def test_mesh_cli_executes_release_pass_transaction_end_to_end(tmp_path: Path) -> None:
    repo, feature = _feature(tmp_path)
    report = feature / "release-review.md"
    report.write_text("No findings.\nREVIEW_VERDICT: PASS\n", encoding="utf-8")
    env = {**os.environ, "MESH_SPECKIT_PYTHON": sys.executable}

    commands = [
        [
            "init",
            str(repo),
            str(feature),
            "T001",
            "--scope",
            SCOPE_A,
            "--writer-session",
            "agy-project",
            "--invariant",
            "release requires RELEASE PASS",
            "--expect-revision",
            "0",
            "--json",
        ],
        [
            "open",
            str(repo),
            str(feature),
            "T001",
            "--level",
            "RELEASE",
            "--scope",
            SCOPE_A,
            "--reviewer-session",
            "codex-project",
            "--delegation-id",
            "review-release-1",
            "--expect-revision",
            "1",
            "--json",
        ],
        [
            "record",
            str(repo),
            str(feature),
            "T001",
            "--verdict",
            "PASS",
            "--evidence-file",
            str(report),
            "--mutations-run",
            "1",
            "--expect-revision",
            "2",
            "--json",
        ],
        ["status", str(repo), str(feature), "T001", "--json"],
    ]
    outputs: list[dict] = []
    for command in commands:
        proc = subprocess.run(
            ["bash", str(MESH), "speckit", "review", *command],
            cwd=ROOT,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0, proc.stderr
        outputs.append(json.loads(proc.stdout))

    assert [item["revision"] for item in outputs] == [1, 2, 3, 3]
    assert outputs[-1]["status"] == "RELEASE_PASSED"
    assert outputs[-1]["events"][-1]["data"]["evidence"]["path"] == (
        "release-review.md"
    )

    check = subprocess.run(
        [
            "bash",
            str(MESH),
            "speckit",
            "review",
            "check",
            str(repo),
            str(feature),
            "T001",
            "--json",
        ],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert check.returncode == 0, check.stderr
    assert json.loads(check.stdout)["release_passed"] is True


def test_check_exit_codes_distinguish_unsatisfied_and_invalid(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    repo, feature = _feature(tmp_path)
    _init(repo, feature)

    assert review.main(["check", str(repo), str(feature), "T001", "--json"]) == 1
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "READY_FOR_REVIEW"
    assert output["release_passed"] is False

    assert review.main(["check", str(repo), str(feature), "T999", "--json"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "not initialized" in captured.err
