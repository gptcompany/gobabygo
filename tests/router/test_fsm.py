"""Tests for the FSM transition guard and dead-letter stream.

Covers:
- All valid transitions from ALLOWED_TRANSITIONS
- Invalid transitions are rejected
- Terminal states have no outgoing transitions
- apply_transition success path (CAS + event)
- apply_transition invalid path (dead-letter written)
- apply_transition CAS failure path (concurrent modification)
- Dead-letter entry content verification
- Dead-letter query functions
- canceled reachable from all non-terminal states
"""

from __future__ import annotations

import pytest

from src.router.db import RouterDB
from src.router.dead_letter import count_dead_letters, get_dead_letters
from src.router.fsm import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATES,
    TransitionRequest,
    apply_transition,
    validate_transition,
)
from src.router.models import Task, TaskPhase, TaskStatus


@pytest.fixture
def db() -> RouterDB:
    """Create an in-memory RouterDB with schema initialized."""
    rdb = RouterDB(":memory:")
    rdb.init_schema()
    return rdb


def _make_task(status: TaskStatus = TaskStatus.queued) -> Task:
    """Helper to create a task with a given status."""
    return Task(
        title="Test task",
        phase=TaskPhase.implement,
        status=status,
    )


# -- validate_transition --


def test_valid_transitions() -> None:
    """All entries in ALLOWED_TRANSITIONS pass validate_transition."""
    for from_status, allowed_targets in ALLOWED_TRANSITIONS.items():
        for to_status in allowed_targets:
            assert validate_transition(from_status, to_status), (
                f"Expected {from_status.value} -> {to_status.value} to be valid"
            )


def test_review_to_timeout_valid() -> None:
    """review -> timeout is a valid FSM transition (used for review deadline expiry)."""
    assert validate_transition(TaskStatus.review, TaskStatus.timeout), (
        "Expected review -> timeout to be valid"
    )


def test_review_to_timeout_apply(db: RouterDB) -> None:
    """apply_transition succeeds for review -> timeout."""
    task = _make_task(TaskStatus.review)
    db.insert_task(task)

    request = TransitionRequest(
        task_id=task.task_id,
        from_status=TaskStatus.review,
        to_status=TaskStatus.timeout,
        reason="review_timeout",
    )
    result = apply_transition(db, request)

    assert result.success is True
    updated = db.get_task(task.task_id)
    assert updated is not None
    assert updated.status == TaskStatus.timeout


def test_review_to_queued_valid() -> None:
    """review -> queued is a valid FSM transition (retry after review timeout)."""
    assert validate_transition(TaskStatus.review, TaskStatus.queued), (
        "Expected review -> queued to be valid"
    )


def test_review_to_queued_apply(db: RouterDB) -> None:
    """apply_transition succeeds for review -> queued."""
    task = _make_task(TaskStatus.review)
    db.insert_task(task)

    request = TransitionRequest(
        task_id=task.task_id,
        from_status=TaskStatus.review,
        to_status=TaskStatus.queued,
        reason="review_timeout_retry",
    )
    result = apply_transition(db, request)

    assert result.success is True
    updated = db.get_task(task.task_id)
    assert updated is not None
    assert updated.status == TaskStatus.queued


def test_invalid_transitions() -> None:
    """Known invalid transitions are rejected."""
    invalid_pairs = [
        (TaskStatus.queued, TaskStatus.completed),
        (TaskStatus.queued, TaskStatus.running),
        (TaskStatus.queued, TaskStatus.review),
        # running -> queued is now valid (step retry with on_failure=retry)
        (TaskStatus.running, TaskStatus.assigned),
        (TaskStatus.completed, TaskStatus.queued),
        (TaskStatus.completed, TaskStatus.running),
        (TaskStatus.failed, TaskStatus.queued),
        (TaskStatus.timeout, TaskStatus.running),
    ]
    for from_s, to_s in invalid_pairs:
        assert not validate_transition(from_s, to_s), (
            f"Expected {from_s.value} -> {to_s.value} to be invalid"
        )


def test_terminal_states_immutable() -> None:
    """Terminal states (completed, failed, timeout, canceled) have no outgoing transitions."""
    for terminal in TERMINAL_STATES:
        allowed = ALLOWED_TRANSITIONS[terminal]
        assert len(allowed) == 0, (
            f"Terminal state {terminal.value} should have no transitions, "
            f"but has: {[s.value for s in allowed]}"
        )
        # Also verify against every possible target
        for target in TaskStatus:
            assert not validate_transition(terminal, target), (
                f"Terminal {terminal.value} should not transition to {target.value}"
            )


# -- apply_transition success --


def test_apply_transition_success(db: RouterDB) -> None:
    """Valid transition (queued -> assigned) succeeds with event created."""
    task = _make_task(TaskStatus.queued)
    db.insert_task(task)

    request = TransitionRequest(
        task_id=task.task_id,
        from_status=TaskStatus.queued,
        to_status=TaskStatus.assigned,
        reason="worker found",
    )
    result = apply_transition(db, request)

    assert result.success is True
    assert result.reason is None
    assert result.event_id is not None

    # Verify task status actually changed
    updated_task = db.get_task(task.task_id)
    assert updated_task is not None
    assert updated_task.status == TaskStatus.assigned

    # Verify event was created
    events = db.get_events(task.task_id)
    assert len(events) == 1
    assert events[0].event_type == "state_transition"
    assert events[0].payload["from"] == "queued"
    assert events[0].payload["to"] == "assigned"
    assert events[0].payload["reason"] == "worker found"

    # No dead letters for valid transition
    assert count_dead_letters(db) == 0


