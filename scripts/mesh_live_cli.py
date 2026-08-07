#!/usr/bin/env python3
"""Read-only operator view over local or remote tmux sessions."""

import argparse
import json
import os
import pwd
import re
import socket
import subprocess
import sys
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Callable, Sequence


DEFAULT_WS_HOST = "sam@10.0.0.2"
DEFAULT_BOARD_LINES = 20
DEFAULT_PEEK_LINES = 120
MAX_CAPTURE_LINES = 2000
_FIELD_SEPARATOR = "\x1f"
_SAFE_USER = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*[$]?$")
_REMOTE_PAYLOAD = globals().get("_MESH_LIVE_REMOTE_PAYLOAD")


@dataclass(frozen=True)
class LiveSession:
    owner: str
    name: str
    created_at: int = 0
    activity_at: int = 0
    windows: int = 0
    attached: int = 0
    pane_id: str = ""
    pane_path: str = ""
    pane_command: str = ""
    pane_dead: bool = False
    role: str = ""
    repo_name: str = ""
    output: str = ""
    capture_error: str = ""

    @property
    def key(self) -> tuple[str, str]:
        return self.owner, self.name

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "LiveSession":
        return cls(
            owner=str(raw.get("owner") or ""),
            name=str(raw.get("name") or ""),
            created_at=_as_int(raw.get("created_at")),
            activity_at=_as_int(raw.get("activity_at")),
            windows=_as_int(raw.get("windows")),
            attached=_as_int(raw.get("attached")),
            pane_id=str(raw.get("pane_id") or ""),
            pane_path=str(raw.get("pane_path") or ""),
            pane_command=str(raw.get("pane_command") or ""),
            pane_dead=_as_bool(raw.get("pane_dead")),
            role=str(raw.get("role") or ""),
            repo_name=str(raw.get("repo_name") or ""),
            output=str(raw.get("output") or ""),
            capture_error=str(raw.get("capture_error") or ""),
        )


@dataclass(frozen=True)
class LiveEndpoint:
    host: str
    local: bool
    users: tuple[str, ...]


class LiveReadError(RuntimeError):
    pass


class SessionResolutionError(ValueError):
    pass


RequestFn = Callable[[LiveEndpoint, dict[str, Any]], dict[str, Any]]


