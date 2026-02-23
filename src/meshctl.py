"""meshctl -- Mesh operator CLI.

Standalone HTTP client for inspecting and managing the mesh router.
No imports from src.router.* -- this is a pure HTTP client using only
argparse (stdlib) and requests.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

import requests

# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------


def _base_url() -> str:
    """Read MESH_ROUTER_URL from env, default http://localhost:8780."""
    url = os.environ.get("MESH_ROUTER_URL", "http://localhost:8780")
    return url.rstrip("/")


def _headers() -> dict[str, str]:
    """Return auth header dict. Empty if MESH_AUTH_TOKEN is not set."""
    token = os.environ.get("MESH_AUTH_TOKEN", "")
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


# ---------------------------------------------------------------------------
# Time formatting helpers
# ---------------------------------------------------------------------------


def _format_age(iso_timestamp: str | None) -> str:
    """Convert ISO-8601 timestamp to relative age string.

    Examples: "2s ago", "45s ago", "3m ago", "1h 15m ago", "2d ago".
    Returns "n/a" if parse fails or timestamp is None/empty.
    """
    if not iso_timestamp:
        return "n/a"
    try:
        ts = datetime.fromisoformat(iso_timestamp)
        delta = datetime.now(timezone.utc) - ts
        total_seconds = int(delta.total_seconds())
        if total_seconds < 0:
            total_seconds = 0
        if total_seconds < 60:
            return f"{total_seconds}s ago"
        if total_seconds < 3600:
            minutes = total_seconds // 60
            return f"{minutes}m ago"
        if total_seconds < 86400:
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            if minutes:
                return f"{hours}h {minutes}m ago"
            return f"{hours}h ago"
        days = total_seconds // 86400
        return f"{days}d ago"
    except (ValueError, TypeError, OSError):
        return "n/a"


def _format_duration(seconds: float) -> str:
    """Convert seconds to compact duration string.

    Examples: "12s", "3m12s", "1h5m", "2h15m".
    No "ago" suffix (used for task age and uptime).
    """
    total = int(seconds)
    if total < 60:
        return f"{total}s"
    if total < 3600:
        m = total // 60
        s = total % 60
        if s:
            return f"{m}m{s}s"
        return f"{m}m"
    h = total // 3600
    m = (total % 3600) // 60
    if m:
        return f"{h}h{m}m"
    return f"{h}h"


# ---------------------------------------------------------------------------
# Status command
# ---------------------------------------------------------------------------


def cmd_status(args: argparse.Namespace) -> None:
    """Show mesh worker state and queue summary."""
    base = _base_url()
    headers = _headers()

    # Fetch workers
    try:
        workers_resp = requests.get(f"{base}/workers", headers=headers, timeout=10)
    except requests.ConnectionError as e:
        print(f"Error: Cannot connect to mesh router at {base} -- {e}", file=sys.stderr)
        sys.exit(1)

    if workers_resp.status_code == 401:
        print("Error: Authentication failed. Set MESH_AUTH_TOKEN.", file=sys.stderr)
        sys.exit(1)
    if workers_resp.status_code != 200:
        print(f"Error: /workers returned {workers_resp.status_code}", file=sys.stderr)
        sys.exit(1)

    # Fetch health
    try:
        health_resp = requests.get(f"{base}/health", headers=headers, timeout=10)
    except requests.ConnectionError as e:
        print(f"Error: Cannot connect to mesh router at {base} -- {e}", file=sys.stderr)
        sys.exit(1)

    if health_resp.status_code != 200:
        print(f"Error: /health returned {health_resp.status_code}", file=sys.stderr)
        sys.exit(1)

    workers_data = workers_resp.json()
    health_data = health_resp.json()

    # --json: raw combined output
    if args.json_output:
        combined = {"workers": workers_data.get("workers", []), "health": health_data}
        print(json.dumps(combined, indent=2))
        return

    # Human-readable table
    workers = workers_data.get("workers", [])
    if not workers:
        print("No workers registered.")
    else:
        print("WORKERS")
        print(
            f"{'ID':<10} {'MACHINE':<12} {'TYPE':<8} {'STATUS':<10} "
            f"{'LAST HB':<12} TASKS"
        )
        for w in workers:
            wid = w.get("worker_id", "?")[:8]
            machine = w.get("machine", "?")[:12]
            cli_type = w.get("cli_type", "?")[:8]
            status = w.get("status", "?")[:10]
            last_hb = _format_age(w.get("last_heartbeat"))
            running_tasks = w.get("running_tasks", [])
            if running_tasks:
                count = len(running_tasks)
                oldest_age = max(t.get("age_s", 0) for t in running_tasks)
                tasks_str = f"{count} running ({_format_duration(oldest_age)})"
            else:
                tasks_str = "-"
            print(
                f"{wid:<10} {machine:<12} {cli_type:<8} {status:<10} "
                f"{last_hb:<12} {tasks_str}"
            )

    # Queue summary from /health
    queue_depth = health_data.get("queue_depth", 0)
    worker_count = health_data.get("workers", 0)
    uptime_s = health_data.get("uptime_s", 0)

    print()
    print("QUEUE")
    print(f"Queued: {queue_depth} | Workers: {worker_count}")
    print(f"Uptime: {_format_duration(uptime_s)}")


# ---------------------------------------------------------------------------
# Drain command
# ---------------------------------------------------------------------------


