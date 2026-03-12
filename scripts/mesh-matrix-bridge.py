#!/usr/bin/env python3
"""
Matrix notification bridge for AI Mesh Network.

Out-of-process service that polls the router HTTP API and emits
Matrix notifications when human intervention is needed.

Designed to run on muletto (or any host with network access to the router).
Bridge crash does NOT affect task execution.
"""

from __future__ import annotations

import json
import logging
import os
import re
import signal
import time
import hashlib
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlencode
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger("mesh-matrix-bridge")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BridgeConfig:
    router_url: str
    auth_token: str
    matrix_homeserver: str
    matrix_access_token: str
    matrix_default_room: str
    matrix_unrouted_room: str
    poll_interval_s: float = 10.0
    matrix_boss_room: str | None = None
    matrix_command_prefix: str = "!mesh"
    matrix_verifier_id: str = "matrix-operator"
    input_patterns: list[re.Pattern[str]] = field(default_factory=list)
    request_timeout_s: float = 10.0
    topology_path: str | None = None

    @classmethod
    def from_env(cls) -> BridgeConfig:
        def req(key: str) -> str:
            val = os.environ.get(key)
            if not val:
                raise SystemExit(f"Missing required env var: {key}")
            return val

        raw_patterns = os.environ.get(
            "MESH_MATRIX_INPUT_PATTERNS",
            r"approve|continue|press enter|y/n|\by\b/\bn\b|select|confirm|proceed",
        )
        combined = re.compile(raw_patterns, re.IGNORECASE)

        return cls(
            router_url=req("MESH_ROUTER_URL").rstrip("/"),
            auth_token=req("MESH_AUTH_TOKEN"),
            matrix_homeserver=req("MESH_MATRIX_HOMESERVER").rstrip("/"),
            matrix_access_token=req("MESH_MATRIX_ACCESS_TOKEN"),
            matrix_default_room=req("MESH_MATRIX_DEFAULT_ROOM"),
            matrix_unrouted_room=req("MESH_MATRIX_UNROUTED_ROOM"),
            poll_interval_s=float(os.environ.get("MESH_MATRIX_POLL_INTERVAL_S", "10")),
            matrix_boss_room=os.environ.get("MESH_MATRIX_BOSS_ROOM"),
            matrix_command_prefix=os.environ.get("MESH_MATRIX_COMMAND_PREFIX", "!mesh").strip() or "!mesh",
            matrix_verifier_id=os.environ.get("MESH_MATRIX_VERIFIER_ID", "matrix-operator").strip() or "matrix-operator",
            input_patterns=[combined],
            request_timeout_s=float(os.environ.get("MESH_MATRIX_REQUEST_TIMEOUT_S", "10")),
            topology_path=os.environ.get("MESH_TOPOLOGY_PATH"),
        )


# ---------------------------------------------------------------------------
# Local state (rebuildable on restart)
# ---------------------------------------------------------------------------

@dataclass
class BridgeState:
    """Lightweight local state, rebuilt from scratch on restart."""

    # session_id -> last seen message seq
    session_seqs: dict[str, int] = field(default_factory=dict)
    # set of task_ids currently known to be in review
    review_task_ids: set[str] = field(default_factory=set)
    # thread_id -> last known status
    thread_statuses: dict[str, str] = field(default_factory=dict)
    # set of open session_ids from last poll
    open_sessions: set[str] = field(default_factory=set)
    # session_ids currently waiting on human input; cleared on inbound operator input or close
    awaiting_input_sessions: set[str] = field(default_factory=set)
    # matrix sync token for inbound room commands
    matrix_since: str | None = None


@dataclass(frozen=True)
class MatrixCommand:
    room_id: str
    sender: str
    event_id: str
    command: str
    target: str
    text: str
    body: str


# ---------------------------------------------------------------------------
# Topology helper (optional)
# ---------------------------------------------------------------------------

def load_repo_rooms(topology_path: str | None) -> dict[str, str]:
    """Load repo -> notify_room mapping from topology file."""
    if not topology_path or not os.path.isfile(topology_path):
        return {}
    try:
        import yaml  # optional dep
        with open(topology_path) as f:
            topo = yaml.safe_load(f)
        rooms: dict[str, str] = {}
        for repo_name, repo_cfg in (topo.get("repos") or {}).items():
            room = repo_cfg.get("notify_room")
            if room:
                rooms[repo_name] = room
        boss_room = (topo.get("global") or {}).get("boss_notify_room")
        if boss_room:
            rooms["__boss__"] = boss_room
        unrouted_room = (topo.get("global") or {}).get("unrouted_notify_room")
        if unrouted_room:
            rooms["__unrouted__"] = unrouted_room
        return rooms
    except Exception as exc:
        logger.warning("Failed to load topology rooms: %s", exc)
        return {}


