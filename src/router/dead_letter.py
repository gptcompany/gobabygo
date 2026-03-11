"""Dead-letter stream for rejected FSM transitions.

Captures all invalid or failed transition attempts with full context
for debugging and monitoring. Writes are independent of the FSM transaction
(dead-letter entries are committed even when the transition is rolled back).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_dead_letter(
    db,
    task_id: str,
    from_status: str,
    to_status: str,
    reason: str,
    payload: dict | None = None,
    *,
    conn=None,
) -> str:
    """Write a dead-letter entry for a rejected transition.

    Args:
        db: RouterDB instance.
        task_id: The task that was targeted.
        from_status: The status transition was attempted from.
        to_status: The status transition was attempted to.
        reason: Why the transition was rejected.
        payload: Optional additional context (serialized as JSON).

    Returns:
        The dl_id (UUID4) of the created dead-letter entry.
    """
    dl_id = str(uuid.uuid4())
    ts = _utc_now()
    payload_json = json.dumps(payload) if payload else "{}"

    target = conn or db._conn
    target.execute(
        """INSERT INTO dead_letter_events
        (dl_id, task_id, attempted_from, attempted_to, reason, original_payload, ts)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (dl_id, task_id, from_status, to_status, reason, payload_json, ts),
    )
    if conn is None:
        db._conn.commit()
    return dl_id


def get_dead_letters(
    db,
    task_id: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Query dead-letter entries, optionally filtered by task_id.

    Args:
        db: RouterDB instance.
        task_id: If provided, filter to this task only.
        limit: Maximum number of entries to return.

    Returns:
        List of dicts with dl_id, task_id, attempted_from, attempted_to,
        reason, original_payload (parsed), ts.
    """
    if task_id is not None:
        cur = db._conn.execute(
            "SELECT * FROM dead_letter_events WHERE task_id = ? ORDER BY ts DESC LIMIT ?",
            (task_id, limit),
        )
    else:
        cur = db._conn.execute(
            "SELECT * FROM dead_letter_events ORDER BY ts DESC LIMIT ?",
            (limit,),
        )

    results = []
    for row in cur.fetchall():
        results.append({
            "dl_id": row["dl_id"],
            "task_id": row["task_id"],
            "attempted_from": row["attempted_from"],
            "attempted_to": row["attempted_to"],
            "reason": row["reason"],
            "original_payload": json.loads(row["original_payload"]),
            "ts": row["ts"],
        })
    return results


def count_dead_letters(db) -> int:
    """Return total count of dead-letter entries for monitoring.

    Args:
        db: RouterDB instance.

    Returns:
        Total number of dead-letter entries.
    """
    cur = db._conn.execute("SELECT COUNT(*) FROM dead_letter_events")
    return cur.fetchone()[0]
