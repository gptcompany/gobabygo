"""Tests for the transactional Spec Kit review ledger."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

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


def _local_init(repo: Path, feature: Path, task="T001", revision=0):
    return review.initialize_task(
        repo, feature, task, scope=SCOPE_A, writer_session="codex-fixture",
        invariants=["release evidence"], mutation_budget=1,
        expected_revision=revision, local=True,
    )


def test_local_review_uses_existing_ledger_without_github(tmp_path):
    repo, feature = _feature(tmp_path)
    (feature / "github-ledger.json").unlink()
    with pytest.raises(ValueError, match="identity missing"):
        review.review_status(repo, feature)
    assert not (feature / "review-ledger.json").exists()
    _local_init(repo, feature)
    status = review.review_status(repo, feature, "T001")
    ledger = json.loads((feature / "review-ledger.json").read_text())
    key = ledger["feature_key"]
    assert key == "local:" + str(uuid.UUID(key.removeprefix("local:")))
    assert ledger["tasks"]["T001"]["task_key"] == key + ":T001"
    assert status["revision"] == 1
    before = (feature / "review-ledger.json").read_bytes()
    with pytest.raises(ValueError, match="already exists"):
        _local_init(repo, feature, revision=1)
    assert (feature / "review-ledger.json").read_bytes() == before
    _local_init(repo, feature, "T002", 1)
    assert review.review_status(repo, feature)["feature_key"] == key
    assert not (feature / "github-ledger.json").exists()


def test_local_identity_survives_move_and_remote_addition(tmp_path):
    repo, feature = _feature(tmp_path)
    (feature / "github-ledger.json").unlink()
    _local_init(repo, feature)
    before = (feature / "review-ledger.json").read_bytes()
    relative = feature.relative_to(repo)
    moved = tmp_path / "moved"
    repo.rename(moved)
    subprocess.run(["git", "-C", str(moved), "remote", "add", "origin",
                    "https://github.com/example/project.git"], check=True)
    assert review.review_status(moved, moved / relative)["revision"] == 1
    assert (moved / relative / "review-ledger.json").read_bytes() == before


def test_missing_github_binding_cannot_reset_existing_review_as_local(tmp_path):
    repo, feature = _feature(tmp_path)
    _init(repo, feature)
    before = (feature / "review-ledger.json").read_bytes()
    (feature / "github-ledger.json").unlink()
    with pytest.raises(ValueError, match="restore its GitHub binding"):
        _local_init(repo, feature)
    assert (feature / "review-ledger.json").read_bytes() == before


def test_local_review_blocks_conflicting_publication_and_stale_plan(tmp_path):
    from scripts import mesh_speckit_github as github
    repo, feature = _feature(tmp_path)
    binding = (feature / "github-ledger.json").read_bytes()
    (feature / "github-ledger.json").unlink()
    plan = github.build_binding_plan(repo, feature, repository="example/project")
    _local_init(repo, feature)
    with pytest.raises(ValueError, match="explicit GitHub migration"):
        github.apply_binding_plan(plan)
    with pytest.raises(ValueError, match="explicit GitHub migration"):
        github.build_binding_plan(repo, feature, repository="example/project")
    (feature / "github-ledger.json").write_bytes(binding)
    with pytest.raises(ValueError, match="conflicting local/GitHub"):
        review.review_status(repo, feature)
    with pytest.raises(ValueError, match="explicit GitHub migration"):
        github.load_feature(repo, feature)


def test_local_initialization_never_replaces_a_github_binding(tmp_path):
    repo, feature = _feature(tmp_path)
    with pytest.raises(ValueError, match="conflicting local/GitHub"):
        _local_init(repo, feature)
    assert not (feature / "review-ledger.json").exists()


def test_competing_local_identity_cannot_overwrite_first_commit(tmp_path):
    repo, feature = _feature(tmp_path)
    (feature / "github-ledger.json").unlink()
    competing = review.load_feature(repo, feature, initialize_local=True)
    _local_init(repo, feature)
    before = (feature / "review-ledger.json").read_bytes()
    with pytest.raises(ValueError, match="feature"):
        review._transact(competing, 0, lambda ledger, revision: {})
    assert (feature / "review-ledger.json").read_bytes() == before


@pytest.mark.parametrize("damage", ["bad-uuid", "symlink", "invalid-json"])
def test_local_identity_damage_fails_closed(tmp_path, damage):
    repo, feature = _feature(tmp_path)
    (feature / "github-ledger.json").unlink()
    _local_init(repo, feature)
    path = feature / "review-ledger.json"
    if damage == "symlink":
        saved = repo / "saved.json"
        path.rename(saved)
        path.symlink_to(saved)
    elif damage == "invalid-json":
        path.write_text("{")
    else:
        payload = json.loads(path.read_text())
        payload["feature_key"] = "local:not-a-uuid"
        path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        review.review_status(repo, feature)


def test_local_init_cli(tmp_path, capsys):
    repo, feature = _feature(tmp_path)
    (feature / "github-ledger.json").unlink()
    assert review.main(["init", str(repo), str(feature), "T001", "--local",
                        "--scope", SCOPE_A, "--writer-session", "codex-fixture",
                        "--expect-revision", "0", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["revision"] == 1


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


def _ack(repo: Path, feature: Path, revision: int) -> dict:
    active = review.review_status(repo, feature, "T001")["active_review"]
    evidence = feature / "ack.md"
    evidence.write_text("Reviewer accepted the immutable review scope.\n")
    return review.acknowledge_review(
        repo, feature, "T001", reviewer_session=active["reviewer"],
        delegation_id=active["delegation_id"], evidence_file=evidence,
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
        reviewer_session="codex-project",
        delegation_id=review.review_status(repo, feature, "T001")["active_review"]["delegation_id"],
        blocking_high=high,
        blocking_medium=medium,
        invalidates_safety=safety,
        mutations_run=1,
        expected_revision=revision,
    )


@pytest.mark.parametrize("local", [False, True])
def test_release_pass_is_terminal_and_durable(tmp_path: Path, local: bool) -> None:
    repo, feature = _feature(tmp_path)
    if local:
        (feature / "github-ledger.json").unlink()
        assert _local_init(repo, feature)["revision"] == 1
    else:
        assert _init(repo, feature)["revision"] == 1
    assert _open(repo, feature, 1, level="RELEASE", scope=SCOPE_A)["revision"] == 2

    _ack(repo, feature, 2)
    result = _record(repo, feature, 3, "PASS")

    assert result["status"] == "RELEASE_PASSED"
    state = review.review_status(repo, feature, "T001")
    assert state["revision"] == 4
    assert state["status"] == "RELEASE_PASSED"
    assert state["correction_round"] == 0
    evidence = state["events"][-1]["data"]["evidence"]
    assert evidence["path"] == "review-3.md"
    assert len(evidence["sha256"]) == 64
    assert (feature / "review-ledger.json").is_file()
    assert review.review_check(repo, feature, "T001", scope=SCOPE_A)["release_passed"] is True
    review.complete_task(repo, feature, "T001", scope=SCOPE_A, expected_revision=4)
    assert review.review_status(repo, feature, "T001")["completed"] is True


def test_binding_and_local_initialization_share_lock(tmp_path):
    from scripts import mesh_speckit_github as github
    repo, feature = _feature(tmp_path)
    (feature / "github-ledger.json").unlink()
    plan = github.build_binding_plan(repo, feature, repository="example/project")
    with github.review_feature_lock(repo, feature):
        with pytest.raises(ValueError, match="another review ledger transaction"):
            github.apply_binding_plan(plan)
        with pytest.raises(ValueError, match="another review ledger transaction"):
            _local_init(repo, feature)
    assert not (feature / "review-ledger.json").exists()
    assert not (feature / "github-ledger.json").exists()


def test_binding_appearing_after_local_load_is_rejected(tmp_path):
    from scripts import mesh_speckit_github as github
    repo, feature = _feature(tmp_path)
    (feature / "github-ledger.json").unlink()
    pending = review.load_feature(repo, feature, initialize_local=True)
    github.apply_binding_plan(github.build_binding_plan(repo, feature, repository="example/project"))
    with pytest.raises(ValueError, match="conflicting local/GitHub"):
        review._transact(pending, 0, lambda ledger, revision: {})
    assert not (feature / "review-ledger.json").exists()


def test_legacy_aliases_share_one_canonical_review_cycle(tmp_path: Path) -> None:
    repo, feature = _feature(tmp_path)
    _init(repo, feature)
    report = feature / "mapping.md"
    report.write_text("T003x6-J and T003x6-L1d belong to T001; history is evidence only.\n")
    for revision, alias in enumerate(("T003x6-J", "T003x6-L1d"), 1):
        review.register_alias(repo, feature, "T001", alias=alias,
                              evidence_file=report, expected_revision=revision)
    for alias in ("T001", "T003x6-J", "t003X6-l1D"):
        assert review.resolve_task(repo, feature, alias)["task"] == "T001"
    state = review.review_status(repo, feature, "T001")
    assert state["cycle"] == 1
    assert state["aliases"] == ["T003X6-J", "T003X6-L1D"]
    assert state["total_correction_rounds"] == 0
    with pytest.raises(review.ReviewLedgerError, match="already mapped"):
        review.register_alias(repo, feature, "T001", alias="t003x6-j",
                              evidence_file=report, expected_revision=3)
    with pytest.raises(review.ReviewLedgerError, match="canonical"):
        review.register_alias(repo, feature, "T001", alias="T002",
                              evidence_file=report, expected_revision=3)
    with pytest.raises(review.ReviewLedgerError, match="not mapped"):
        review.resolve_task(repo, feature, "T003x6-unknown")
    with pytest.raises(review.ReviewLedgerError, match="128"):
        review.resolve_task(repo, feature, "T" + "1" * 200 + "J")
    assert review.review_status(repo, feature, "T001")["revision"] == 3


def test_alias_cannot_move_to_another_task(tmp_path: Path) -> None:
    repo, feature = _feature(tmp_path)
    _init(repo, feature)
    report = feature / "mapping.md"
    report.write_text("Legacy task belongs to T001.\n")
    review.register_alias(repo, feature, "T001", alias="T003x6-J",
                          evidence_file=report, expected_revision=1)
    review.initialize_task(repo, feature, "T002", scope=SCOPE_B,
                           writer_session="agy-project", invariants=[],
                           mutation_budget=1, expected_revision=2)
    with pytest.raises(review.ReviewLedgerError, match="already mapped"):
        review.register_alias(repo, feature, "T002", alias="T003x6-J",
                              evidence_file=report, expected_revision=3)
    assert review.resolve_task(repo, feature, "T003x6-J")["task"] == "T001"


def test_dispatch_is_reserved_before_delivery_and_never_replayed(tmp_path: Path, monkeypatch) -> None:
    repo, feature = _feature(tmp_path)
    _init(repo, feature)
    _open(repo, feature, 1, level="RELEASE", scope=SCOPE_A)
    _ack(repo, feature, 2)
    _record(repo, feature, 3, "CHANGES_REQUIRED", medium=1)
    review.open_correction(repo, feature, "T001", delegation_id="fix-1", expected_revision=4)
    calls = []

    def deliver(message, delegation):
        state = json.loads((feature / "review-ledger.json").read_text())
        assert state["tasks"]["T001"]["events"][-1]["type"] == "correction_dispatch_attempted"
        with pytest.raises(review.ReviewLedgerError, match="transaction is active"):
            review.open_review(repo, feature, "T001", level="DELTA", scope=DELTA_1,
                               reviewer_session="codex-project", delegation_id="review-new",
                               invariant="", expected_revision=6)
        calls.append((message, delegation))
        raise TimeoutError("response lost after delivery")

    monkeypatch.setattr(review, "_prepare_local_delivery", lambda *_: (
        {"owner": "test", "name": "agy-project", "pane_id": "%9", "pane_pid": 42}, deliver))
    args = dict(delegation_id="fix-1", message="fix-1 example/project:review-ledger-001:T001 fix it",
                worker_repo=repo, expected_revision=5)
    result = review.dispatch_correction(repo, feature, "T001", **args)
    assert result["submission"] == "unknown"
    assert result["delivery_error"] == "response lost after delivery"
    assert result["revision"] == 7
    with pytest.raises(review.ReviewLedgerError, match="already attempted"):
        review.dispatch_correction(repo, feature, "T001", **{**args, "expected_revision": 7})
    assert len(calls) == 1
    with pytest.raises(review.ReviewLedgerError, match="delegation"):
        review.dispatch_correction(repo, feature, "T001", **{
            **args, "delegation_id": "other", "expected_revision": 7})


def test_completion_requires_exact_release_and_intact_evidence(tmp_path: Path) -> None:
    repo, feature = _feature(tmp_path)
    _init(repo, feature)
    before = (feature / "tasks.md").read_bytes()
    with pytest.raises(review.ReviewLedgerError, match="RELEASE_PASSED"):
        review.complete_task(repo, feature, "T001", scope=SCOPE_A, expected_revision=1)
    assert (feature / "tasks.md").read_bytes() == before
    _open(repo, feature, 1, level="RELEASE", scope=SCOPE_A)
    _ack(repo, feature, 2)
    _record(repo, feature, 3, "PASS")
    with pytest.raises(review.ReviewLedgerError, match="scope"):
        review.complete_task(repo, feature, "T001", scope=SCOPE_B, expected_revision=4)
    evidence = feature / "review-3.md"
    content = evidence.read_text()
    evidence.write_text("tampered report")
    with pytest.raises(review.ReviewLedgerError, match="evidence"):
        review.complete_task(repo, feature, "T001", scope=SCOPE_A, expected_revision=4)
    evidence.write_text(content)
    result = review.complete_task(repo, feature, "T001", scope=SCOPE_A, expected_revision=4)
    assert result["completed"] is True
    assert (feature / "tasks.md").read_text().startswith("- [x] T001")
    assert "- [ ] T002" in (feature / "tasks.md").read_text()
    assert review.complete_task(repo, feature, "T001", scope=SCOPE_A, expected_revision=4)["completed"] is True
    assert review.review_status(repo, feature, "T001")["completed"] is True


@pytest.mark.parametrize("failure", ["persist", "crash", "unexpected", "success"])
def test_dispatch_failure_boundaries(tmp_path: Path, monkeypatch, failure: str) -> None:
    repo, feature = _feature(tmp_path)
    _init(repo, feature)
    _open(repo, feature, 1, level="RELEASE", scope=SCOPE_A)
    _ack(repo, feature, 2)
    _record(repo, feature, 3, "CHANGES_REQUIRED", medium=1)
    review.open_correction(repo, feature, "T001", delegation_id="fix-1", expected_revision=4)
    calls = []

    def deliver(*_):
        calls.append(1)
        if failure == "crash":
            raise SystemExit("simulated process death after reservation")
        if failure == "unexpected":
            raise TypeError("internal transport error after input")
        return {"text_sent": True, "enter_sent": True, "submission": "verified"}

    monkeypatch.setattr(review, "_prepare_local_delivery", lambda *_: (
        {"owner": "test", "name": "agy-project", "pane_id": "%9", "pane_pid": 42}, deliver))
    args = dict(delegation_id="fix-1", message="fix-1 example/project:review-ledger-001:T001 fix it",
                worker_repo=repo, expected_revision=5)
    if failure == "persist":
        def fail(*_):
            raise OSError("disk full")
        monkeypatch.setattr(review, "_atomic_write", fail)
        with pytest.raises(OSError):
            review.dispatch_correction(repo, feature, "T001", **args)
        assert calls == []
        assert review.review_status(repo, feature, "T001")["revision"] == 5
    else:
        if failure == "crash":
            with pytest.raises(SystemExit):
                review.dispatch_correction(repo, feature, "T001", **args)
        else:
            result = review.dispatch_correction(repo, feature, "T001", **args)
            assert result["submission"] == ("unknown" if failure == "unexpected" else "verified")
        state = review.review_status(repo, feature, "T001")
        assert state["last_dispatch"]["receipt_recorded"] is (failure != "crash")
        assert state["last_dispatch"]["submission"] == ("verified" if failure == "success" else "unknown")
        with pytest.raises(review.ReviewLedgerError, match="already attempted"):
            review.dispatch_correction(repo, feature, "T001", **{
                **args, "expected_revision": state["revision"]})
        assert calls == [1]


def test_pass_rejects_blocking_findings_without_mutation(tmp_path: Path) -> None:
    repo, feature = _feature(tmp_path)
    _init(repo, feature)
    _open(repo, feature, 1, level="RELEASE", scope=SCOPE_A)

    _ack(repo, feature, 2)
    with pytest.raises(review.ReviewLedgerError, match="PASS is forbidden"):
        _record(repo, feature, 3, "PASS", high=1)

    assert review.review_status(repo, feature, "T001")["revision"] == 3


def test_two_corrections_then_escalation_and_no_third_round(tmp_path: Path) -> None:
    repo, feature = _feature(tmp_path)
    _init(repo, feature)
    _open(repo, feature, 1, level="RELEASE", scope=SCOPE_A)
    _ack(repo, feature, 2)
    assert _record(repo, feature, 3, "CHANGES_REQUIRED", high=1)["status"] == "CHANGES_REQUIRED"

    first = review.open_correction(
        repo, feature, "T001", delegation_id="fix-1", expected_revision=4
    )
    assert first["round"] == 1
    _open(repo, feature, 5, level="DELTA", scope=DELTA_1)
    _ack(repo, feature, 6)
    _record(repo, feature, 7, "CHANGES_REQUIRED", medium=1)
    second = review.open_correction(
        repo, feature, "T001", delegation_id="fix-2", expected_revision=8
    )
    assert second["round"] == 2
    _open(repo, feature, 9, level="DELTA", scope=DELTA_2)
    _ack(repo, feature, 10)
    exhausted = _record(repo, feature, 11, "CHANGES_REQUIRED", safety=True)
    assert exhausted["status"] == "REVIEW_BUDGET_EXHAUSTED"

    with pytest.raises(review.ReviewLedgerError, match="cannot open correction"):
        review.open_correction(
            repo, feature, "T001", delegation_id="fix-3", expected_revision=12
        )
    with pytest.raises(review.ReviewLedgerError, match="BACKLOG is forbidden"):
        review.decide_exhausted(
            repo,
            feature,
            "T001",
            decision="BACKLOG",
            reason="defer it",
            expected_revision=12,
        )
    result = review.decide_exhausted(
        repo,
        feature,
        "T001",
        decision="ESCALATE",
        reason="money safety remains unresolved",
        expected_revision=12,
    )
    assert result["status"] == "ESCALATED"


def test_delta_pass_requires_new_candidate_before_release(tmp_path: Path) -> None:
    repo, feature = _feature(tmp_path)
    _init(repo, feature)
    _open(repo, feature, 1, level="RELEASE", scope=SCOPE_A)
    _ack(repo, feature, 2)
    _record(repo, feature, 3, "CHANGES_REQUIRED", medium=1)
    review.open_correction(
        repo, feature, "T001", delegation_id="fix-1", expected_revision=4
    )
    _open(repo, feature, 5, level="DELTA", scope=DELTA_1)
    _ack(repo, feature, 6)
    accepted = _record(repo, feature, 7, "PASS")
    assert accepted["status"] == "CANDIDATE_UPDATE_REQUIRED"

    with pytest.raises(review.ReviewLedgerError, match="cannot open review"):
        _open(repo, feature, 8, level="RELEASE", scope=SCOPE_A)
    with pytest.raises(review.ReviewLedgerError, match="must differ"):
        review.update_candidate(
            repo, feature, "T001", scope=SCOPE_A, expected_revision=8
        )
    review.update_candidate(repo, feature, "T001", scope=SCOPE_B, expected_revision=8)
    _open(repo, feature, 9, level="RELEASE", scope=SCOPE_B)
    _ack(repo, feature, 10)
    assert _record(repo, feature, 11, "PASS")["status"] == "RELEASE_PASSED"


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


def test_review_timeout_allows_one_different_fallback_then_escalates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo, feature = _feature(tmp_path)
    _init(repo, feature)
    opened = _open(repo, feature, 1, level="RELEASE", scope=SCOPE_A)
    assert opened["fallback_attempt"] == 0
    assert opened["deadline_at"] > opened["opened_at"]
    status = review.review_status(repo, feature, "T001")
    assert status["review_ack_deadline_at"] == opened["deadline_at"]

    with pytest.raises(review.ReviewLedgerError, match="deadline has not elapsed"):
        review.timeout_review(repo, feature, "T001", expected_revision=2)
    assert review.review_status(repo, feature, "T001")["revision"] == 2

    monkeypatch.setattr(review, "_now", lambda: "2100-01-01T00:00:00+00:00")
    timed_out = review.timeout_review(repo, feature, "T001", expected_revision=2)
    assert timed_out["status"] == "READY_FOR_REVIEW"
    assert timed_out["fallback_allowed"] is True

    with pytest.raises(review.ReviewLedgerError, match="different session"):
        _open(repo, feature, 3, level="RELEASE", scope=SCOPE_A)

    fallback = review.open_review(
        repo,
        feature,
        "T001",
        level="RELEASE",
        scope=SCOPE_A,
        reviewer_session="codex-project-alt",
        delegation_id="review-fallback",
        invariant="",
        expected_revision=3,
    )
    assert fallback["fallback_attempt"] == 1

    monkeypatch.setattr(review, "_now", lambda: "2200-01-01T00:00:00+00:00")
    exhausted = review.timeout_review(repo, feature, "T001", expected_revision=4)
    assert exhausted["status"] == "ESCALATED"
    assert exhausted["fallback_allowed"] is False
    assert review.review_check(repo, feature, "T001", scope=SCOPE_A)["release_passed"] is False


@pytest.mark.parametrize("first,second", [("abandon", "abandon"), ("abandon", "timeout"), ("timeout", "abandon")])
@pytest.mark.parametrize("level", ["RELEASE", "DELTA"])
def test_abandon_shares_timeout_budget(monkeypatch, tmp_path, first, second, level):
    repo, feature = _feature(tmp_path)
    _init(repo, feature)
    revision = 1
    scope = SCOPE_A
    if level == "DELTA":
        _open(repo, feature, 1, level="RELEASE", scope=SCOPE_A)
        _ack(repo, feature, 2)
        _record(repo, feature, 3, "CHANGES_REQUIRED", medium=1)
        review.open_correction(repo, feature, "T001", delegation_id="fix-1", expected_revision=4)
        revision, scope = 5, DELTA_1
    opened = _open(repo, feature, revision, level=level, scope=scope)
    report = feature / "abandon.md"
    payload = "Redacted diagnostic: reviewer unavailable; credentials [REDACTED]."
    report.write_text(payload)

    def fail(kind, expected):
        if kind == "timeout":
            return review.timeout_review(repo, feature, "T001", expected_revision=expected)
        return review.abandon_review(
            repo, feature, "T001", reason="AUTH_SESSION_INVALID",
            evidence_file=report, expected_revision=expected,
        )

    monkeypatch.setattr(review, "_now", lambda: "2100-01-01T00:00:00+00:00")
    result = fail(first, revision + 1)
    assert result["status"] == ("CORRECTION_OPEN" if level == "DELTA" else "READY_FOR_REVIEW")
    assert result["fallback_allowed"] is True
    assert result["fallback_attempt"] == 0
    ledger = json.loads((feature / "review-ledger.json").read_text())
    assert ledger["tasks"]["T001"]["active_review"] is None
    if first == "abandon":
        event = ledger["tasks"]["T001"]["events"][-1]
        assert event["type"] == "review_abandoned"
        assert event["data"] == {
            **{key: opened[key] for key in review._ACTIVE_REVIEW_FIELDS},
            "reason": "AUTH_SESSION_INVALID",
            "evidence": {"path": "abandon.md", "sha256": hashlib.sha256(payload.encode()).hexdigest()},
            "opened_at": opened["opened_at"], "fallback_attempt": 0,
        }
        assert payload not in json.dumps(ledger)
    before = (feature / "review-ledger.json").read_bytes()
    with pytest.raises(review.ReviewLedgerError, match="different session"):
        _open(repo, feature, revision + 2, level=level, scope=scope)
    assert (feature / "review-ledger.json").read_bytes() == before
    fallback = review.open_review(
        repo, feature, "T001", level=level, scope=scope,
        reviewer_session="codex-project-alt", delegation_id="fallback",
        invariant="", expected_revision=revision + 2,
    )
    assert fallback["fallback_attempt"] == 1
    monkeypatch.setattr(review, "_now", lambda: "2200-01-01T00:00:00+00:00")
    result = fail(second, revision + 3)
    assert result["status"] == "ESCALATED"
    assert result["fallback_allowed"] is False
    assert result["fallback_attempt"] == 1


@pytest.mark.parametrize("failure", ["invalid", "lowercase", "revision", "missing", "symlink", "outside", "directory", "status", "active"])
def test_abandon_fails_without_mutation(tmp_path, failure):
    repo, feature = _feature(tmp_path)
    _init(repo, feature)
    _open(repo, feature, 1, level="RELEASE", scope=SCOPE_A)
    evidence = feature / "abandon.md"
    evidence.write_text("Redacted evidence")
    reason, revision = "DELIVERY_UNVERIFIED", 2
    if failure == "invalid":
        reason = "arbitrary secret text"
    elif failure == "lowercase":
        reason = "delivery_unverified"
    elif failure == "revision":
        revision = 1
    elif failure == "missing":
        evidence = feature / "missing.md"
    elif failure == "symlink":
        link = feature / "link.md"
        link.symlink_to(evidence)
        evidence = link
    elif failure == "outside":
        evidence = repo / "outside.md"
        evidence.write_text("Evidence")
    elif failure == "directory":
        evidence = feature
    elif failure == "status":
        _ack(repo, feature, 2)
        _record(repo, feature, 3, "PASS")
        revision = 4
    elif failure == "active":
        path = feature / "review-ledger.json"
        ledger = json.loads(path.read_text())
        ledger["tasks"]["T001"]["active_review"] = None
        path.write_text(json.dumps(ledger))
    before = (feature / "review-ledger.json").read_bytes()
    with pytest.raises(review.ReviewLedgerError):
        review.abandon_review(repo, feature, "T001", reason=reason,
                              evidence_file=evidence, expected_revision=revision)
    assert (feature / "review-ledger.json").read_bytes() == before


def test_stale_reviewer_cannot_record_against_fallback_lease(tmp_path: Path) -> None:
    repo, feature = _feature(tmp_path)
    _init(repo, feature)
    review.open_review(
        repo,
        feature,
        "T001",
        level="RELEASE",
        scope=SCOPE_A,
        reviewer_session="codex-original",
        delegation_id="review-original",
        invariant="",
        expected_revision=1,
    )
    abandoned = feature / "abandoned.md"
    abandoned.write_text("Credential rotation invalidated the original reviewer.\n")
    review.abandon_review(
        repo,
        feature,
        "T001",
        reason="AUTH_SESSION_INVALID",
        evidence_file=abandoned,
        expected_revision=2,
    )
    review.open_review(
        repo,
        feature,
        "T001",
        level="RELEASE",
        scope=SCOPE_A,
        reviewer_session="codex-fallback",
        delegation_id="review-fallback",
        invariant="",
        expected_revision=3,
    )
    report = feature / "late-original-report.md"
    report.write_text("Late report from the invalidated reviewer.\n")
    ledger = feature / "review-ledger.json"
    _ack(repo, feature, 4)
    before = ledger.read_bytes()
    with pytest.raises(review.ReviewLedgerError, match="reviewer session does not match"):
        review.record_review(
            repo,
            feature,
            "T001",
            verdict="PASS",
            evidence_file=report,
            reviewer_session="codex-original",
            delegation_id="review-original",
            blocking_high=0,
            blocking_medium=0,
            invalidates_safety=False,
            mutations_run=1,
            expected_revision=5,
        )
    assert ledger.read_bytes() == before

    result = review.record_review(
        repo,
        feature,
        "T001",
        verdict="PASS",
        evidence_file=report,
        reviewer_session="codex-fallback",
        delegation_id="review-fallback",
        blocking_high=0,
        blocking_medium=0,
        invalidates_safety=False,
        mutations_run=1,
        expected_revision=5,
    )
    assert result["status"] == "RELEASE_PASSED"


@pytest.mark.parametrize("reason", sorted(review.ABANDON_REASONS))
def test_mesh_cli_abandon(tmp_path, reason):
    repo, feature = _feature(tmp_path)
    _init(repo, feature)
    _open(repo, feature, 1, level="RELEASE", scope=SCOPE_A)
    (feature / "abandon.md").write_text("Redacted evidence")
    result = subprocess.run(
        [str(MESH), "speckit", "review", "abandon", str(repo), str(feature), "T001",
         "--reason", reason, "--evidence-file", "abandon.md", "--expect-revision", "2", "--json"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["revision"] == 3
    assert output["reason"] == reason
    assert output["status"] == "READY_FOR_REVIEW"


def test_delta_timeout_preserves_open_correction(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo, feature = _feature(tmp_path)
    _init(repo, feature)
    _open(repo, feature, 1, level="RELEASE", scope=SCOPE_A)
    _ack(repo, feature, 2)
    _record(repo, feature, 3, "CHANGES_REQUIRED", medium=1)
    review.open_correction(
        repo, feature, "T001", delegation_id="fix-1", expected_revision=4
    )
    _open(repo, feature, 5, level="DELTA", scope=DELTA_1)

    monkeypatch.setattr(review, "_now", lambda: "2100-01-01T00:00:00+00:00")
    timed_out = review.timeout_review(repo, feature, "T001", expected_revision=6)

    assert timed_out["status"] == "CORRECTION_OPEN"
    assert timed_out["fallback_allowed"] is True
    assert review.review_status(repo, feature, "T001")["correction_round"] == 1


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
    _ack(repo, feature, 3)
    result = review.record_review(
        repo,
        feature,
        "T001",
        verdict="PASS",
        evidence_file=feature / "budget-review.md",
        reviewer_session="codex-project",
        delegation_id="review-2",
        blocking_high=0,
        blocking_medium=0,
        invalidates_safety=False,
        mutations_run=2,
        expected_revision=4,
    )
    assert result["status"] == "RELEASE_PASSED"


def test_duplicate_review_and_mutation_overflow_preserve_revision(tmp_path: Path) -> None:
    repo, feature = _feature(tmp_path)
    _init(repo, feature)
    _open(repo, feature, 1, level="INVARIANT", scope=SCOPE_A, invariant="at most two corrections")
    _ack(repo, feature, 2)
    _record(repo, feature, 3, "PASS")

    with pytest.raises(review.ReviewLedgerError, match="already recorded"):
        _open(
            repo,
            feature,
            4,
            level="INVARIANT",
            scope=SCOPE_A,
            invariant="at most two corrections",
        )
    assert review.review_status(repo, feature, "T001")["revision"] == 4

    _open(repo, feature, 4, level="RELEASE", scope=SCOPE_A)
    (feature / "overflow-review.md").write_text("review\n", encoding="utf-8")
    _ack(repo, feature, 5)
    with pytest.raises(review.ReviewLedgerError, match="exceed frozen budget"):
        review.record_review(
            repo,
            feature,
            "T001",
            verdict="PASS",
            evidence_file=feature / "overflow-review.md",
            reviewer_session="codex-project",
            delegation_id="review-4",
            blocking_high=0,
            blocking_medium=0,
            invalidates_safety=False,
            mutations_run=2,
            expected_revision=6,
        )
    assert review.review_status(repo, feature, "T001")["revision"] == 6


def test_replan_starts_new_cycle_without_losing_event_history(tmp_path: Path) -> None:
    repo, feature = _feature(tmp_path)
    _init(repo, feature)
    _open(repo, feature, 1, level="RELEASE", scope=SCOPE_A)
    _ack(repo, feature, 2)
    _record(repo, feature, 3, "CHANGES_REQUIRED", medium=1)
    review.open_correction(
        repo, feature, "T001", delegation_id="fix-1", expected_revision=4
    )
    _open(repo, feature, 5, level="DELTA", scope=DELTA_1)
    _ack(repo, feature, 6)
    _record(repo, feature, 7, "CHANGES_REQUIRED", medium=1)
    review.open_correction(
        repo, feature, "T001", delegation_id="fix-2", expected_revision=8
    )
    _open(repo, feature, 9, level="DELTA", scope=DELTA_2)
    _ack(repo, feature, 10)
    _record(repo, feature, 11, "CHANGES_REQUIRED", medium=1)
    review.decide_exhausted(
        repo,
        feature,
        "T001",
        decision="REPLAN",
        reason="task boundary is wrong",
        expected_revision=12,
    )
    before = review.review_status(repo, feature, "T001")

    plan = feature / "replan.json"
    plan.write_text(json.dumps({
        "task_key": before["task_key"], "previous_cycle": 1,
        "failure_evidence": "review-11.md: blocking medium remains",
        "approach_change": "Replace static inference with runtime observation",
        "acceptance_criteria": "Same-run evidence required",
        "finding_disposition": "Carry the blocking medium into cycle 2",
    }))

    restarted = review.initialize_task(
        repo,
        feature,
        "T001",
        scope=SCOPE_C,
        writer_session="agy-project",
        invariants=["new bounded invariant"],
        mutation_budget=1,
        expected_revision=13,
        replan_file=plan,
    )

    after = review.review_status(repo, feature, "T001")
    assert restarted["status"] == "READY_FOR_REVIEW"
    assert after["cycle"] == 2
    assert after["correction_round"] == 0
    assert len(after["events"]) == len(before["events"]) + 1
    assert after["last_findings"] == before["last_findings"]
    assert after["total_correction_rounds"] == 2


def test_replan_requires_bound_evidence_without_mutating_on_failure(tmp_path: Path) -> None:
    repo, feature = _feature(tmp_path)
    _init(repo, feature)
    _open(repo, feature, 1, level="RELEASE", scope=SCOPE_A)
    _ack(repo, feature, 2)
    _record(repo, feature, 3, "CHANGES_REQUIRED", medium=1)
    review.decide_exhausted(repo, feature, "T001", decision="REPLAN",
                          reason="runtime observation needed", expected_revision=4)
    before = (feature / "review-ledger.json").read_bytes()
    kwargs = dict(scope=SCOPE_A, writer_session="agy-project", invariants=[],
                  mutation_budget=1, expected_revision=5)
    with pytest.raises(review.ReviewLedgerError, match="replan artifact"):
        review.initialize_task(repo, feature, "T001", **kwargs)
    assert (feature / "review-ledger.json").read_bytes() == before
    plan = feature / "replan.json"
    payload = {
        "task_key": review.review_status(repo, feature, "T001")["task_key"],
        "previous_cycle": 0, "failure_evidence": "review-2.md",
        "approach_change": "Observe runtime execution",
        "acceptance_criteria": "Require matching run IDs",
        "finding_disposition": "Keep the medium open until independently reviewed",
    }
    plan.write_text(json.dumps(payload))
    with pytest.raises(review.ReviewLedgerError, match="previous cycle"):
        review.initialize_task(repo, feature, "T001", replan_file=plan, **kwargs)
    assert (feature / "review-ledger.json").read_bytes() == before
    payload["previous_cycle"] = 1
    for field, value in [("task_key", "other/repo:feature:T001"),
                         ("previous_cycle", True), ("approach_change", "  "),
                         ("finding_disposition", []), ("acceptance_criteria", "x" * 4097)]:
        plan.write_text(json.dumps({**payload, field: value}))
        with pytest.raises(review.ReviewLedgerError):
            review.initialize_task(repo, feature, "T001", replan_file=plan, **kwargs)
        assert (feature / "review-ledger.json").read_bytes() == before
    plan.write_text(json.dumps(payload))
    proc = subprocess.run(
        [str(MESH), "speckit", "review", "init", str(repo), str(feature), "T001",
         "--scope", SCOPE_A, "--writer-session", "agy-project",
         "--invariant", "runtime evidence",
         "--replan-file", "replan.json", "--expect-revision", "5", "--json"],
        env={**os.environ, "MESH_SPECKIT_PYTHON": sys.executable},
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    state = review.review_status(repo, feature, "T001")
    assert state["frozen_scope"] == SCOPE_A
    assert state["last_findings"]["blocking_medium"] == 1
    assert state["events"][-1]["data"]["replan"]["plan"] == payload
    with pytest.raises(review.ReviewLedgerError, match="revision mismatch"):
        review.initialize_task(repo, feature, "T001", replan_file=plan, **kwargs)
    _open(repo, feature, 6, level="INVARIANT", scope=SCOPE_A, invariant="runtime evidence")
    _ack(repo, feature, 7)
    _record(repo, feature, 8, "CHANGES_REQUIRED", medium=1)
    review.decide_exhausted(repo, feature, "T001", decision="REPLAN",
                          reason="new evidence requires another decision", expected_revision=9)
    with pytest.raises(review.ReviewLedgerError, match="previous cycle"):
        review.initialize_task(repo, feature, "T001", replan_file=plan,
                               **{**kwargs, "expected_revision": 10})


def test_replan_can_stop_after_initial_failed_review(tmp_path: Path) -> None:
    repo, feature = _feature(tmp_path)
    _init(repo, feature)
    _open(repo, feature, 1, level="RELEASE", scope=SCOPE_A)
    _ack(repo, feature, 2)
    _record(repo, feature, 3, "CHANGES_REQUIRED", medium=1)

    result = review.decide_exhausted(
        repo,
        feature,
        "T001",
        decision="REPLAN",
        reason="the task boundary invalidates the acceptance model",
        expected_revision=4,
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
            reviewer_session="codex-project",
            delegation_id="review-1",
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
            reviewer_session="codex-project",
            delegation_id="review-1",
            blocking_high=0,
            blocking_medium=0,
            invalidates_safety=False,
            mutations_run=0,
            expected_revision=2,
        )


@pytest.mark.parametrize("local", [False, True])
def test_mesh_cli_executes_release_pass_transaction_end_to_end(tmp_path: Path, local: bool) -> None:
    repo, feature = _feature(tmp_path)
    if local:
        (feature / "github-ledger.json").unlink()
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
            "--reviewer-session",
            "codex-project",
            "--delegation-id",
            "review-release-1",
            "--mutations-run",
            "1",
            "--expect-revision",
            "2",
            "--json",
        ],
        ["status", str(repo), str(feature), "T001", "--json"],
    ]
    commands[2][commands[2].index("--expect-revision") + 1] = "3"
    commands.insert(2, ["ack", str(repo), str(feature), "T001",
                        "--reviewer-session", "codex-project",
                        "--delegation-id", "review-release-1",
                        "--evidence-file", "ack.md", "--expect-revision", "2", "--json"])
    (feature / "ack.md").write_text("Reviewer accepted the scope.\n")
    if local:
        commands[0].append("--local")
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

    assert [item["revision"] for item in outputs] == [1, 2, 3, 4, 4]
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
            "--scope",
            SCOPE_A,
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

    assert review.main(
        ["check", str(repo), str(feature), "T001", "--scope", SCOPE_A, "--json"]
    ) == 1
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "READY_FOR_REVIEW"
    assert output["release_passed"] is False

    assert review.main(
        ["check", str(repo), str(feature), "T999", "--scope", SCOPE_A, "--json"]
    ) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "task not found in tasks.md" in captured.err


def test_check_rejects_stale_candidate_scope(tmp_path: Path) -> None:
    repo, feature = _feature(tmp_path)
    _init(repo, feature)
    _open(repo, feature, 1, level="RELEASE", scope=SCOPE_A)
    _ack(repo, feature, 2)
    _record(repo, feature, 3, "PASS")

    with pytest.raises(review.ReviewLedgerError, match="scope mismatch"):
        review.review_check(repo, feature, "T001", scope=SCOPE_B)

    assert review.review_status(repo, feature, "T001")["revision"] == 4


def test_mesh_cli_executes_correction_cycle_end_to_end(tmp_path: Path) -> None:
    repo, feature = _feature(tmp_path)
    failed_report = feature / "release-failed.md"
    failed_report.write_text("One medium finding.\n", encoding="utf-8")
    delta_report = feature / "delta-pass.md"
    delta_report.write_text("Correction verified.\n", encoding="utf-8")
    release_report = feature / "release-pass.md"
    release_report.write_text("Release scope verified.\n", encoding="utf-8")
    ack_report = feature / "ack.md"
    ack_report.write_text("Reviewer accepted the immutable review scope.\n", encoding="utf-8")
    env = {**os.environ, "MESH_SPECKIT_PYTHON": sys.executable}

    def run(*arguments: str, expected: int = 0) -> dict:
        proc = subprocess.run(
            ["bash", str(MESH), "speckit", "review", *arguments, "--json"],
            cwd=ROOT,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == expected, proc.stderr
        return json.loads(proc.stdout)

    common = (str(repo), str(feature), "T001")
    assert run(
        "init",
        *common,
        "--scope",
        SCOPE_A,
        "--writer-session",
        "agy-project",
        "--invariant",
        "release requires RELEASE PASS",
        "--expect-revision",
        "0",
    )["status"] == "READY_FOR_REVIEW"
    assert run("check", *common, "--scope", SCOPE_A, expected=1)["release_passed"] is False
    run(
        "open",
        *common,
        "--level",
        "RELEASE",
        "--scope",
        SCOPE_A,
        "--reviewer-session",
        "codex-project",
        "--delegation-id",
        "release-1",
        "--expect-revision",
        "1",
    )
    run(
        "ack", *common,
        "--reviewer-session", "codex-project",
        "--delegation-id", "release-1",
        "--evidence-file", str(ack_report),
        "--expect-revision", "2",
    )
    assert run(
        "record",
        *common,
        "--verdict",
        "CHANGES_REQUIRED",
        "--evidence-file",
        str(failed_report),
        "--reviewer-session",
        "codex-project",
        "--delegation-id",
        "release-1",
        "--blocking-medium",
        "1",
        "--expect-revision",
        "3",
    )["status"] == "CHANGES_REQUIRED"
    assert run(
        "correction",
        *common,
        "--delegation-id",
        "fix-1",
        "--expect-revision",
        "4",
    )["round"] == 1
    run(
        "open",
        *common,
        "--level",
        "DELTA",
        "--scope",
        DELTA_1,
        "--reviewer-session",
        "codex-project",
        "--delegation-id",
        "delta-1",
        "--expect-revision",
        "5",
    )
    run(
        "ack", *common,
        "--reviewer-session", "codex-project",
        "--delegation-id", "delta-1",
        "--evidence-file", str(ack_report),
        "--expect-revision", "6",
    )
    assert run(
        "record",
        *common,
        "--verdict",
        "PASS",
        "--evidence-file",
        str(delta_report),
        "--reviewer-session",
        "codex-project",
        "--delegation-id",
        "delta-1",
        "--expect-revision",
        "7",
    )["status"] == "CANDIDATE_UPDATE_REQUIRED"
    assert run(
        "candidate",
        *common,
        "--scope",
        SCOPE_B,
        "--expect-revision",
        "8",
    )["status"] == "READY_FOR_REVIEW"
    run(
        "open",
        *common,
        "--level",
        "RELEASE",
        "--scope",
        SCOPE_B,
        "--reviewer-session",
        "codex-project",
        "--delegation-id",
        "release-2",
        "--expect-revision",
        "9",
    )
    run(
        "ack", *common,
        "--reviewer-session", "codex-project",
        "--delegation-id", "release-2",
        "--evidence-file", str(ack_report),
        "--expect-revision", "10",
    )
    assert run(
        "record",
        *common,
        "--verdict",
        "PASS",
        "--evidence-file",
        str(release_report),
        "--reviewer-session",
        "codex-project",
        "--delegation-id",
        "release-2",
        "--expect-revision",
        "11",
    )["status"] == "RELEASE_PASSED"
    checked = run("check", *common, "--scope", SCOPE_B)
    assert checked["release_passed"] is True
    assert checked["revision"] == 12


def test_mesh_cli_executes_bounded_reviewer_timeout_end_to_end(tmp_path: Path) -> None:
    repo, feature = _feature(tmp_path)
    env = {**os.environ, "MESH_SPECKIT_PYTHON": sys.executable}

    def run(*arguments: str) -> dict:
        proc = subprocess.run(
            ["bash", str(MESH), "speckit", "review", *arguments, "--json"],
            cwd=ROOT,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout)

    common = (str(repo), str(feature), "T001")
    run(
        "init",
        *common,
        "--scope",
        SCOPE_A,
        "--writer-session",
        "agy-project",
        "--expect-revision",
        "0",
    )
    run(
        "open",
        *common,
        "--level",
        "RELEASE",
        "--scope",
        SCOPE_A,
        "--reviewer-session",
        "codex-project",
        "--delegation-id",
        "review-initial",
        "--expect-revision",
        "1",
    )

    ledger_path = feature / "review-ledger.json"
    payload = json.loads(ledger_path.read_text(encoding="utf-8"))
    payload["tasks"]["T001"]["events"][-1]["at"] = "2000-01-01T00:00:00+00:00"
    ledger_path.write_text(json.dumps(payload), encoding="utf-8")
    first = run("timeout", *common, "--expect-revision", "2")
    assert first["status"] == "READY_FOR_REVIEW"
    assert first["fallback_allowed"] is True

    run(
        "open",
        *common,
        "--level",
        "RELEASE",
        "--scope",
        SCOPE_A,
        "--reviewer-session",
        "codex-project-alt",
        "--delegation-id",
        "review-fallback",
        "--expect-revision",
        "3",
    )
    payload = json.loads(ledger_path.read_text(encoding="utf-8"))
    payload["tasks"]["T001"]["events"][-1]["at"] = "2000-01-01T00:00:00+00:00"
    ledger_path.write_text(json.dumps(payload), encoding="utf-8")
    second = run("timeout", *common, "--expect-revision", "4")
    assert second["status"] == "ESCALATED"
    assert second["fallback_allowed"] is False
    assert run("status", *common)["revision"] == 5


def test_ack_starts_review_deadline_at_ack_and_blocks_early_record(tmp_path, monkeypatch):
    repo, feature = _feature(tmp_path)
    monkeypatch.setattr(review, "_now", lambda: "2030-01-01T12:00:00+00:00")
    _init(repo, feature)
    opened = _open(repo, feature, 1, level="RELEASE", scope=SCOPE_A)
    assert opened["status"] == "REVIEW_PENDING_ACK"
    state = review.review_status(repo, feature, "T001")
    assert state["review_ack_opened_at"] == "2030-01-01T12:00:00+00:00"
    assert state["review_ack_deadline_at"] == "2030-01-01T12:05:00+00:00"
    assert "review_deadline_at" not in state
    before = (feature / "review-ledger.json").read_bytes()
    with pytest.raises(review.ReviewLedgerError, match="REVIEW_PENDING_ACK"):
        _record(repo, feature, 2, "PASS")
    assert (feature / "review-ledger.json").read_bytes() == before
    monkeypatch.setattr(review, "_now", lambda: "2030-01-01T12:04:00+00:00")
    ack = _ack(repo, feature, 2)
    assert ack["status"] == "REVIEW_OPEN"
    state = review.review_status(repo, feature, "T001")
    assert state["review_opened_at"] == "2030-01-01T12:04:00+00:00"
    assert state["review_deadline_at"] == "2030-01-01T13:04:00+00:00"
    assert "review_ack_deadline_at" not in state
    assert [e["type"] for e in state["events"]][-2:] == ["review_opened", "review_acknowledged"]
    monkeypatch.setattr(review, "_now", lambda: "2030-01-01T13:00:00+00:00")
    with pytest.raises(review.ReviewLedgerError, match="deadline has not elapsed"):
        review.timeout_review(repo, feature, "T001", expected_revision=3)
    monkeypatch.setattr(review, "_now", lambda: "2030-01-01T13:04:00+00:00")
    review.timeout_review(repo, feature, "T001", expected_revision=3)
    assert review.review_status(repo, feature, "T001")["events"][-1]["type"] == "review_timed_out"


def test_existing_open_review_without_ack_remains_manageable(tmp_path, monkeypatch):
    repo, feature = _feature(tmp_path)
    monkeypatch.setattr(review, "_now", lambda: "2030-01-01T12:00:00+00:00")
    _init(repo, feature)
    _open(repo, feature, 1, level="RELEASE", scope=SCOPE_A)
    ledger_path = feature / "review-ledger.json"
    payload = json.loads(ledger_path.read_text(encoding="utf-8"))
    payload["tasks"]["T001"]["status"] = "REVIEW_OPEN"
    ledger_path.write_text(json.dumps(payload), encoding="utf-8")

    state = review.review_status(repo, feature, "T001")
    assert state["review_opened_at"] == "2030-01-01T12:00:00+00:00"
    assert state["review_deadline_at"] == "2030-01-01T13:00:00+00:00"
    monkeypatch.setattr(review, "_now", lambda: "2030-01-01T13:00:00+00:00")
    assert review.timeout_review(repo, feature, "T001", expected_revision=2)["fallback_allowed"]


@pytest.mark.parametrize("failure", ["reviewer", "delegation", "revision", "outside", "symlink", "parent-symlink", "directory", "missing", "secret", "binary"])
def test_ack_failure_preserves_ledger_bytes(tmp_path, failure):
    repo, feature = _feature(tmp_path)
    _init(repo, feature)
    _open(repo, feature, 1, level="RELEASE", scope=SCOPE_A)
    evidence = feature / "ack.md"
    evidence.write_text("Reviewer accepted the scope.\n")
    kwargs = dict(reviewer_session="codex-project", delegation_id="review-1",
                  evidence_file=evidence, expected_revision=2)
    if failure == "reviewer":
        kwargs["reviewer_session"] = "different"
    elif failure == "delegation":
        kwargs["delegation_id"] = "different"
    elif failure == "revision":
        kwargs["expected_revision"] = 1
    elif failure == "outside":
        kwargs["evidence_file"] = repo / "outside.md"
        kwargs["evidence_file"].write_text("Accepted")
    elif failure == "symlink":
        link = feature / "link.md"
        link.symlink_to(evidence)
        kwargs["evidence_file"] = link
    elif failure == "parent-symlink":
        link = feature / "linked"
        link.symlink_to(feature, target_is_directory=True)
        kwargs["evidence_file"] = link / "ack.md"
    elif failure == "directory":
        kwargs["evidence_file"] = feature
    elif failure == "missing":
        evidence.unlink()
    elif failure == "secret":
        evidence.write_text("API_KEY=do-not-persist-this\n")
    elif failure == "binary":
        evidence.write_bytes(b"\xff")
    before = (feature / "review-ledger.json").read_bytes()
    with pytest.raises(review.ReviewLedgerError):
        review.acknowledge_review(repo, feature, "T001", **kwargs)
    assert (feature / "review-ledger.json").read_bytes() == before


def test_pending_ack_timeout_and_stale_ack_share_terminal_budget(tmp_path, monkeypatch):
    repo, feature = _feature(tmp_path)
    monkeypatch.setattr(review, "_now", lambda: "2030-01-01T12:00:00+00:00")
    _init(repo, feature)
    _open(repo, feature, 1, level="RELEASE", scope=SCOPE_A)
    monkeypatch.setattr(review, "_now", lambda: "2030-01-01T12:04:59+00:00")
    with pytest.raises(review.ReviewLedgerError, match="deadline has not elapsed"):
        review.timeout_review(repo, feature, "T001", expected_revision=2)
    monkeypatch.setattr(review, "_now", lambda: "2030-01-01T12:05:00+00:00")
    assert review.timeout_review(repo, feature, "T001", expected_revision=2)["fallback_allowed"]
    review.open_review(repo, feature, "T001", level="RELEASE", scope=SCOPE_A,
                       reviewer_session="fallback", delegation_id="fallback-id",
                       invariant="", expected_revision=3)
    report = feature / "ack.md"
    report.write_text("Accepted")
    before = (feature / "review-ledger.json").read_bytes()
    with pytest.raises(review.ReviewLedgerError, match="identity"):
        review.acknowledge_review(repo, feature, "T001", reviewer_session="codex-project",
                                  delegation_id="review-1", evidence_file=report, expected_revision=4)
    assert (feature / "review-ledger.json").read_bytes() == before
    monkeypatch.setattr(review, "_now", lambda: "2030-01-01T12:10:00+00:00")
    assert review.timeout_review(repo, feature, "T001", expected_revision=4)["status"] == "ESCALATED"
    state = review.review_status(repo, feature, "T001")
    assert sum(e["type"] == "review_ack_timed_out" for e in state["events"]) == 2
