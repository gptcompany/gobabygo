"""Tests for LongPollRegistry -- per-worker Condition and predicate pattern."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import pytest

from src.router.longpoll import LongPollRegistry, PollResult
from src.router.models import Task, TaskStatus


@pytest.fixture
def registry() -> LongPollRegistry:
    return LongPollRegistry()


@pytest.fixture
def mock_db():
    """Return a mock RouterDB that returns no tasks by default."""
    db = MagicMock()
    db.get_tasks_by_worker = MagicMock(return_value=[])
    return db


@pytest.fixture
def mock_db_with_task():
    """Return a mock RouterDB that returns an assigned task."""
    db = MagicMock()
    task = Task(
        task_id="t1",
        title="test task",
        phase="implement",
        status=TaskStatus.assigned,
        assigned_worker="w1",
        idempotency_key="k1",
    )
    db.get_tasks_by_worker = MagicMock(return_value=[task])
    return db


class TestRegisterUnregister:
    def test_register_creates_slot(self, registry: LongPollRegistry) -> None:
        registry.register("w1")
        assert "w1" in registry._slots

    def test_unregister_removes_slot(self, registry: LongPollRegistry) -> None:
        registry.register("w1")
        registry.unregister("w1")
        assert "w1" not in registry._slots

    def test_unregister_nonexistent_is_noop(self, registry: LongPollRegistry) -> None:
        registry.unregister("w-nonexistent")  # should not raise

    def test_reregister_replaces_condition(self, registry: LongPollRegistry) -> None:
        registry.register("w1")
        old_cond = registry._slots["w1"].condition
        registry.register("w1")
        new_cond = registry._slots["w1"].condition
        assert old_cond is not new_cond


class TestWaitForTask:
    def test_wait_returns_none_on_timeout(
        self, registry: LongPollRegistry, mock_db: MagicMock,
    ) -> None:
        registry.register("w1")
        result = registry.wait_for_task("w1", timeout_s=0.1, db=mock_db)
        assert result.task is None
        assert result.conflict is False

    def test_wait_returns_task_on_notify(
        self, registry: LongPollRegistry, mock_db_with_task: MagicMock,
    ) -> None:
        registry.register("w1")
        result_holder: list[PollResult] = []

        def poll() -> None:
            res = registry.wait_for_task("w1", timeout_s=5.0, db=mock_db_with_task)
            result_holder.append(res)

        t = threading.Thread(target=poll)
        t.start()

        # Give thread time to enter wait
        time.sleep(0.05)
        registry.notify_task_available("w1")
        t.join(timeout=2.0)

        assert len(result_holder) == 1
        assert result_holder[0].task is not None
        assert result_holder[0].task.task_id == "t1"
        assert result_holder[0].conflict is False

    def test_concurrent_poll_returns_conflict(
        self, registry: LongPollRegistry, mock_db: MagicMock,
    ) -> None:
        registry.register("w1")
        barrier = threading.Event()

        def first_poll() -> None:
            barrier.set()  # signal we've started
            registry.wait_for_task("w1", timeout_s=2.0, db=mock_db)

        t = threading.Thread(target=first_poll)
        t.start()
        barrier.wait(timeout=1.0)
        time.sleep(0.05)  # ensure first poll enters wait

        # Second poll should get conflict
        result = registry.wait_for_task("w1", timeout_s=0.1, db=mock_db)
        assert result.conflict is True

        # Clean up -- notify to unblock first poll
        registry.notify_task_available("w1")
        t.join(timeout=2.0)

    def test_zombie_connection_replaced(
        self, registry: LongPollRegistry, mock_db: MagicMock,
    ) -> None:
        registry.register("w1")
        slot = registry._slots["w1"]
        # Simulate zombie: in_flight_poll set, but timestamp far in the past
        slot.in_flight_poll = True
        slot.in_flight_since = time.monotonic() - 100.0  # 100s ago

        # New poll should succeed (zombie detected and replaced)
        result = registry.wait_for_task("w1", timeout_s=0.1, db=mock_db)
        assert result.conflict is False
        assert result.task is None  # no task dispatched

    def test_timeout_race_condition_mitigation(
        self, registry: LongPollRegistry, mock_db_with_task: MagicMock,
    ) -> None:
        """On timeout, handler checks DB -- finds task dispatched during timeout window."""
        registry.register("w1")
        result = registry.wait_for_task("w1", timeout_s=0.05, db=mock_db_with_task)
        # Even though no notify was sent, DB fallback finds the task
        assert result.task is not None
        assert result.task.task_id == "t1"


class TestNotify:
    def test_notify_without_waiter_is_noop(
        self, registry: LongPollRegistry,
    ) -> None:
        # Notify for non-registered worker -- should not raise
        registry.notify_task_available("w-nonexistent")

    def test_notify_registered_but_not_waiting(
        self, registry: LongPollRegistry,
    ) -> None:
        registry.register("w1")
        # Notify when no poll in progress -- should not raise
        registry.notify_task_available("w1")


class TestWaitingCount:
    def test_waiting_count_tracks_active_polls(
        self, registry: LongPollRegistry, mock_db: MagicMock,
    ) -> None:
        assert registry.waiting_count() == 0

        registry.register("w1")
        registry.register("w2")

        started = threading.Event()
        results: list[PollResult] = []

        def poll(wid: str) -> None:
            started.set()
            res = registry.wait_for_task(wid, timeout_s=5.0, db=mock_db)
            results.append(res)

        t1 = threading.Thread(target=poll, args=("w1",))
        t2 = threading.Thread(target=poll, args=("w2",))
        t1.start()
        started.wait(timeout=1.0)
        time.sleep(0.05)

        started.clear()
        t2.start()
        started.wait(timeout=1.0)
        time.sleep(0.05)

        assert registry.waiting_count() == 2

        # Notify both to unblock
        registry.notify_task_available("w1")
        registry.notify_task_available("w2")
        t1.join(timeout=2.0)
        t2.join(timeout=2.0)

    def test_waiting_count_decreases_after_timeout(
        self, registry: LongPollRegistry, mock_db: MagicMock,
    ) -> None:
        registry.register("w1")
        registry.wait_for_task("w1", timeout_s=0.05, db=mock_db)
        assert registry.waiting_count() == 0

    def test_auto_creates_slot_on_wait(
        self, registry: LongPollRegistry, mock_db: MagicMock,
    ) -> None:
        """wait_for_task auto-creates a slot if worker not registered."""
        result = registry.wait_for_task("w-new", timeout_s=0.05, db=mock_db)
        assert result.task is None
        assert "w-new" in registry._slots