# ---------------------------------------------------------------------------
# Router HTTP client
# ---------------------------------------------------------------------------

class RouterClient:
    """Minimal HTTP client for router read endpoints."""

    def __init__(self, config: BridgeConfig) -> None:
        self._base = config.router_url
        self._token = config.auth_token
        self._timeout = config.request_timeout_s

    def _get(self, path: str, params: dict[str, str] | None = None) -> Any:
        url = f"{self._base}{path}"
        if params:
            qs = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
            if qs:
                url = f"{url}?{qs}"
        req = Request(url, method="GET")
        req.add_header("Authorization", f"Bearer {self._token}")
        req.add_header("Accept", "application/json")
        try:
            with urlopen(req, timeout=self._timeout) as resp:
                return json.loads(resp.read())
        except HTTPError as exc:
            logger.error("Router HTTP %s %s: %s", exc.code, url, exc.reason)
            return None
        except (URLError, TimeoutError) as exc:
            logger.error("Router unreachable %s: %s", url, exc)
            return None

    def _post(self, path: str, payload: dict[str, Any]) -> Any:
        url = f"{self._base}{path}"
        req = Request(url, data=json.dumps(payload).encode("utf-8"), method="POST")
        req.add_header("Authorization", f"Bearer {self._token}")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        try:
            with urlopen(req, timeout=self._timeout) as resp:
                return json.loads(resp.read())
        except HTTPError as exc:
            logger.error("Router HTTP %s %s: %s", exc.code, url, exc.reason)
            return None
        except (URLError, TimeoutError) as exc:
            logger.error("Router unreachable %s: %s", url, exc)
            return None

    def get_sessions(self, state: str | None = None) -> list[dict]:
        params = {}
        if state:
            params["state"] = state
        data = self._get("/sessions", params)
        return (data or {}).get("sessions", [])

    def get_session_messages(
        self, session_id: str, after_seq: int = 0, limit: int = 200
    ) -> list[dict]:
        data = self._get(
            "/sessions/messages",
            {"session_id": session_id, "after_seq": str(after_seq), "limit": str(limit)},
        )
        return (data or {}).get("messages", [])

    def get_tasks(self, status: str | None = None) -> list[dict]:
        params = {}
        if status:
            params["status"] = status
        data = self._get("/tasks", params)
        return (data or {}).get("tasks", [])

    def get_threads(self, status: str | None = None) -> list[dict]:
        params = {}
        if status:
            params["status"] = status
        data = self._get("/threads", params)
        return (data or {}).get("threads", [])

    def approve_review_task(self, task_id: str, verifier_id: str) -> Any:
        return self._post("/tasks/review/approve", {"task_id": task_id, "verifier_id": verifier_id})

    def reject_review_task(self, task_id: str, verifier_id: str, reason: str) -> Any:
        return self._post(
            "/tasks/review/reject",
            {"task_id": task_id, "verifier_id": verifier_id, "reason": reason},
        )

    def send_session_message(self, session_id: str, content: str) -> Any:
        return self._post(
            "/sessions/send",
            {
                "session_id": session_id,
                "direction": "in",
                "role": "operator",
                "content": content,
            },
        )

    def send_session_key(self, session_id: str, key: str, repeat: int = 1) -> Any:
        return self._post(
            "/sessions/send-key",
            {"session_id": session_id, "key": key, "repeat": repeat},
        )

    def signal_session(self, session_id: str, signal_name: str) -> Any:
        return self._post("/sessions/signal", {"session_id": session_id, "signal": signal_name})

    def record_notification(self, payload: dict[str, Any]) -> bool:
        """Record notification in router ledger with retry logic."""
        for attempt in range(2):
            data = self._post("/notifications", payload)
            if not data:
                if attempt == 0:
                    time.sleep(0.2)
                    continue
                return False

            status = data.get("status")
            if status in ("created", "duplicate"):
                return True

            if attempt == 0:
                time.sleep(0.2)
                continue

        return False