class LiveClient:
    def __init__(self, endpoint: LiveEndpoint, request_fn: RequestFn | None = None) -> None:
        self.endpoint = endpoint
        self._request_fn = request_fn or request_endpoint

    def discover(self) -> tuple[list[LiveSession], list[str]]:
        response = self._request_fn(
            self.endpoint,
            {"op": "discover", "users": list(self.endpoint.users)},
        )
        sessions = [
            LiveSession.from_dict(item)
            for item in response.get("sessions", [])
            if isinstance(item, dict)
        ]
        sessions.sort(key=lambda item: (item.name.lower(), item.owner.lower()))
        warnings = [str(item) for item in response.get("warnings", []) if str(item).strip()]
        return sessions, warnings

    def capture(
        self,
        sessions: Sequence[LiveSession],
        lines: int,
    ) -> tuple[list[LiveSession], list[str]]:
        bounded_lines = validate_capture_lines(lines, allow_zero=True)
        if not sessions or bounded_lines == 0:
            return list(sessions), []
        targets = [
            {
                "owner": item.owner,
                "name": item.name,
                "pane_id": item.pane_id,
            }
            for item in sessions
        ]
        response = self._request_fn(
            self.endpoint,
            {"op": "capture", "targets": targets, "lines": bounded_lines},
        )
        captures = {
            (str(item.get("owner") or ""), str(item.get("name") or "")): item
            for item in response.get("captures", [])
            if isinstance(item, dict)
        }
        enriched: list[LiveSession] = []
        for session in sessions:
            capture = captures.get(session.key, {})
            enriched.append(
                replace(
                    session,
                    output=str(capture.get("output") or ""),
                    capture_error=str(capture.get("error") or ""),
                )
            )
        warnings = [str(item) for item in response.get("warnings", []) if str(item).strip()]
        return enriched, warnings


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _as_bool(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def validate_capture_lines(lines: int, *, allow_zero: bool) -> int:
    minimum = 0 if allow_zero else 1
    try:
        value = int(lines)
    except (TypeError, ValueError) as exc:
        raise ValueError("lines must be an integer") from exc
    if value < minimum or value > MAX_CAPTURE_LINES:
        raise ValueError(f"lines must be between {minimum} and {MAX_CAPTURE_LINES}")
    return value


def _current_username() -> str:
    try:
        return pwd.getpwuid(os.geteuid()).pw_name
    except (KeyError, OSError):
        return os.environ.get("USER", "").strip()


def _run_command(args: list[str], *, timeout: float = 10.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _user_exists(owner: str) -> bool:
    if not owner or not _SAFE_USER.fullmatch(owner):
        return False
    if owner == _current_username():
        return True
    try:
        proc = _run_command(["id", "-u", owner])
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def _tmux_prefix(owner: str) -> list[str] | None:
    if not _user_exists(owner):
        return None
    if owner == _current_username():
        return []
    return ["sudo", "-n", "-u", owner]


def _no_tmux_server(stderr: str) -> bool:
    lowered = str(stderr or "").lower()
    if "no server running" in lowered or "failed to connect to server" in lowered:
        return True
    return "error connecting to" in lowered and "no such file or directory" in lowered


def _tmux_environment(prefix: list[str], session_name: str, variable: str) -> str:
    try:
        proc = _run_command([*prefix, "tmux", "show-environment", "-t", session_name, variable])
    except (OSError, subprocess.SubprocessError):
        return ""
    if proc.returncode != 0:
        return ""
    value = proc.stdout.strip()
    marker = f"{variable}="
    return value[len(marker) :] if value.startswith(marker) else ""


def _discover_owner(owner: str) -> tuple[list[dict[str, Any]], list[str]]:
    prefix = _tmux_prefix(owner)
    if prefix is None:
        return [], []

    session_format = _FIELD_SEPARATOR.join(
        [
            "#{session_name}",
            "#{session_created}",
            "#{session_activity}",
            "#{session_windows}",
            "#{session_attached}",
        ]
    )
    try:
        proc = _run_command([*prefix, "tmux", "list-sessions", "-F", session_format])
    except FileNotFoundError:
        return [], [f"{owner}: tmux or sudo not found"]
    except subprocess.SubprocessError as exc:
        return [], [f"{owner}: unable to list tmux sessions: {exc}"]

    if proc.returncode != 0:
        if _no_tmux_server(proc.stderr):
            return [], []
        detail = (proc.stderr or proc.stdout or f"exit {proc.returncode}").strip()
        return [], [f"{owner}: unable to list tmux sessions: {detail}"]

    sessions: list[dict[str, Any]] = []
    pane_format = _FIELD_SEPARATOR.join(
        ["#{pane_id}", "#{pane_current_path}", "#{pane_current_command}", "#{pane_dead}"]
    )
    for row in proc.stdout.splitlines():
        parts = row.split(_FIELD_SEPARATOR)
        if len(parts) != 5 or not parts[0]:
            continue
        name, created_at, activity_at, windows, attached = parts
        pane_id = pane_path = pane_command = pane_dead = ""
        try:
            pane_proc = _run_command(
                [*prefix, "tmux", "display-message", "-p", "-t", name, pane_format]
            )
        except (OSError, subprocess.SubprocessError):
            pane_proc = None
        if pane_proc is not None and pane_proc.returncode == 0:
            pane_parts = pane_proc.stdout.rstrip("\n").split(_FIELD_SEPARATOR)
            if len(pane_parts) == 4:
                pane_id, pane_path, pane_command, pane_dead = pane_parts

        role = _tmux_environment(prefix, name, "MESH_UI_ROLE")
        repo_name = _tmux_environment(prefix, name, "MESH_UI_REPO_NAME")
        if not repo_name and pane_path:
            repo_name = Path(pane_path).name
        sessions.append(
            {
                "owner": owner,
                "name": name,
                "created_at": _as_int(created_at),
                "activity_at": _as_int(activity_at),
                "windows": _as_int(windows),
                "attached": _as_int(attached),
                "pane_id": pane_id,
                "pane_path": pane_path,
                "pane_command": pane_command,
                "pane_dead": _as_bool(pane_dead),
                "role": role,
                "repo_name": repo_name,
            }
        )
    return sessions, []


def _capture_target(target: dict[str, Any], lines: int) -> tuple[dict[str, str], list[str]]:
    owner = str(target.get("owner") or "")
    name = str(target.get("name") or "")
    pane_id = str(target.get("pane_id") or "")
    result = {"owner": owner, "name": name, "output": "", "error": ""}
    prefix = _tmux_prefix(owner)
    if prefix is None:
        result["error"] = "tmux owner is unavailable"
        return result, []
    tmux_target = pane_id or name
    try:
        proc = _run_command(
            [*prefix, "tmux", "capture-pane", "-p", "-S", f"-{lines}", "-t", tmux_target]
        )
    except (OSError, subprocess.SubprocessError) as exc:
        result["error"] = str(exc)
        return result, []
    if proc.returncode != 0:
        result["error"] = (proc.stderr or proc.stdout or f"exit {proc.returncode}").strip()
    else:
        result["output"] = proc.stdout.rstrip("\n")
    return result, []


def handle_reader_request(payload: dict[str, Any]) -> dict[str, Any]:
    operation = str(payload.get("op") or "")
    if operation == "discover":
        sessions: list[dict[str, Any]] = []
        warnings: list[str] = []
        seen_users: set[str] = set()
        for raw_owner in payload.get("users", []):
            owner = str(raw_owner or "").strip()
            if not owner or owner in seen_users:
                continue
            seen_users.add(owner)
            owner_sessions, owner_warnings = _discover_owner(owner)
            sessions.extend(owner_sessions)
            warnings.extend(owner_warnings)
        return {"sessions": sessions, "warnings": warnings}

    if operation == "capture":
        lines = validate_capture_lines(payload.get("lines", DEFAULT_BOARD_LINES), allow_zero=False)
        captures: list[dict[str, str]] = []
        warnings: list[str] = []
        for raw_target in payload.get("targets", []):
            if not isinstance(raw_target, dict):
                continue
            capture, capture_warnings = _capture_target(raw_target, lines)
            captures.append(capture)
            warnings.extend(capture_warnings)
        return {"captures": captures, "warnings": warnings}

    raise ValueError(f"unsupported read operation: {operation or '<empty>'}")


def _host_without_user(host: str) -> str:
    value = host.rsplit("@", 1)[-1].strip()
    if value.startswith("[") and "]" in value:
        return value[1 : value.index("]")]
    return value.split(":", 1)[0]


def host_is_local(host: str) -> bool:
    target = _host_without_user(host).lower()
    if target in {"", "localhost", "127.0.0.1", "::1"}:
        return True
    local_names = {socket.gethostname().lower(), socket.getfqdn().lower()}
    if target in local_names:
        return True
    connection = os.environ.get("SSH_CONNECTION", "").split()
    return len(connection) >= 3 and target == connection[2].lower()


def _ssh_options() -> list[str]:
    control_dir = Path(os.environ.get("MESH_SSH_CONTROL_DIR", "~/.ssh/cm")).expanduser()
    try:
        control_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    interval = os.environ.get("MESH_SSH_SERVER_ALIVE_INTERVAL", "15")
    count = os.environ.get("MESH_SSH_SERVER_ALIVE_COUNT_MAX", "12")
    persist = os.environ.get("MESH_SSH_CONTROL_PERSIST", "30m")
    values = [
        f"ServerAliveInterval={interval}",
        f"ServerAliveCountMax={count}",
        "TCPKeepAlive=yes",
        "ConnectTimeout=10",
        "ConnectionAttempts=3",
        "ControlMaster=auto",
        f"ControlPersist={persist}",
        f"ControlPath={control_dir}/%C",
        "IPQoS=none",
    ]
    result: list[str] = []
    for value in values:
        result.extend(["-o", value])
    return result


def _parse_json_response(stdout: str) -> dict[str, Any]:
    for line in reversed(str(stdout or "").splitlines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise LiveReadError("reader returned no JSON response")


def request_endpoint(endpoint: LiveEndpoint, payload: dict[str, Any]) -> dict[str, Any]:
    if endpoint.local:
        return handle_reader_request(payload)
    if not endpoint.host:
        raise LiveReadError("missing remote host")

    source = Path(__file__).read_text(encoding="utf-8")
    remote_source = f"_MESH_LIVE_REMOTE_PAYLOAD = {payload!r}\n{source}"
    try:
        proc = subprocess.run(
            ["ssh", *_ssh_options(), endpoint.host, "python3", "-"],
            input=remote_source,
            check=False,
            capture_output=True,
            text=True,
            timeout=45,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise LiveReadError(f"unable to run reader on {endpoint.host}: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or f"exit {proc.returncode}").strip()
        raise LiveReadError(f"reader failed on {endpoint.host}: {detail}")
    return _parse_json_response(proc.stdout)


def filter_sessions(sessions: Sequence[LiveSession], query: str) -> list[LiveSession]:
    needle = str(query or "").strip().lower()
    if not needle:
        return list(sessions)
    result: list[LiveSession] = []
    for session in sessions:
        fields = (
            session.owner,
            session.name,
            session.pane_path,
            session.pane_command,
            session.role,
            session.repo_name,
        )
        if any(needle in field.lower() for field in fields if field):
            result.append(session)
    return result


def resolve_session(
    sessions: Sequence[LiveSession],
    requested: str,
    *,
    owner: str = "",
) -> LiveSession:
    name = str(requested or "").strip()
    desired_owner = str(owner or "").strip()
    if not name:
        raise SessionResolutionError("missing tmux session name")
    candidates = [item for item in sessions if not desired_owner or item.owner == desired_owner]
    exact = [item for item in candidates if item.name == name]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        owners = ", ".join(sorted(item.owner for item in exact))
        raise SessionResolutionError(f"session '{name}' exists for multiple owners: {owners}")
    prefix = [item for item in candidates if item.name.startswith(name)]
    if len(prefix) == 1:
        return prefix[0]
    if len(prefix) > 1:
        matches = ", ".join(f"{item.owner}/{item.name}" for item in prefix)
        raise SessionResolutionError(f"session prefix '{name}' is ambiguous: {matches}")
    scope = f" for owner '{desired_owner}'" if desired_owner else ""
    raise SessionResolutionError(f"tmux session '{name}' not found{scope}")


def render_board(sessions: Sequence[LiveSession]) -> str:
    if not sessions:
        return "No live tmux sessions matched."
    blocks: list[str] = []
    for session in sessions:
        state = f"attached:{session.attached}" if session.attached else "detached"
        command = session.pane_command or "unknown"
        location = session.pane_path or session.repo_name or "unknown"
        role = f" | role={session.role}" if session.role else ""
        blocks.append(
            f"=== {session.owner}/{session.name} | {state} | windows={session.windows} "
            f"| cmd={command}{role} | {location} ==="
        )
        if session.capture_error:
            blocks.append(f"[capture error] {session.capture_error}")
        elif session.output:
            blocks.append(session.output)
        else:
            blocks.append("[no captured output]")
    return "\n\n".join(blocks)


def _default_users(host: str) -> tuple[str, ...]:
    configured = os.environ.get("MESH_LIVE_USERS", "").strip()
    raw_users: list[str] = []
    if configured:
        raw_users.extend(configured.split(","))
    else:
        if "@" in host:
            raw_users.append(host.rsplit("@", 1)[0])
        raw_users.extend(["sam", "mesh-worker", "mesh"])
        raw_users.append(_current_username())
    result: list[str] = []
    for raw in raw_users:
        user = raw.strip()
        if user and _SAFE_USER.fullmatch(user) and user not in result:
            result.append(user)
    return tuple(result)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read live tmux sessions without router or iTerm2.")
    parser.add_argument(
        "--host",
        default=os.environ.get("MESH_WS_HOST", DEFAULT_WS_HOST),
        help=f"SSH target hosting tmux. Default: MESH_WS_HOST or {DEFAULT_WS_HOST}",
    )
    parser.add_argument(
        "--local",
        action="store_true",
        default=_as_bool(os.environ.get("MESH_LIVE_LOCAL")),
        help="Read tmux on this machine instead of using SSH.",
    )
    parser.add_argument(
        "--users",
        default="",
        help="Comma-separated tmux owners. Default: login user, sam, mesh-worker, mesh.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    board = sub.add_parser("board", help="List live sessions with recent pane output.")
    board.add_argument("query", nargs="?", default="", help="Optional session/repo/owner filter.")
    board.add_argument("--lines", type=int, default=DEFAULT_BOARD_LINES)
    board.add_argument("--json", action="store_true", help="Emit structured JSON.")

    peek = sub.add_parser("peek", help="Capture recent output from one live session.")
    peek.add_argument("session", help="Exact session name or unique prefix.")
    peek.add_argument("lines", nargs="?", type=int, default=DEFAULT_PEEK_LINES)
    peek.add_argument("--owner", default="", help="Disambiguate sessions owned by different users.")
    peek.add_argument("--json", action="store_true", help="Emit structured JSON.")
    return parser.parse_args(argv)


def _endpoint_from_args(args: argparse.Namespace) -> LiveEndpoint:
    if args.users:
        users = tuple(
            user.strip()
            for user in args.users.split(",")
            if user.strip() and _SAFE_USER.fullmatch(user.strip())
        )
    else:
        users = _default_users(args.host)
    return LiveEndpoint(
        host=args.host,
        local=bool(args.local or host_is_local(args.host)),
        users=tuple(dict.fromkeys(users)),
    )


def _print_warnings(warnings: Sequence[str]) -> None:
    for warning in warnings:
        print(f"Warning: {warning}", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    if _REMOTE_PAYLOAD is not None:
        try:
            response = handle_reader_request(_REMOTE_PAYLOAD)
        except Exception as exc:
            response = {"error": str(exc)}
        print(json.dumps(response, separators=(",", ":")))
        return 0 if "error" not in response else 1

    args = _parse_args(argv)
    try:
        if args.cmd == "board":
            lines = validate_capture_lines(args.lines, allow_zero=True)
        else:
            lines = validate_capture_lines(args.lines, allow_zero=False)
        client = LiveClient(_endpoint_from_args(args))
        sessions, warnings = client.discover()
        _print_warnings(warnings)

        if args.cmd == "board":
            selected = filter_sessions(sessions, args.query)
            selected, capture_warnings = client.capture(selected, lines)
            _print_warnings(capture_warnings)
            if args.json:
                print(json.dumps({"sessions": [asdict(item) for item in selected]}, indent=2))
            else:
                print(render_board(selected))
            return 1 if any(item.capture_error for item in selected) else 0

        selected = resolve_session(sessions, args.session, owner=args.owner)
        captured, capture_warnings = client.capture([selected], lines)
        _print_warnings(capture_warnings)
        result = captured[0]
        if args.json:
            print(json.dumps(asdict(result), indent=2))
        elif result.capture_error:
            print(f"Error: {result.capture_error}", file=sys.stderr)
        else:
            print(result.output)
        return 1 if result.capture_error else 0
    except (LiveReadError, SessionResolutionError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
