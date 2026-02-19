"""Tests for the communication hierarchy policy enforcement.

Covers:
- Role validation: boss/president can create, worker cannot
- Dispatch authorization: only president dispatches
- Task ownership: assigned worker can ack/complete, wrong worker cannot
- Hierarchy edges: valid edges pass, invalid edges blocked
- Visibility: boss/president view all, worker scoped
"""

from __future__ import annotations

import pytest

from src.router.comms import CommunicationPolicy, HIERARCHY_EDGES
from src.router.models import CommunicationRole, Task, TaskPhase, TaskStatus


@pytest.fixture
def policy() -> CommunicationPolicy:
    return CommunicationPolicy()


@pytest.fixture
def assigned_task() -> Task:
    """Task assigned to worker w1."""
    return Task(
        title="Implement feature",
        phase=TaskPhase.implement,
        status=TaskStatus.assigned,
        assigned_worker="w1",
        created_by="president",
    )


@pytest.fixture
def unassigned_task() -> Task:
    """Task with no assigned worker."""
    return Task(
        title="Queued task",
        phase=TaskPhase.implement,
        status=TaskStatus.queued,
        assigned_worker=None,
    )


# -- Role validation tests --


class TestCanCreateTask:
    def test_can_create_task_boss(self, policy: CommunicationPolicy) -> None:
        """Boss can create tasks."""
        assert policy.can_create_task("boss") is True

    def test_can_create_task_president(self, policy: CommunicationPolicy) -> None:
        """President can create tasks."""
        assert policy.can_create_task("president") is True

    def test_can_create_task_worker(self, policy: CommunicationPolicy) -> None:
        """Worker cannot create tasks."""
        assert policy.can_create_task("worker") is False


class TestCanDispatchTask:
    def test_can_dispatch_task_president(self, policy: CommunicationPolicy) -> None:
        """President can dispatch tasks to workers."""
        assert policy.can_dispatch_task("president") is True

    def test_can_dispatch_task_worker(self, policy: CommunicationPolicy) -> None:
        """Worker cannot dispatch tasks."""
        assert policy.can_dispatch_task("worker") is False

    def test_can_dispatch_task_boss(self, policy: CommunicationPolicy) -> None:
        """Boss cannot dispatch (only president dispatches)."""
        assert policy.can_dispatch_task("boss") is False


# -- Task ownership tests --


class TestCanAckTask:
    def test_can_ack_task_assigned_worker(
        self, policy: CommunicationPolicy, assigned_task: Task
    ) -> None:
        """Assigned worker can acknowledge their task."""
        assert policy.can_ack_task("w1", assigned_task) is True

    def test_can_ack_task_wrong_worker(
        self, policy: CommunicationPolicy, assigned_task: Task
    ) -> None:
        """Different worker cannot acknowledge someone else's task."""
        assert policy.can_ack_task("w2", assigned_task) is False

    def test_can_ack_task_unassigned(
        self, policy: CommunicationPolicy, unassigned_task: Task
    ) -> None:
        """No worker can ack a task with no assigned worker."""
        assert policy.can_ack_task("w1", unassigned_task) is False


class TestCanCompleteTask:
    def test_can_complete_task_assigned_worker(
        self, policy: CommunicationPolicy, assigned_task: Task
    ) -> None:
        """Assigned worker can report completion."""
        assert policy.can_complete_task("w1", assigned_task) is True

    def test_can_complete_task_wrong_worker(
        self, policy: CommunicationPolicy, assigned_task: Task
    ) -> None:
        """Different worker cannot report completion for someone else's task."""
        assert policy.can_complete_task("w2", assigned_task) is False


# -- Hierarchy edge tests --


class TestValidateCommunication:
    def test_validate_communication_boss_to_president(
        self, policy: CommunicationPolicy
    ) -> None:
        """Boss can communicate with president."""
        assert policy.validate_communication("boss", "president") is True

    def test_validate_communication_president_to_worker(
        self, policy: CommunicationPolicy
    ) -> None:
        """President can communicate with worker."""
        assert policy.validate_communication("president", "worker") is True

    def test_validate_communication_worker_to_president(
        self, policy: CommunicationPolicy
    ) -> None:
        """Worker can communicate with president."""
        assert policy.validate_communication("worker", "president") is True

    def test_validate_communication_worker_to_worker(
        self, policy: CommunicationPolicy
    ) -> None:
        """Worker cannot communicate with another worker (no lateral)."""
        assert policy.validate_communication("worker", "worker") is False

    def test_validate_communication_worker_to_boss(
        self, policy: CommunicationPolicy
    ) -> None:
        """Worker cannot communicate with boss (must go through president)."""
        assert policy.validate_communication("worker", "boss") is False

    def test_validate_communication_boss_to_worker(
        self, policy: CommunicationPolicy
    ) -> None:
        """Boss cannot communicate directly with worker (must go through president)."""
        assert policy.validate_communication("boss", "worker") is False


# -- Visibility tests --


class TestCanViewAllTasks:
    def test_can_view_all_tasks_boss(self, policy: CommunicationPolicy) -> None:
        """Boss can view all tasks."""
        assert policy.can_view_all_tasks("boss") is True

    def test_can_view_all_tasks_president(self, policy: CommunicationPolicy) -> None:
        """President can view all tasks."""
        assert policy.can_view_all_tasks("president") is True

    def test_can_view_all_tasks_worker(self, policy: CommunicationPolicy) -> None:
        """Worker has scoped visibility (cannot view all)."""
        assert policy.can_view_all_tasks("worker") is False
