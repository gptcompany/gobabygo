"""Communication hierarchy policy enforcement for the AI Mesh Router.

Enforces the runtime hierarchy for BOSS, PRESIDENT, LEAD, and WORKER roles.
Every operation that violates the hierarchy is a hard block.

The CommunicationPolicy class is stateless (no DB dependency) — it is a
pure policy engine that returns bool for each authorization check.
"""

from __future__ import annotations

from src.router.models import CommunicationRole, Task

# Allowed communication edges (directed graph).
# Each key maps to the set of roles it may communicate WITH.
HIERARCHY_EDGES: dict[CommunicationRole, set[CommunicationRole]] = {
    CommunicationRole.boss: {CommunicationRole.president},
    CommunicationRole.president: {
        CommunicationRole.worker,
        CommunicationRole.lead,
        CommunicationRole.boss,
    },
    CommunicationRole.lead: {
        CommunicationRole.worker,
        CommunicationRole.president,
    },
    CommunicationRole.worker: {
        CommunicationRole.lead,
        CommunicationRole.president,
    },
}

# Roles allowed to create tasks.
_TASK_CREATORS: set[str] = {
    CommunicationRole.boss.value,
    CommunicationRole.president.value,
    CommunicationRole.lead.value,
}

# President and lead may dispatch tasks to workers.
_DISPATCHERS: set[str] = {
    CommunicationRole.president.value,
    CommunicationRole.lead.value,
}

# Roles with full task visibility.
_FULL_VISIBILITY: set[str] = {
    CommunicationRole.boss.value,
    CommunicationRole.president.value,
    CommunicationRole.lead.value,
}


class CommunicationPolicy:
    """Stateless policy engine for communication hierarchy enforcement."""

    def can_create_task(self, creator_role: str) -> bool:
        """Boss, president, and lead can create tasks. Workers cannot."""
        return creator_role in _TASK_CREATORS

    def can_dispatch_task(self, dispatcher_role: str) -> bool:
        """President and lead roles can dispatch tasks to workers."""
        return dispatcher_role in _DISPATCHERS

    def can_ack_task(self, worker_id: str, task: Task) -> bool:
        """Only the assigned worker can acknowledge their own task."""
        return (
            task.assigned_worker is not None
            and task.assigned_worker == worker_id
        )

    def can_complete_task(self, worker_id: str, task: Task) -> bool:
        """Only the assigned worker can report task completion."""
        return (
            task.assigned_worker is not None
            and task.assigned_worker == worker_id
        )

    def can_view_all_tasks(self, role: str) -> bool:
        """Boss, president, and lead can view all tasks."""
        return role in _FULL_VISIBILITY

    def validate_communication(self, sender_role: str, receiver_role: str) -> bool:
        """Check if the sender -> receiver edge exists in the hierarchy."""
        try:
            sender = CommunicationRole(sender_role)
            receiver = CommunicationRole(receiver_role)
        except ValueError:
            return False
        return receiver in HIERARCHY_EDGES.get(sender, set())