# ---------------------------------------------------------------------------
# Matrix client (minimal, no SDK dependency)
# ---------------------------------------------------------------------------

class MatrixClient:
    """Minimal Matrix client using client-server API directly."""

    def __init__(self, homeserver: str, access_token: str, timeout: float = 10.0) -> None:
        self._base = homeserver
        self._token = access_token
        self._timeout = timeout
        self._txn_id = int(time.time() * 1000)

    def send_message(self, room_id: str, body: str, html: str | None = None) -> bool:
        """Send a text message (with optional HTML) to a Matrix room."""
        self._txn_id += 1
        url = (
            f"{self._base}/_matrix/client/v3/rooms/{quote(room_id, safe='')}"
            f"/send/m.room.message/{self._txn_id}"
        )
        content: dict[str, str] = {
            "msgtype": "m.text",
            "body": body,
        }
        if html:
            content["format"] = "org.matrix.custom.html"
            content["formatted_body"] = html
        payload = json.dumps(content).encode()
        req = Request(url, data=payload, method="PUT")
        req.add_header("Authorization", f"Bearer {self._token}")
        req.add_header("Content-Type", "application/json")
        try:
            with urlopen(req, timeout=self._timeout) as resp:
                if resp.status in (200, 201):
                    return True
                logger.error("Matrix send failed: HTTP %s", resp.status)
                return False
        except (HTTPError, URLError, TimeoutError) as exc:
            logger.error("Matrix send error: %s", exc)
            return False

    def sync(self, since: str | None = None, timeout_ms: int = 0) -> dict[str, Any] | None:
        params = {"timeout": str(timeout_ms)}
        if since:
            params["since"] = since
        url = f"{self._base}/_matrix/client/v3/sync?{urlencode(params)}"
        req = Request(url, method="GET")
        req.add_header("Authorization", f"Bearer {self._token}")
        req.add_header("Accept", "application/json")
        try:
            with urlopen(req, timeout=self._timeout) as resp:
                return json.loads(resp.read())
        except (HTTPError, URLError, TimeoutError) as exc:
            logger.error("Matrix sync error: %s", exc)
            return None


# ---------------------------------------------------------------------------
# Notification rendering
# ---------------------------------------------------------------------------

def render_attach_command(session: dict) -> str:
    """Render a human-friendly attach command from session metadata."""
    meta = session.get("metadata") or {}
    kind = meta.get("attach_kind")
    target = meta.get("attach_target")
    if kind == "upterm" and target:
        return f"ssh {target.replace('ssh://', '')}"
    if kind == "ssh_tmux" and target:
        return f"ssh -t {target.replace('ssh://', '')} (tmux attach)"
    tmux = meta.get("tmux_session")
    if tmux:
        return f"tmux attach -t {tmux} (local only)"
    return "(no attach handle available)"