def cmd_drain(args: argparse.Namespace) -> None:
    """Drain a worker: POST /workers/<id>/drain, then poll until offline."""
    base = _base_url()
    headers = _headers()
    worker_id = args.worker_id
    timeout = args.timeout  # default 300s

    # Initiate drain
    try:
        resp = requests.post(
            f"{base}/workers/{worker_id}/drain", headers=headers, timeout=10
        )
    except requests.ConnectionError as e:
        print(
            f"Error: Cannot connect to mesh router at {base} -- {e}",
            file=sys.stderr,
        )
        sys.exit(1)

    if resp.status_code == 404:
        print(f"Error: Worker '{worker_id}' not found.", file=sys.stderr)
        sys.exit(1)
    elif resp.status_code == 409:
        detail = resp.json().get("detail", resp.json().get("error", "conflict"))
        print(
            f"Error: Cannot drain worker '{worker_id}': {detail}", file=sys.stderr
        )
        sys.exit(1)
    elif resp.status_code == 401:
        print("Error: Authentication failed. Set MESH_AUTH_TOKEN.", file=sys.stderr)
        sys.exit(1)
    elif resp.status_code != 202:
        print(
            f"Error: Unexpected response {resp.status_code}: {resp.text}",
            file=sys.stderr,
        )
        sys.exit(1)

    data = resp.json()
    status = data.get("status", "")

    # If worker drained immediately (was idle, no tasks)
    if status == "drained_immediately":
        print(
            f"Worker {worker_id} drained and retired. (was idle, no tasks)"
        )
        return

    # Poll GET /workers/<id> every 2s until status == "offline" or timeout
    print(f"Draining worker {worker_id}...")
    start = time.monotonic()
    poll_interval = 2

    while True:
        elapsed = time.monotonic() - start
        if elapsed >= timeout:
            print(
                f"Warning: Drain timed out after {timeout}s. "
                "Worker may still be draining on server.",
                file=sys.stderr,
            )
            sys.exit(1)

        time.sleep(poll_interval)

        try:
            resp = requests.get(
                f"{base}/workers/{worker_id}", headers=headers, timeout=10
            )
        except requests.ConnectionError:
            print(
                "Warning: Lost connection to router during drain polling.",
                file=sys.stderr,
            )
            sys.exit(1)

        if resp.status_code == 404:
            # Worker already gone (deregistered)
            print(f"Worker {worker_id} drained and retired.")
            return

        if resp.status_code != 200:
            print(
                f"Warning: Poll returned {resp.status_code}, retrying...",
                file=sys.stderr,
            )
            continue

        worker = resp.json()
        worker_status = worker.get("status", "")
        running = worker.get("running_tasks", [])

        if worker_status == "offline":
            print(f"Worker {worker_id} drained and retired.")
            return

        # Show progress
        task_count = len(running)
        if task_count > 0:
            print(f"  Waiting for {task_count} task(s)...")
        else:
            print(f"  Status: {worker_status}, waiting...")


# ---------------------------------------------------------------------------
# Submit command
# ---------------------------------------------------------------------------


def cmd_submit(args: argparse.Namespace) -> None:
    """Submit a new task via POST /tasks."""
    base = _base_url()
    headers = _headers()
    headers["Content-Type"] = "application/json"

    body: dict[str, object] = {"title": args.title}
    if args.cli:
        body["target_cli"] = args.cli
    if args.account:
        body["target_account"] = args.account
    if args.phase:
        body["phase"] = args.phase
    if args.priority is not None:
        body["priority"] = args.priority
    if args.payload:
        try:
            body["payload"] = json.loads(args.payload)
        except json.JSONDecodeError:
            print("Error: --payload must be valid JSON", file=sys.stderr)
            sys.exit(1)

    try:
        resp = requests.post(f"{base}/tasks", json=body, headers=headers, timeout=10)
    except requests.ConnectionError as e:
        print(f"Error: Cannot connect to mesh router at {base} -- {e}", file=sys.stderr)
        sys.exit(1)

    if resp.status_code == 201:
        data = resp.json()
        print(f"Task created: {data.get('task_id', '?')}")
    elif resp.status_code == 401:
        print("Error: Authentication failed. Set MESH_AUTH_TOKEN.", file=sys.stderr)
        sys.exit(1)
    elif resp.status_code == 409:
        print(f"Error: Duplicate task -- {resp.json().get('detail', '')}", file=sys.stderr)
        sys.exit(1)
    else:
        print(f"Error: {resp.status_code} -- {resp.text}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

parser = argparse.ArgumentParser(prog="meshctl", description="Mesh operator CLI")
sub = parser.add_subparsers(dest="command")

status_parser = sub.add_parser("status", help="Show mesh state")
status_parser.add_argument(
    "--json",
    action="store_true",
    dest="json_output",
    help="Machine-readable JSON output",
)

drain_parser = sub.add_parser("drain", help="Gracefully drain a worker")
drain_parser.add_argument("worker_id", help="Worker ID to drain")
drain_parser.add_argument(
    "--timeout",
    type=int,
    default=300,
    help="Timeout in seconds (default: 300)",
)

submit_parser = sub.add_parser("submit", help="Submit a new task")
submit_parser.add_argument("--title", required=True, help="Task title")
submit_parser.add_argument("--cli", default=None, help="Target CLI type (claude/codex/gemini)")
submit_parser.add_argument("--account", default=None, help="Target account profile")
submit_parser.add_argument("--phase", default=None, help="Task phase")
submit_parser.add_argument("--priority", type=int, default=None, help="Priority (higher = first)")
submit_parser.add_argument("--payload", default=None, help="JSON payload string")

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parsed_args = parser.parse_args()
    if parsed_args.command == "status":
        cmd_status(parsed_args)
    elif parsed_args.command == "drain":
        cmd_drain(parsed_args)
    elif parsed_args.command == "submit":
        cmd_submit(parsed_args)
    else:
        parser.print_help()
        sys.exit(1)
