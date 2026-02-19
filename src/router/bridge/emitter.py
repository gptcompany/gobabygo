"""Event emitter for the GSD-Router bridge.

Wraps GSD command activity into CloudEvent-compliant envelopes,
validates against JSON Schema, checks CommunicationPolicy, and
dispatches via a pluggable transport. Falls back to buffer on failure.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from cloudevents.http import CloudEvent
from cloudevents.conversion import to_json

from src.router.bridge.schema import validate_event_data
from src.router.bridge.transport import EventTransport
from src.router.comms import CommunicationPolicy

# Import TYPE_CHECKING to avoid circular imports at runtime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.router.bridge.buffer import FallbackBuffer


class EventEmitter:
    """Creates and dispatches CloudEvent-wrapped GSD command events.

    Args:
        transport: The transport adapter for event delivery.
        source_machine: Machine identifier for CloudEvent source field.
        comm_policy: Optional CommunicationPolicy for sender role validation.
        buffer: Optional FallbackBuffer for offline resilience.
    """

    def __init__(
        self,
        transport: EventTransport,
        source_machine: str = "unknown",
        comm_policy: CommunicationPolicy | None = None,
        buffer: FallbackBuffer | None = None,
    ) -> None:
        self.transport = transport
        self.source_machine = source_machine
        self.comm_policy = comm_policy
        self.buffer = buffer

    @staticmethod
    def _make_idempotency_key(
        run_id: str,
        command: str,
        step: str,
        event_kind: str,
        attempt: int,
    ) -> str:
        """Generate a deterministic idempotency key via SHA-256.

        Stable across Python sessions and versions (unlike built-in hash()).
        """
        raw = f"{run_id}:{command}:{step}:{event_kind}:{attempt}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def emit(
        self,
        command: str,
        event_kind: str,
        run_id: str,
        sender_role: str = "president",
        task_id: str | None = None,
        phase: str | None = None,
        step: str | None = None,
        target_cli: str | None = None,
        target_account: str | None = None,
        status: str | None = None,
        attempt: int = 1,
        artifact_paths: list[str] | None = None,
        duration_ms: int | None = None,
    ) -> bool:
        """Create a CloudEvent, validate, and send via transport.

        Returns True if the event was delivered successfully.
        Returns False if delivery failed (event may be buffered).

        Raises:
            ValueError: If schema validation fails or sender role is unauthorized.
        """
        # 1. CommunicationPolicy check
        if self.comm_policy is not None:
            if not self.comm_policy.can_create_task(sender_role):
                raise ValueError(
                    f"Sender role '{sender_role}' is not authorized to emit events"
                )

        # 2. Build data payload
        ts = datetime.now(timezone.utc).isoformat()
        idempotency_key = self._make_idempotency_key(
            run_id, command, step or "", event_kind, attempt
        )

        data = {
            "run_id": run_id,
            "task_id": task_id,
            "phase": phase,
            "gsd_command": command,
            "step": step,
            "event": event_kind,
            "target_cli": target_cli,
            "target_account": target_account,
            "status": status,
            "attempt": attempt,
            "idempotency_key": idempotency_key,
            "artifact_paths": artifact_paths or [],
            "ts": ts,
            "duration_ms": duration_ms,
            "sender_role": sender_role,
        }

        # 3. Validate against JSON Schema
        errors = validate_event_data(data)
        if errors:
            raise ValueError(f"Event schema validation failed: {errors}")

        # 4. Create CloudEvent
        attributes = {
            "type": f"com.mesh.command.{event_kind}",
            "source": f"mesh/gsd-bridge/{self.source_machine}",
        }
        cloud_event = CloudEvent(attributes, data)

        # 5. Serialize
        cloud_event_json = to_json(cloud_event).decode("utf-8")

        # 6. Send via transport
        success = self.transport.send(cloud_event_json)

        # 7. Buffer on failure
        if not success and self.buffer is not None:
            self.buffer.append(cloud_event_json)

        return success

    def replay_buffer(self) -> tuple[int, int]:
        """Replay buffered events via transport.

        Returns (sent_count, failed_count). Returns (0, 0) if no buffer.
        """
        if self.buffer is None:
            return (0, 0)
        return self.buffer.replay(self.transport)
