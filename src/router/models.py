"""Pydantic models for the AI Mesh Router.

All timestamps are UTC ISO-8601. All IDs are UUID4 strings.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def _utc_now() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _uuid4() -> str:
    """Return a new UUID4 string."""
    return str(uuid.uuid4())


class TaskStatus(str, Enum):
    queued = "queued"
    assigned = "assigned"
    blocked = "blocked"
    running = "running"
    review = "review"
    completed = "completed"
    failed = "failed"
    timeout = "timeout"
    canceled = "canceled"


class TaskPhase(str, Enum):
    plan = "plan"
    implement = "implement"
    test = "test"
    integrate = "integrate"
    release = "release"


class CommunicationRole(str, Enum):
    boss = "boss"
    president = "president"
    worker = "worker"


class CLIType(str, Enum):
    claude = "claude"
    codex = "codex"
    gemini = "gemini"


class Task(BaseModel):
    task_id: str = Field(default_factory=_uuid4)
    parent_task_id: str | None = None
    phase: TaskPhase = TaskPhase.implement
    title: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    target_cli: CLIType = CLIType.claude
    target_account: str = "default"
    priority: int = 1
    deadline_ts: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    status: TaskStatus = TaskStatus.queued
    assigned_worker: str | None = None
    lease_expires_at: str | None = None
    attempt: int = 1
    not_before: str | None = None
    created_by: str | None = None
    idempotency_key: str = Field(default_factory=_uuid4)
    created_at: str = Field(default_factory=_utc_now)
    updated_at: str = Field(default_factory=_utc_now)


class TaskEvent(BaseModel):
    event_id: str = Field(default_factory=_uuid4)
    task_id: str
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str = Field(default_factory=_uuid4)
    ts: str = Field(default_factory=_utc_now)


class Worker(BaseModel):
    worker_id: str = Field(default_factory=_uuid4)
    machine: str = ""
    cli_type: CLIType = CLIType.claude
    account_profile: str = "default"
    capabilities: list[str] = Field(default_factory=list)
    role: str = CommunicationRole.worker.value
    status: str = "idle"
    last_heartbeat: str = Field(default_factory=_utc_now)
    idle_since: str = Field(default_factory=_utc_now)
    stale_since: str | None = None
    concurrency: int = 1


class Lease(BaseModel):
    lease_id: str = Field(default_factory=_uuid4)
    task_id: str
    worker_id: str
    granted_at: str = Field(default_factory=_utc_now)
    expires_at: str
