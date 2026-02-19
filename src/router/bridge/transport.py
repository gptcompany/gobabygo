"""Pluggable transport layer for the event bridge.

Defines the EventTransport protocol and two implementations:
- InProcessTransport: direct DB write (testing / local dev)
- HttpTransport: HTTP POST to router (VPN production)
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

import requests

from src.router.db import RouterDB

logger = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@runtime_checkable
class EventTransport(Protocol):
    """Protocol for sending serialized CloudEvent JSON to the router."""

    def send(self, cloud_event_json: str) -> bool:
        """Send a serialized CloudEvent. Returns True on success."""
        ...


class InProcessTransport:
    """Direct DB write — for testing and single-machine development.

    Parses the CloudEvent JSON, extracts the data payload, and writes
    directly to task_events without foreign key enforcement (bridge events
    may arrive before their referenced tasks exist in the router).
    """

    def __init__(self, db: RouterDB) -> None:
        self.db = db

    def send(self, cloud_event_json: str) -> bool:
        """Parse CloudEvent JSON and write event to DB.

        Returns True on success, False on duplicate idempotency_key
        or parse error.
        """
        try:
            envelope = json.loads(cloud_event_json)
        except (json.JSONDecodeError, TypeError):
            logger.error("InProcessTransport: invalid JSON")
            return False

        data = envelope.get("data", {})
        task_id = data.get("task_id")
        if not task_id:
            task_id = data.get("run_id", "unknown")

        idempotency_key = data.get("idempotency_key", "")
        event_type = f"command.{data.get('event', 'unknown')}"
        ts = data.get("ts", _utc_now())
        payload_json = json.dumps(data, default=str)

        # Insert directly with FK temporarily disabled — bridge events
        # may reference tasks that don't exist yet in the router DB.
        try:
            self.db._conn.execute("PRAGMA foreign_keys=OFF")
            self.db._conn.execute(
                """INSERT INTO task_events
                (task_id, event_type, payload, idempotency_key, ts)
                VALUES (?, ?, ?, ?, ?)""",
                (task_id, event_type, payload_json, idempotency_key, ts),
            )
            self.db._conn.commit()
            return True
        except sqlite3.IntegrityError:
            self.db._conn.rollback()
            return False
        finally:
            self.db._conn.execute("PRAGMA foreign_keys=ON")


class HttpTransport:
    """HTTP POST to router — for VPN production deployment.

    Sends serialized CloudEvent JSON to the router's /events endpoint.
    Includes optional bearer token authentication.
    """

    def __init__(
        self,
        router_url: str,
        auth_token: str | None = None,
        timeout: float = 5.0,
    ) -> None:
        self.router_url = router_url.rstrip("/")
        self.auth_token = auth_token
        self.timeout = timeout

    def send(self, cloud_event_json: str) -> bool:
        """POST CloudEvent JSON to router /events endpoint.

        Returns True on 2xx response, False on any error.
        """
        url = f"{self.router_url}/events"
        headers = {"Content-Type": "application/cloudevents+json; charset=utf-8"}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"

        try:
            resp = requests.post(
                url,
                data=cloud_event_json,
                headers=headers,
                timeout=self.timeout,
            )
            if resp.status_code < 300:
                return True
            logger.warning(
                "HttpTransport: %s returned %d", url, resp.status_code
            )
            return False
        except requests.RequestException as e:
            logger.warning("HttpTransport: connection error: %s", e)
            return False