# -- apply_transition invalid --


def test_apply_transition_invalid(db: RouterDB) -> None:
    """Invalid transition (queued -> completed) rejected, dead letter written."""
    task = _make_task(TaskStatus.queued)
    db.insert_task(task)

    request = TransitionRequest(
        task_id=task.task_id,
        from_status=TaskStatus.queued,
        to_status=TaskStatus.completed,
        reason="attempt skip",
    )
    result = apply_transition(db, request)

    assert result.success is False
    assert result.reason is not None
    assert "Invalid transition" in result.reason
    assert result.event_id is None

    # Task status unchanged
    updated_task = db.get_task(task.task_id)
    assert updated_task is not None
    assert updated_task.status == TaskStatus.queued

    # Dead letter was written
    assert count_dead_letters(db) == 1


# -- apply_transition CAS failure --


def test_apply_transition_concurrent(db: RouterDB) -> None:
    """CAS failure (concurrent modification) produces dead letter.

    Simulate by claiming from_status=queued when task is actually assigned.
    """
    task = _make_task(TaskStatus.queued)
    db.insert_task(task)

    # Pre-modify: move task to assigned directly via DB
    db.update_task_status(task.task_id, TaskStatus.queued, TaskStatus.assigned)

    # Now try queued -> assigned (valid FSM transition, but CAS will fail)
    request = TransitionRequest(
        task_id=task.task_id,
        from_status=TaskStatus.queued,
        to_status=TaskStatus.assigned,
        reason="late arrival",
    )
    result = apply_transition(db, request)

    assert result.success is False
    assert result.reason is not None
    assert "CAS failure" in result.reason
    assert result.event_id is None

    # Dead letter was written
    assert count_dead_letters(db) == 1

    # Task status unchanged (still assigned from the pre-modification)
    updated_task = db.get_task(task.task_id)
    assert updated_task is not None
    assert updated_task.status == TaskStatus.assigned


# -- Dead letter content verification --


def test_dead_letter_written(db: RouterDB) -> None:
    """After invalid transition, dead_letter_events has entry with correct fields."""
    task = _make_task(TaskStatus.queued)
    db.insert_task(task)

    request = TransitionRequest(
        task_id=task.task_id,
        from_status=TaskStatus.queued,
        to_status=TaskStatus.completed,
        reason="skip to done",
    )
    apply_transition(db, request)

    dls = get_dead_letters(db, task_id=task.task_id)
    assert len(dls) == 1

    dl = dls[0]
    assert dl["task_id"] == task.task_id
    assert dl["attempted_from"] == "queued"
    assert dl["attempted_to"] == "completed"
    assert "Invalid transition" in dl["reason"]
    assert dl["original_payload"]["request_reason"] == "skip to done"
    assert "ts" in dl["original_payload"]
    assert dl["dl_id"]  # non-empty UUID
    assert dl["ts"]  # non-empty timestamp


# -- Dead letter query --


def test_dead_letter_query(db: RouterDB) -> None:
    """get_dead_letters returns correct entries, filtered and unfiltered."""
    task1 = _make_task(TaskStatus.queued)
    task2 = _make_task(TaskStatus.completed)
    db.insert_task(task1)
    db.insert_task(task2)

    # Generate dead letters for both tasks
    apply_transition(db, TransitionRequest(
        task_id=task1.task_id,
        from_status=TaskStatus.queued,
        to_status=TaskStatus.completed,
        reason="invalid1",
    ))
    apply_transition(db, TransitionRequest(
        task_id=task2.task_id,
        from_status=TaskStatus.completed,
        to_status=TaskStatus.running,
        reason="invalid2",
    ))

    # Unfiltered: both entries
    all_dls = get_dead_letters(db)
    assert len(all_dls) == 2

    # Filtered by task_id
    task1_dls = get_dead_letters(db, task_id=task1.task_id)
    assert len(task1_dls) == 1
    assert task1_dls[0]["task_id"] == task1.task_id

    task2_dls = get_dead_letters(db, task_id=task2.task_id)
    assert len(task2_dls) == 1
    assert task2_dls[0]["task_id"] == task2.task_id

    # Count
    assert count_dead_letters(db) == 2


# -- canceled from all non-terminal states --


def test_canceled_from_any_non_terminal(db: RouterDB) -> None:
    """canceled is reachable from queued, assigned, blocked, running, review."""
    non_terminal = [
        TaskStatus.queued,
        TaskStatus.assigned,
        TaskStatus.blocked,
        TaskStatus.running,
        TaskStatus.review,
    ]

    for status in non_terminal:
        # Validate FSM allows it
        assert validate_transition(status, TaskStatus.canceled), (
            f"Expected {status.value} -> canceled to be valid"
        )

        # Apply it through the full path
        task = _make_task(status)
        db.insert_task(task)

        request = TransitionRequest(
            task_id=task.task_id,
            from_status=status,
            to_status=TaskStatus.canceled,
            reason=f"cancel from {status.value}",
        )
        result = apply_transition(db, request)

        assert result.success is True, (
            f"apply_transition {status.value} -> canceled failed: {result.reason}"
        )

        # Verify task is now canceled
        updated = db.get_task(task.task_id)
        assert updated is not None
        assert updated.status == TaskStatus.canceled

    # No dead letters should have been written
    assert count_dead_letters(db) == 0