def render_notification(
    trigger: str,
    *,
    trace_id: str | None = None,
    command_prefix: str = "!mesh",
    repo: str | None = None,
    session: dict | None = None,
    task: dict | None = None,
    thread: dict | None = None,
    excerpt: str | None = None,
) -> tuple[str, str]:
    """Return (plain_text, html) notification pair."""
    emoji = {
        "input_requested": "\u2753",       # ❓
        "approval_needed": "\u2705",       # ✅
        "thread_blocked": "\u26a0\ufe0f",  # ⚠️
        "thread_failed": "\u274c",         # ❌
        "thread_completed": "\U0001f389",  # 🎉
    }.get(trigger, "\U0001f514")           # 🔔

    lines: list[str] = [f"{emoji} **{trigger.replace('_', ' ').title()}**"]
    html_lines: list[str] = [f"{emoji} <b>{trigger.replace('_', ' ').title()}</b>"]

    if repo:
        lines.append(f"Repo: `{repo}`")
        html_lines.append(f"Repo: <code>{repo}</code>")

    if trace_id:
        lines.append(f"Trace: `{trace_id}`")
        html_lines.append(f"Trace: <code>{trace_id}</code>")

    if session:
        sid = session.get("session_id", "?")[:12]
        lines.append(f"Session: `{sid}`")
        html_lines.append(f"Session: <code>{sid}</code>")
        attach = render_attach_command(session)
        lines.append(f"Attach: `{attach}`")
        html_lines.append(f"Attach: <code>{attach}</code>")

    if task:
        tid = task.get("task_id", "?")[:12]
        title = task.get("title", "")
        lines.append(f"Task: `{tid}` {title}")
        html_lines.append(f"Task: <code>{tid}</code> {title}")

    if thread:
        thid = thread.get("thread_id", "?")[:12]
        name = thread.get("name", "")
        status = thread.get("status", "?")
        lines.append(f"Thread: `{thid}` {name} [{status}]")
        html_lines.append(f"Thread: <code>{thid}</code> {name} [{status}]")

    if excerpt:
        short = excerpt[:200]
        lines.append(f"Excerpt: {short}")
        html_lines.append(f"Excerpt: {short}")

    if trigger == "input_requested":
        lines.append("_Quick text reply for simple input only; full control requires terminal attach._")
        html_lines.append("<em>Quick text reply for simple input only; full control requires terminal attach.</em>")
        if session:
            sid = session.get("session_id", "?")[:12]
            lines.append(f"Reply in room: `{command_prefix} send {sid} <text>` or `{command_prefix} enter {sid}`")
            html_lines.append(
                f"Reply in room: <code>{command_prefix} send {sid} &lt;text&gt;</code> or <code>{command_prefix} enter {sid}</code>"
            )
    if trigger == "approval_needed" and task:
        tid = task.get("task_id", "?")[:12]
        lines.append(f"Reply in room: `{command_prefix} approve {tid}` or `{command_prefix} reject {tid} <reason>`")
        html_lines.append(
            f"Reply in room: <code>{command_prefix} approve {tid}</code> or <code>{command_prefix} reject {tid} &lt;reason&gt;</code>"
        )

    plain = "\n".join(lines)
    html = "<br>".join(html_lines)
    return plain, html


