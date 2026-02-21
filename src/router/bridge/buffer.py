"""Fallback buffer for offline event resilience.

When the transport fails (router unreachable), events are buffered
to an NDJSON file. On reconnect, buffered events are replayed in order.
Uses fcntl file locking for single-machine concurrency safety.
"""

from __future__ import annotations

import fcntl
from datetime import datetime, timezone
from pathlib import Path

from src.router.bridge.transport import EventTransport


class FallbackBuffer:
    """NDJSON file buffer for offline event resilience.

    Events are appended atomically with file locking.
    Replay sends all buffered events via transport and rotates
    the file on full success.
    """

    def __init__(self, buffer_path: str | Path = "~/.mesh/events-buffer.jsonl") -> None:
        self.buffer_path = Path(buffer_path).expanduser()
        self.buffer_path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, cloud_event_json: str) -> None:
        """Append a serialized CloudEvent to the buffer file.

        Uses fcntl.flock for file-level locking (concurrent writers
        safe on the same machine).
        """
        with open(self.buffer_path, "a") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                f.write(cloud_event_json.rstrip("\n") + "\n")
                f.flush()
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    def read_all(self) -> list[str]:
        """Read all buffered events as list of JSON strings."""
        if not self.buffer_path.exists():
            return []
        events = []
        with open(self.buffer_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    events.append(line)
        return events

    def count(self) -> int:
        """Return number of buffered events."""
        return len(self.read_all())

    def replay(self, transport: EventTransport) -> tuple[int, int]:
        """Replay all buffered events via transport.

        Returns (sent_count, failed_count).
        On full success, rotates the buffer file to .replayed-{ts}.
        On partial failure, rewrites buffer with only failed events.
        """
        events = self.read_all()
        if not events:
            return (0, 0)

        sent = 0
        failed_events: list[str] = []

        for event_json in events:
            if transport.send(event_json):
                sent += 1
            else:
                failed_events.append(event_json)

        if not failed_events:
            # Full success — rotate file
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            rotated = self.buffer_path.with_suffix(f".replayed-{ts}")
            self.buffer_path.rename(rotated)
        else:
            # Partial failure — rewrite buffer with only failed events
            with open(self.buffer_path, "w") as f:
                for ev in failed_events:
                    f.write(ev.rstrip("\n") + "\n")

        return (sent, len(failed_events))

    def clear(self) -> None:
        """Remove the buffer file entirely."""
        if self.buffer_path.exists():
            self.buffer_path.unlink()