def build_trace_id(
    trigger: str,
    *,
    session_id: str | None = None,
    message_seq: int | None = None,
    task_id: str | None = None,
    thread_id: str | None = None,
    thread_status: str | None = None,
) -> str:
    """Build deterministic trace id for notification events."""
    payload = {
        "trigger": trigger,
        "session_id": session_id,
        "message_seq": message_seq,
        "task_id": task_id,
        "thread_id": thread_id,
        "thread_status": thread_status,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
    return f"ntf_{digest}"


# ---------------------------------------------------------------------------
# Trigger detection
# ---------------------------------------------------------------------------

class TriggerDetector:
    """Detect notification triggers from router state changes."""

    ACTIVE_SESSION_TASK_STATUSES = frozenset({"assigned", "running", "review", "blocked"})

    def __init__(
        self,
        config: BridgeConfig,
        router: RouterClient,
        state: BridgeState,
    ) -> None:
        self._config = config
        self._router = router
        self._state = state

    def poll(self) -> list[dict[str, Any]]:
        """Run one poll cycle. Returns list of notification dicts."""
        notifications: list[dict[str, Any]] = []
        # Build once per cycle to avoid N x /tasks calls for open sessions.
        all_tasks = self._router.get_tasks()
        task_repo_map = {
            t.get("task_id"): t.get("repo")
            for t in all_tasks
            if t.get("task_id")
        }
        task_status_map = {
            t.get("task_id"): t.get("status")
            for t in all_tasks
            if t.get("task_id")
        }

        # --- Session messages: detect input_requested ---
        sessions = self._router.get_sessions(state="open")
        active_sessions = []
        for session in sessions:
            task_id = session.get("task_id")
            if not task_id:
                continue
            if task_status_map.get(task_id) not in self.ACTIVE_SESSION_TASK_STATUSES:
                continue
            active_sessions.append(session)

        current_open = {s["session_id"] for s in active_sessions}
        session_map = {s["session_id"]: s for s in active_sessions}

        for session in active_sessions:
            sid = session["session_id"]
            last_seq = self._state.session_seqs.get(sid, 0)
            messages = self._router.get_session_messages(sid, after_seq=last_seq)
            if not messages:
                continue

            max_seq = max(m.get("seq", 0) for m in messages)
            self._state.session_seqs[sid] = max_seq

            # Process in order so operator replies clear pending input state
            # before later outbound prompts in the same batch.
            for msg in sorted(messages, key=lambda item: item.get("seq", 0)):
                direction = msg.get("direction", "")
                if direction == "in":
                    self._state.awaiting_input_sessions.discard(sid)
                    continue
                if direction != "out":
                    continue
                content = msg.get("content", "")
                if sid in self._state.awaiting_input_sessions:
                    continue
                for pattern in self._config.input_patterns:
                    if pattern.search(content):
                        notifications.append({
                            "trigger": "input_requested",
                            "trace_id": build_trace_id(
                                "input_requested",
                                session_id=sid,
                                message_seq=msg.get("seq"),
                            ),
                            "repo": self._task_repo(session, task_repo_map),
                            "session": session,
                            "excerpt": content,
                        })
                        self._state.awaiting_input_sessions.add(sid)
                        break  # one notification per message

        # Clean up closed sessions
        closed = self._state.open_sessions - current_open
        for sid in closed:
            self._state.session_seqs.pop(sid, None)
            self._state.awaiting_input_sessions.discard(sid)
        self._state.open_sessions = current_open

        # --- Tasks in review: detect approval_needed ---
        review_tasks = self._router.get_tasks(status="review")
        current_review_ids = {t["task_id"] for t in review_tasks}
        new_review = current_review_ids - self._state.review_task_ids

        for task in review_tasks:
            if task["task_id"] in new_review:
                # Find associated session if any
                session_for_task = None
                task_session_id = task.get("session_id")
                if task_session_id and task_session_id in session_map:
                    session_for_task = session_map[task_session_id]

                notifications.append({
                    "trigger": "approval_needed",
                    "trace_id": build_trace_id(
                        "approval_needed",
                        task_id=task.get("task_id"),
                    ),
                    "repo": task.get("repo"),
                    "task": task,
                    "session": session_for_task,
                })

        self._state.review_task_ids = current_review_ids

        # --- Thread status transitions ---
        threads = self._router.get_threads()
        for thread in threads:
            tid = thread["thread_id"]
            new_status = thread.get("status", "")
            old_status = self._state.thread_statuses.get(tid)

            if old_status != new_status and new_status in ("failed", "completed", "blocked"):
                notifications.append({
                    "trigger": f"thread_{new_status}",
                    "trace_id": build_trace_id(
                        f"thread_{new_status}",
                        thread_id=tid,
                        thread_status=new_status,
                    ),
                    "thread": thread,
                })

            self._state.thread_statuses[tid] = new_status

        return notifications

    def _task_repo(self, session: dict, task_repo_map: dict[str, str | None]) -> str | None:
        """Extract repo from session's task if available."""
        task_id = session.get("task_id")
        if not task_id:
            return None
        return task_repo_map.get(task_id)


def parse_matrix_command(event: dict[str, Any], command_prefix: str) -> MatrixCommand | None:
    if event.get("type") != "m.room.message":
        return None
    content = event.get("content")
    if not isinstance(content, dict):
        return None
    body = str(content.get("body") or "").strip()
    if not body:
        return None

    prefixes: list[str] = []
    for candidate in (command_prefix, command_prefix.lstrip("!/"), "mesh"):
        candidate = candidate.strip()
        if candidate and candidate.lower() not in {item.lower() for item in prefixes}:
            prefixes.append(candidate)

    rest: str | None = None
    for prefix in prefixes:
        if body.lower() == prefix.lower():
            rest = ""
            break
        marker = f"{prefix} "
        if body.lower().startswith(marker.lower()):
            rest = body[len(marker):].strip()
            break
    if rest is None:
        return None

    if not rest:
        command = "help"
        target = ""
        text = ""
    else:
        parts = rest.split(maxsplit=2)
        command = parts[0].strip().lower()
        target = parts[1].strip() if len(parts) >= 2 else ""
        text = parts[2].strip() if len(parts) >= 3 else ""

    return MatrixCommand(
        room_id="",
        sender=str(event.get("sender") or "").strip(),
        event_id=str(event.get("event_id") or "").strip(),
        command=command,
        target=target,
        text=text,
        body=body,
    )


# ---------------------------------------------------------------------------
# Bridge main loop
# ---------------------------------------------------------------------------

class MatrixBridge:
    """Main bridge orchestrator."""

    def __init__(self, config: BridgeConfig) -> None:
        self.config = config
        self.state = BridgeState()
        self.router = RouterClient(config)
        self.matrix = MatrixClient(
            config.matrix_homeserver,
            config.matrix_access_token,
            config.request_timeout_s,
        )
        self.detector = TriggerDetector(config, self.router, self.state)
        self.repo_rooms = load_repo_rooms(config.topology_path)
        self._running = True

    def _room_repo_scope(self, room_id: str) -> str | None:
        for repo_name, mapped_room in self.repo_rooms.items():
            if repo_name.startswith("__"):
                continue
            if mapped_room == room_id:
                return repo_name
        return None

    def _allowed_command_rooms(self) -> set[str]:
        rooms = {
            self.config.matrix_default_room,
            self.config.matrix_unrouted_room,
        }
        if self.config.matrix_boss_room:
            rooms.add(self.config.matrix_boss_room)
        for room_id in self.repo_rooms.values():
            if room_id:
                rooms.add(room_id)
        return rooms

    def _seed_matrix_since(self) -> None:
        snapshot = self.matrix.sync(since=None, timeout_ms=0)
        if snapshot:
            self.state.matrix_since = snapshot.get("next_batch") or self.state.matrix_since

    def _poll_matrix_commands(self) -> list[MatrixCommand]:
        snapshot = self.matrix.sync(since=self.state.matrix_since, timeout_ms=0)
        if not snapshot:
            return []
        self.state.matrix_since = snapshot.get("next_batch") or self.state.matrix_since
        rooms = ((snapshot.get("rooms") or {}).get("join") or {})
        allowed_rooms = self._allowed_command_rooms()
        commands: list[MatrixCommand] = []
        for room_id, room_payload in rooms.items():
            if room_id not in allowed_rooms:
                continue
            for event in (((room_payload or {}).get("timeline") or {}).get("events") or []):
                parsed = parse_matrix_command(event, self.config.matrix_command_prefix)
                if parsed is None:
                    continue
                commands.append(
                    MatrixCommand(
                        room_id=room_id,
                        sender=parsed.sender,
                        event_id=parsed.event_id,
                        command=parsed.command,
                        target=parsed.target,
                        text=parsed.text,
                        body=parsed.body,
                    )
                )
        return commands

    def _repo_matches_scope(self, repo_value: str | None, repo_scope: str | None) -> bool:
        if not repo_scope:
            return True
        repo_text = str(repo_value or "").strip()
        if not repo_text:
            return False
        return repo_text == repo_scope or os.path.basename(repo_text.rstrip("/")) == repo_scope

    def _resolve_review_task(self, prefix: str, room_id: str) -> tuple[dict[str, Any] | None, str | None]:
        repo_scope = self._room_repo_scope(room_id)
        tasks = [
            task for task in self.router.get_tasks(status="review")
            if self._repo_matches_scope(task.get("repo"), repo_scope)
        ]
        needle = prefix.strip().lower()
        matches = [task for task in tasks if str(task.get("task_id") or "").lower().startswith(needle)]
        if not matches:
            return None, f"no review task matches '{prefix}'"
        if len(matches) > 1:
            options = ", ".join(str(task.get("task_id", ""))[:12] for task in matches[:5])
            return None, f"multiple review tasks match '{prefix}': {options}"
        return matches[0], None

    def _resolve_open_session(self, prefix: str, room_id: str) -> tuple[dict[str, Any] | None, str | None]:
        repo_scope = self._room_repo_scope(room_id)
        task_repo_map = {
            task.get("task_id"): task.get("repo")
            for task in self.router.get_tasks()
            if task.get("task_id")
        }
        sessions = []
        for session in self.router.get_sessions(state="open"):
            repo_value = task_repo_map.get(session.get("task_id"))
            if repo_value is None:
                meta = session.get("metadata") or {}
                repo_value = meta.get("working_dir")
            if self._repo_matches_scope(repo_value, repo_scope):
                sessions.append(session)

        needle = prefix.strip().lower()
        matches = [session for session in sessions if str(session.get("session_id") or "").lower().startswith(needle)]
        if not matches:
            return None, f"no open session matches '{prefix}'"
        if len(matches) > 1:
            options = ", ".join(str(session.get("session_id", ""))[:12] for session in matches[:5])
            return None, f"multiple open sessions match '{prefix}': {options}"
        return matches[0], None

    def _command_help(self) -> str:
        prefix = self.config.matrix_command_prefix
        return "\n".join(
            [
                "Mesh room commands:",
                f"- {prefix} approve <task-id-prefix>",
                f"- {prefix} reject <task-id-prefix> <reason>",
                f"- {prefix} send <session-id-prefix> <text>",
                f"- {prefix} enter <session-id-prefix>",
                f"- {prefix} interrupt <session-id-prefix>",
            ]
        )

    def _handle_matrix_command(self, cmd: MatrixCommand) -> str:
        prefix = self.config.matrix_command_prefix
        if cmd.command in {"", "help"}:
            return self._command_help()

        if cmd.command == "approve":
            if not cmd.target:
                return f"Usage: {prefix} approve <task-id-prefix>"
            task, error = self._resolve_review_task(cmd.target, cmd.room_id)
            if error:
                return f"Error: {error}"
            result = self.router.approve_review_task(task["task_id"], self.config.matrix_verifier_id)
            if not result:
                return "Error: router approval request failed"
            return f"Approved {task['task_id'][:12]} -> {str(result.get('status') or 'unknown')}"

        if cmd.command == "reject":
            if not cmd.target or not cmd.text:
                return f"Usage: {prefix} reject <task-id-prefix> <reason>"
            task, error = self._resolve_review_task(cmd.target, cmd.room_id)
            if error:
                return f"Error: {error}"
            result = self.router.reject_review_task(task["task_id"], self.config.matrix_verifier_id, cmd.text)
            if not result:
                return "Error: router rejection request failed"
            suffix = ""
            if result.get("fix_task_id"):
                suffix = f" fix={str(result['fix_task_id'])[:12]}"
            return f"Rejected {task['task_id'][:12]} -> {str(result.get('status') or 'unknown')}{suffix}"

        if cmd.command == "send":
            if not cmd.target or not cmd.text:
                return f"Usage: {prefix} send <session-id-prefix> <text>"
            session, error = self._resolve_open_session(cmd.target, cmd.room_id)
            if error:
                return f"Error: {error}"
            result = self.router.send_session_message(session["session_id"], cmd.text)
            if not result:
                return "Error: router session message request failed"
            return f"Sent message to {session['session_id'][:12]}"

        if cmd.command == "enter":
            if not cmd.target:
                return f"Usage: {prefix} enter <session-id-prefix>"
            session, error = self._resolve_open_session(cmd.target, cmd.room_id)
            if error:
                return f"Error: {error}"
            result = self.router.send_session_key(session["session_id"], "Enter")
            if not result:
                return "Error: router send-key request failed"
            return f"Sent Enter to {session['session_id'][:12]}"

        if cmd.command == "interrupt":
            if not cmd.target:
                return f"Usage: {prefix} interrupt <session-id-prefix>"
            session, error = self._resolve_open_session(cmd.target, cmd.room_id)
            if error:
                return f"Error: {error}"
            result = self.router.signal_session(session["session_id"], "interrupt")
            if not result:
                return "Error: router signal request failed"
            return f"Sent interrupt to {session['session_id'][:12]}"

        return f"Unknown command '{cmd.command}'. Use `{prefix} help`."

    def _process_inbound_commands(self) -> int:
        handled = 0
        for cmd in self._poll_matrix_commands():
            reply = self._handle_matrix_command(cmd)
            if self.matrix.send_message(cmd.room_id, reply):
                handled += 1
                logger.info("Handled matrix command room=%s sender=%s body=%s", cmd.room_id, cmd.sender, cmd.body)
            else:
                logger.warning("Failed to reply to matrix command room=%s body=%s", cmd.room_id, cmd.body)
        return handled

    def _resolve_room(self, repo: str | None) -> str:
        """Pick the right Matrix room for a notification."""
        if repo and repo in self.repo_rooms:
            return self.repo_rooms[repo]
        return self.repo_rooms.get("__unrouted__", self.config.matrix_unrouted_room)

    def _record_notification(
        self,
        *,
        notif: dict[str, Any],
        room_id: str,
        status: str,
        error: str | None = None,
    ) -> None:
        task = notif.get("task") or {}
        thread = notif.get("thread") or {}
        session = notif.get("session") or {}
        payload = {
            "trace_id": notif.get("trace_id"),
            "trigger": notif.get("trigger"),
            "room_id": room_id,
            "status": status,
            "repo": notif.get("repo"),
            "task_id": task.get("task_id"),
            "thread_id": thread.get("thread_id"),
            "session_id": session.get("session_id"),
            "error": error,
            "metadata": {
                "excerpt": (notif.get("excerpt") or "")[:200],
            },
        }
        if not self.router.record_notification(payload):
            logger.warning(
                "Failed to persist notification trace=%s trigger=%s",
                notif.get("trace_id"),
                notif.get("trigger"),
            )

    def run_once(self) -> int:
        """Run one poll cycle. Returns count of notifications sent."""
        notifications = self.detector.poll()
        sent = 0
        boss_room = self.config.matrix_boss_room or self.repo_rooms.get("__boss__")
        for notif in notifications:
            trigger = notif["trigger"]
            trace_id = notif.get("trace_id")
            repo = notif.get("repo")
            room = self._resolve_room(repo)

            plain, html = render_notification(
                trigger,
                trace_id=trace_id,
                command_prefix=self.config.matrix_command_prefix,
                repo=repo,
                session=notif.get("session"),
                task=notif.get("task"),
                thread=notif.get("thread"),
                excerpt=notif.get("excerpt"),
            )

            if self.matrix.send_message(room, plain, html):
                sent += 1
                logger.info("Sent %s trace=%s to %s", trigger, trace_id, room)
                self._record_notification(notif=notif, room_id=room, status="sent")

                # Also notify boss room for critical triggers
                if trigger in ("thread_failed", "approval_needed") and boss_room:
                    boss_ok = self.matrix.send_message(boss_room, plain, html)
                    self._record_notification(
                        notif=notif,
                        room_id=boss_room,
                        status="sent" if boss_ok else "failed",
                        error=None if boss_ok else "matrix_send_failed",
                    )
            else:
                logger.warning("Failed to send %s trace=%s to %s", trigger, trace_id, room)
                self._record_notification(
                    notif=notif,
                    room_id=room,
                    status="failed",
                    error="matrix_send_failed",
                )

        handled_commands = self._process_inbound_commands()
        if handled_commands:
            logger.info("Cycle: %d Matrix commands handled", handled_commands)

        return sent

    def run(self) -> None:
        """Main loop. Runs until SIGINT/SIGTERM."""
        logger.info(
            "Bridge started: router=%s interval=%.1fs rooms=%d",
            self.config.router_url,
            self.config.poll_interval_s,
            len(self.repo_rooms) + 1,
        )

        # Seed state on first poll (no notifications emitted for existing state)
        self._seed_state()

        while self._running:
            try:
                count = self.run_once()
                if count:
                    logger.info("Cycle: %d notifications sent", count)
            except Exception:
                logger.exception("Poll cycle error (non-fatal)")
            time.sleep(self.config.poll_interval_s)

        logger.info("Bridge stopped")

    def _seed_state(self) -> None:
        """Snapshot current state to avoid spurious notifications on startup."""
        logger.info("Seeding state from router...")

        sessions = self.router.get_sessions(state="open")
        self.state.open_sessions = {s["session_id"] for s in sessions}
        for s in sessions:
            # Mark latest seq as seen (drain pages to avoid startup spam).
            latest_seq = self._get_latest_session_seq(s["session_id"])
            if latest_seq > 0:
                self.state.session_seqs[s["session_id"]] = latest_seq

        review_tasks = self.router.get_tasks(status="review")
        self.state.review_task_ids = {t["task_id"] for t in review_tasks}

        threads = self.router.get_threads()
        for t in threads:
            self.state.thread_statuses[t["thread_id"]] = t.get("status", "")

        self._seed_matrix_since()

        logger.info(
            "Seeded: %d sessions, %d review tasks, %d threads",
            len(self.state.open_sessions),
            len(self.state.review_task_ids),
            len(self.state.thread_statuses),
        )

    def _get_latest_session_seq(self, session_id: str) -> int:
        """Return latest known message seq for a session."""
        cursor = 0
        while True:
            msgs = self.router.get_session_messages(session_id, after_seq=cursor, limit=1000)
            if not msgs:
                return cursor
            max_seq = max(m.get("seq", 0) for m in msgs)
            if max_seq <= cursor:
                return cursor
            cursor = max_seq
            # Final page.
            if len(msgs) < 1000:
                return cursor

    def stop(self) -> None:
        self._running = False


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    config = BridgeConfig.from_env()
    bridge = MatrixBridge(config)

    def _shutdown(signum: int, _frame: Any) -> None:
        logger.info("Signal %s received, shutting down", signum)
        bridge.stop()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    bridge.run()


if __name__ == "__main__":
    main()
