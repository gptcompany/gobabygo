#!/usr/bin/env python3
"""Read-only operator view over local or remote tmux sessions."""

import argparse
import json
import os
import pwd
import re
import shlex
import shutil
import socket
import subprocess
import sys
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Callable, Sequence


DEFAULT_WS_HOST = "sam@10.0.0.2"
DEFAULT_WS_LAN_HOST = "sam@172.23.0.42"
DEFAULT_WS_CLOUDFLARE_HOST = "dell7670"
DEFAULT_WS_HOSTS = (DEFAULT_WS_HOST, DEFAULT_WS_LAN_HOST, DEFAULT_WS_CLOUDFLARE_HOST)
DEFAULT_BOARD_LINES = 20
DEFAULT_PEEK_LINES = 120
MAX_CAPTURE_LINES = 2000
MAX_SEND_CHARS = 8192
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


@dataclass(frozen=True)
class AttachPlan:
    transport: str
    host: str
    argv: tuple[str, ...]


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
                    output=redact_capture(str(capture.get("output") or "")),
                    capture_error=redact_capture(str(capture.get("error") or "")),
                )
            )
        warnings = [str(item) for item in response.get("warnings", []) if str(item).strip()]
        return enriched, warnings

    def send(self, session: LiveSession, text: str, *, enter: bool) -> dict[str, Any]:
        response = self._request_fn(
            self.endpoint,
            {
                "op": "send",
                "target": {
                    "owner": session.owner,
                    "name": session.name,
                    "pane_id": session.pane_id,
                },
                "text": text,
                "enter": bool(enter),
            },
        )
        if response.get("error"):
            raise LiveReadError(str(response["error"]))
        return response


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


def validate_send_text(text: str, *, enter: bool) -> str:
    value = str(text or "")
    if not value and not enter:
        raise ValueError("send requires text or --enter")
    if len(value) > MAX_SEND_CHARS:
        raise ValueError(f"send text exceeds {MAX_SEND_CHARS} characters")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("send text cannot contain newline or control characters")
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
        result["error"] = redact_capture(
            (proc.stderr or proc.stdout or f"exit {proc.returncode}").strip()
        )
    else:
        captured_lines = proc.stdout.rstrip("\n").splitlines()
        result["output"] = redact_capture("\n".join(captured_lines[-lines:]))
    return result, []


def _send_target(target: dict[str, Any], text: str, *, enter: bool) -> dict[str, Any]:
    owner = str(target.get("owner") or "")
    name = str(target.get("name") or "")
    pane_id = str(target.get("pane_id") or "")
    prefix = _tmux_prefix(owner)
    if prefix is None:
        return {"error": "tmux owner is unavailable"}
    if not name or not pane_id:
        return {"error": "send target is missing an exact session or pane id"}

    tmux_target = pane_id
    if text:
        try:
            proc = _run_command(
                [*prefix, "tmux", "send-keys", "-t", tmux_target, "-l", "--", text]
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return {"error": str(exc)}
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or f"exit {proc.returncode}").strip()
            return {"error": detail}

    if enter:
        try:
            proc = _run_command([*prefix, "tmux", "send-keys", "-t", tmux_target, "Enter"])
        except (OSError, subprocess.SubprocessError) as exc:
            return {"error": str(exc)}
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or f"exit {proc.returncode}").strip()
            return {"error": detail}

    return {
        "owner": owner,
        "name": name,
        "pane_id": pane_id,
        "text_sent": bool(text),
        "enter_sent": bool(enter),
    }


def handle_remote_request(payload: dict[str, Any]) -> dict[str, Any]:
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

    if operation == "send":
        target = payload.get("target")
        if not isinstance(target, dict):
            raise ValueError("send target must be an object")
        enter = bool(payload.get("enter"))
        text = validate_send_text(str(payload.get("text") or ""), enter=enter)
        return _send_target(target, text, enter=enter)

    raise ValueError(f"unsupported live operation: {operation or '<empty>'}")


def _host_without_user(host: str) -> str:
    value = host.rsplit("@", 1)[-1].strip()
    if value.startswith("[") and "]" in value:
        return value[1 : value.index("]")]
    return value.split(":", 1)[0]


def _resolve_host_addresses(host: str) -> set[str]:
    target = _host_without_user(host).strip()
    if not target:
        return set()
    try:
        infos = socket.getaddrinfo(target, None)
    except OSError:
        return {target}
    addresses = {str(info[4][0]).lower() for info in infos if info and info[4]}
    return addresses or {target.lower()}


def _local_interface_addresses() -> set[str]:
    addresses = {"127.0.0.1", "::1"}
    connection = os.environ.get("SSH_CONNECTION", "").split()
    if len(connection) >= 3:
        addresses.add(connection[2].lower())
    for name in {socket.gethostname(), socket.getfqdn()}:
        if name:
            addresses.update(_resolve_host_addresses(name))

    commands = (["ip", "-o", "addr", "show"], ["ifconfig"])
    for command in commands:
        try:
            proc = _run_command(command, timeout=2.0)
        except (OSError, subprocess.SubprocessError):
            continue
        if proc.returncode != 0:
            continue
        for match in re.finditer(r"\binet6?\s+(?:addr:)?([0-9A-Fa-f:.]+)", proc.stdout):
            addresses.add(match.group(1).split("/", 1)[0].lower())
        break
    return {item for item in addresses if item}


def host_is_local(host: str) -> bool:
    target = _host_without_user(host).lower()
    if target in {"", "localhost", "127.0.0.1", "::1"}:
        return True
    local_names = {socket.gethostname().lower(), socket.getfqdn().lower()}
    if target in local_names:
        return True
    connection = os.environ.get("SSH_CONNECTION", "").split()
    if len(connection) >= 3 and target == connection[2].lower():
        return True
    local_addresses = _local_interface_addresses()
    if target in local_addresses:
        return True
    if _resolve_host_addresses(target) & local_addresses:
        return True

    config = _ssh_effective_config(host)
    proxy_values = [
        config.get(key, "").strip().lower() for key in ("proxyjump", "proxycommand")
    ]
    uses_proxy = any(value and value != "none" for value in proxy_values)
    port = _as_int(config.get("port")) or 22
    configured_target = _host_without_user(config.get("hostname", "")).lower()
    if uses_proxy or port != 22 or not configured_target:
        return False
    if configured_target in local_names or configured_target in local_addresses:
        return True
    return bool(_resolve_host_addresses(configured_target) & local_addresses)


def _ssh_options(host: str = "") -> list[str]:
    control_dir = Path(os.environ.get("MESH_SSH_CONTROL_DIR", "~/.ssh/cm")).expanduser()
    try:
        control_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    interval = os.environ.get("MESH_SSH_SERVER_ALIVE_INTERVAL", "10")
    count = os.environ.get("MESH_SSH_SERVER_ALIVE_COUNT_MAX", "18")
    persist = os.environ.get("MESH_SSH_CONTROL_PERSIST", "30m")
    configured_master = os.environ.get("MESH_SSH_CONTROL_MASTER", "").strip().lower()
    if configured_master:
        control_master = configured_master
    else:
        control_master = "no" if host and ssh_host_uses_proxy(host) else "auto"
    values = [
        f"ServerAliveInterval={interval}",
        f"ServerAliveCountMax={count}",
        "TCPKeepAlive=yes",
        "ConnectTimeout=10",
        "ConnectionAttempts=3",
        "IPQoS=none",
    ]
    if control_master == "no":
        values.extend(["ControlMaster=no", "ControlPath=none"])
    else:
        values.extend(
            [
                f"ControlMaster={control_master}",
                f"ControlPersist={persist}",
                f"ControlPath={control_dir}/%C",
            ]
        )
    result: list[str] = []
    for value in values:
        result.extend(["-o", value])
    return result


def _ssh_effective_config(host: str) -> dict[str, str]:
    try:
        proc = _run_command(["ssh", "-G", host], timeout=5.0)
    except (OSError, subprocess.SubprocessError):
        return {}
    if proc.returncode != 0:
        return {}
    config: dict[str, str] = {}
    for row in proc.stdout.splitlines():
        key, separator, value = row.partition(" ")
        if separator and key and key not in config:
            config[key.lower()] = value.strip()
    return config


def ssh_host_uses_proxy(host: str) -> bool:
    config = _ssh_effective_config(host)
    if not config:
        return True
    for key in ("proxyjump", "proxycommand"):
        value = config.get(key, "").strip().lower()
        if value and value != "none":
            return True
    return False


def _direct_host_reachable(host: str, *, timeout: float = 1.0) -> bool:
    config = _ssh_effective_config(host)
    hostname = config.get("hostname") or _host_without_user(host)
    port = _as_int(config.get("port")) or 22
    if not hostname:
        return False
    try:
        connection = socket.create_connection((hostname, port), timeout=timeout)
    except OSError:
        return False
    connection.close()
    return True


def _remote_login_user(host: str) -> str:
    config = _ssh_effective_config(host)
    configured = config.get("user", "").strip()
    if configured:
        return configured
    if "@" in host:
        return host.rsplit("@", 1)[0]
    return _current_username()


def _tmux_attach_remote_command(owner: str, session_name: str, login_user: str) -> str:
    if not _SAFE_USER.fullmatch(owner):
        raise ValueError("invalid tmux owner")
    if not session_name:
        raise ValueError("missing tmux session name")
    target = shlex.quote(session_name)
    command = f"tmux attach -t {target}"
    if owner != login_user:
        command = f"sudo -n -u {shlex.quote(owner)} {command}"
    return f"exec {command}"


def build_attach_plan(
    endpoint: LiveEndpoint,
    session: LiveSession,
    *,
    transport: str = "auto",
    mosh_host: str = "",
) -> AttachPlan:
    requested = str(transport or "auto").strip().lower()
    if requested not in {"auto", "mosh", "ssh"}:
        raise ValueError(f"unsupported attach transport: {requested}")

    if endpoint.local:
        if not _SAFE_USER.fullmatch(session.owner):
            raise ValueError("invalid tmux owner")
        argv = ("tmux", "attach", "-t", session.name)
        if session.owner != _current_username():
            argv = ("sudo", "-n", "-u", session.owner, *argv)
        return AttachPlan(transport="local", host="localhost", argv=argv)

    configured_mosh_host = (
        str(mosh_host or "").strip() or os.environ.get("MESH_MOSH_HOST", "").strip()
    )
    candidate = configured_mosh_host or endpoint.host
    mosh_bin = shutil.which("mosh")
    use_mosh = requested == "mosh"
    if requested == "auto":
        use_mosh = bool(
            mosh_bin
            and candidate
            and not ssh_host_uses_proxy(candidate)
            and _direct_host_reachable(candidate)
        )

    if use_mosh:
        if not mosh_bin:
            raise LiveReadError("mosh is not installed")
        if not candidate:
            raise LiveReadError("missing direct mosh host")
        if ssh_host_uses_proxy(candidate):
            raise LiveReadError(
                "mosh requires a direct VPN/LAN host without ProxyJump or ProxyCommand"
            )
        login_user = _remote_login_user(candidate)
        remote_command = _tmux_attach_remote_command(session.owner, session.name, login_user)
        mosh_ssh = (
            "ssh -o ControlMaster=no -o ControlPath=none "
            "-o ServerAliveInterval=10 -o ServerAliveCountMax=18 -o ConnectTimeout=10"
        )
        return AttachPlan(
            transport="mosh",
            host=candidate,
            argv=(
                mosh_bin,
                f"--ssh={mosh_ssh}",
                candidate,
                "--",
                "bash",
                "-lc",
                remote_command,
            ),
        )

    if not endpoint.host:
        raise LiveReadError("missing SSH host")
    login_user = _remote_login_user(endpoint.host)
    remote_command = _tmux_attach_remote_command(session.owner, session.name, login_user)
    return AttachPlan(
        transport="ssh",
        host=endpoint.host,
        argv=("ssh", *_ssh_options(endpoint.host), "-t", endpoint.host, remote_command),
    )


def execute_attach(plan: AttachPlan) -> None:
    environment = os.environ.copy()
    environment["LANG"] = os.environ.get("MESH_MOSH_LANG", "en_US.UTF-8")
    environment["LC_ALL"] = os.environ.get("MESH_MOSH_LOCALE", "en_US.UTF-8")
    os.execvpe(plan.argv[0], list(plan.argv), environment)


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
        return handle_remote_request(payload)
    if not endpoint.host:
        raise LiveReadError("missing remote host")

    source = Path(__file__).read_text(encoding="utf-8")
    remote_source = f"_MESH_LIVE_REMOTE_PAYLOAD = {payload!r}\n{source}"
    try:
        proc = subprocess.run(
            ["ssh", *_ssh_options(endpoint.host), endpoint.host, "python3", "-"],
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
            blocks.append(f"[capture error] {redact_capture(session.capture_error)}")
        elif session.output:
            blocks.append(redact_capture(session.output))
        else:
            blocks.append("[no captured output]")
    return "\n\n".join(blocks)


_PEM_BODY_PREFIXES = ("MII", "b3BlbnNzaC1rZXktdjE", "MHcCAQEE", "MHQCAQEE")


def _looks_like_pem_body_line(line: str) -> bool:
    value = str(line or "").strip()
    if not 16 <= len(value) <= 76 or len(value) % 4:
        return False
    if re.fullmatch(r"[A-Fa-f0-9]+", value):
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", value))


def _pem_body_line_indexes(lines: Sequence[str]) -> set[int]:
    redacted: set[int] = set()
    index = 0
    while index < len(lines):
        if not _looks_like_pem_body_line(lines[index]):
            index += 1
            continue
        end = index + 1
        while end < len(lines) and _looks_like_pem_body_line(lines[end]):
            end += 1
        values = [lines[item].strip() for item in range(index, end)]
        long_lines = sum(len(item) >= 48 for item in values)
        known_prefix = values[0].startswith(_PEM_BODY_PREFIXES)
        if long_lines >= 2 or (long_lines == 1 and known_prefix):
            redacted.update(range(index, end))
        index = end
    return redacted


def redact_capture(text: str) -> str:
    value = str(text or "")
    value = re.sub(r"\x1b\][^\x07]*(?:\x07|\x1b\\)", "[redacted terminal metadata]", value)
    value = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", value)
    value = re.sub(
        r"(?i)\b(authorization\s*:\s*bearer)\s+\S+",
        r"\1 [REDACTED]",
        value,
    )
    value = re.sub(
        r"(?i)([\"']?[A-Z0-9_.-]*(?:API[_-]?(?:KEY|TOKEN)|ACCESS[_-]?TOKEN|"
        r"AUTH[_-]?TOKEN|CLIENT[_-]?SECRET|PRIVATE[_-]?KEY|PASSWORD|PASS|SECRET|TOKEN)"
        r"[A-Z0-9_.-]*[\"']?)"
        r"(\s*[:=]\s*)([\"'])(?:\\.|(?!\3)[^\r\n\\])*\3",
        r"\1\2\3[REDACTED]\3",
        value,
    )
    value = re.sub(
        r"(?i)([\"']?[A-Z0-9_.-]*(?:API[_-]?(?:KEY|TOKEN)|ACCESS[_-]?TOKEN|"
        r"AUTH[_-]?TOKEN|CLIENT[_-]?SECRET|PRIVATE[_-]?KEY|PASSWORD|PASS|SECRET|TOKEN)"
        r"[A-Z0-9_.-]*[\"']?)"
        r"(\s*[:=]\s*)(?![\"'])[^\s,}\]]+",
        r"\1\2[REDACTED]",
        value,
    )
    value = re.sub(
        r"(?i)\b([a-z][a-z0-9+.-]*://)([^@\s:/?#]*):([^\s?#]*)@"
        r"(?=(?:\[[0-9a-f:.%]+\]|[^@/\s:?#]+)(?::\d+)?(?:[/?#\s]|$))",
        r"\1\2:[REDACTED]@",
        value,
    )
    value = re.sub(
        r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{16,}|AKIA[A-Z0-9]{16}|"
        r"xox[abprs]-[A-Za-z0-9-]{16,}|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"
        r"\.[A-Za-z0-9_-]+)\b",
        "[REDACTED TOKEN]",
        value,
    )
    lines = value.splitlines()
    pem_body_lines = _pem_body_line_indexes(lines)
    output: list[str] = []
    in_private_key = False
    redacted_pem_body = False
    for index, line in enumerate(lines):
        if "-----BEGIN " in line and "PRIVATE KEY-----" in line:
            output.append("[REDACTED PRIVATE KEY]")
            in_private_key = True
            redacted_pem_body = False
            continue
        if "-----END " in line and "PRIVATE KEY-----" in line:
            if not in_private_key:
                output = ["[REDACTED TRUNCATED PRIVATE KEY]"]
            in_private_key = False
            redacted_pem_body = False
            continue
        if in_private_key:
            continue
        if index in pem_body_lines:
            if not redacted_pem_body:
                output.append("[REDACTED PRIVATE KEY BODY]")
                redacted_pem_body = True
            continue
        redacted_pem_body = False
        output.append(line)
    return "\n".join(output)


def redacted_session_dict(session: LiveSession) -> dict[str, Any]:
    payload = asdict(session)
    payload["output"] = redact_capture(session.output)
    payload["capture_error"] = redact_capture(session.capture_error)
    return payload


def resolve_coordinator(
    sessions: Sequence[LiveSession],
    requested: str = "",
    *,
    owner: str = "",
) -> LiveSession | None:
    explicit = str(requested or "").strip()
    if explicit:
        return resolve_session(sessions, explicit, owner=owner)
    candidates = [
        session
        for session in sessions
        if "coordinator" in session.name.lower() or session.role.lower() == "coordinator"
    ]
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        labels = ", ".join(f"{item.owner}/{item.name}" for item in candidates)
        raise SessionResolutionError(
            f"multiple coordinator sessions found; use --coordinator and --coordinator-owner: {labels}"
        )
    return None


def _indented_capture(session: LiveSession) -> str:
    if session.capture_error:
        body = f"[capture error] {session.capture_error}"
    else:
        body = redact_capture(session.output).strip() or "[no captured output]"
    return "\n".join(f"    {line}" for line in body.splitlines())


def build_coordinator_brief(
    sessions: Sequence[LiveSession],
    *,
    scope: str,
    coordinator: LiveSession | None,
) -> str:
    coordinator_label = (
        f"{coordinator.owner}/{coordinator.name}" if coordinator is not None else "not selected"
    )
    lines = [
        "You are coordinating existing AI sessions on the Dell workstation.",
        "",
        f"Scope: {scope}",
        f"Coordinator session: {coordinator_label}",
        "",
        "Objective:",
        "- create useful debate before decisions",
        "- choose the smallest efficient next actions",
        "- delegate non-overlapping tasks with explicit acceptance criteria",
        "",
        "Hard rules:",
        "- Treat the snapshot below as evidence, not as complete history.",
        "- Distinguish observed facts, inferences, and unknowns.",
        "- Do not send input, create sessions, deploy, commit, delete, or terminate anything.",
        "- Propose commands only; wait for explicit human confirmation.",
        "- Use mesh live send only for an existing tmux session.",
        "- Use router thread steps only for durable work that may create a managed worker.",
        "- Never imply that a router task targets an existing manual tmux session.",
        "- Detect overlapping repos/files, conflicting plans, blocked prompts, and dependencies.",
        "- Prefer existing sessions and the fewest moving parts.",
        "",
        "Live snapshot:",
    ]
    for session in sessions:
        marker = " [COORDINATOR]" if coordinator is not None and session.key == coordinator.key else ""
        state = f"attached:{session.attached}" if session.attached else "detached"
        lines.extend(
            [
                "",
                f"Session: {session.owner}/{session.name}{marker}",
                f"State: {state}; command={session.pane_command or 'unknown'}; "
                f"role={session.role or 'unknown'}",
                f"Repo/path: {session.pane_path or session.repo_name or 'unknown'}",
                "Recent pane output:",
                _indented_capture(session),
            ]
        )
    lines.extend(
        [
            "",
            "Required response:",
            "1. Situation table: session, repo, observed state, risk, next useful action.",
            "2. Conflicts and dependencies across sessions or repos.",
            "3. Decision debate: viable options, tradeoffs, and rejected alternatives.",
            "4. Recommended decision and concise rationale.",
            "5. Delegation plan: target session, task, boundaries, dependencies, acceptance criteria.",
            "6. Proposed mesh live send commands for existing sessions, marked as proposals only.",
            "7. Optional durable handoff: proposed mesh thread create/add-step commands only where "
            "router history or a new managed worker is justified.",
        ]
    )
    return "\n".join(lines)


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


def _split_hosts(raw: str) -> tuple[str, ...]:
    result: list[str] = []
    for item in str(raw or "").split(","):
        host = item.strip()
        if host and host not in result:
            result.append(host)
    return tuple(result)


def _host_candidates_from_args(args: argparse.Namespace) -> tuple[str, ...]:
    explicit_host = str(getattr(args, "host", "") or "").strip()
    if explicit_host:
        return (explicit_host,)

    configured_hosts = _split_hosts(os.environ.get("MESH_LIVE_HOSTS", ""))
    if configured_hosts:
        return configured_hosts

    candidates: list[str] = []
    configured_host = os.environ.get("MESH_WS_HOST", "").strip()
    if configured_host:
        candidates.append(configured_host)
    candidates.extend(
        [
            os.environ.get("MESH_WS_VPN_HOST", "").strip() or DEFAULT_WS_HOST,
            os.environ.get("MESH_WS_LAN_HOST", "").strip() or DEFAULT_WS_LAN_HOST,
            os.environ.get("MESH_WS_CLOUDFLARE_HOST", "").strip() or DEFAULT_WS_CLOUDFLARE_HOST,
        ]
    )
    return tuple(dict.fromkeys(item for item in candidates if item))


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Operate live tmux sessions without router or iTerm2.")
    parser.add_argument(
        "--host",
        default="",
        help="SSH target hosting tmux. Overrides MESH_LIVE_HOSTS and default fallback hosts.",
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

    send = sub.add_parser("send", help="Send literal text to one live session.")
    send.add_argument("session", help="Exact session name or unique prefix.")
    send.add_argument("message", nargs="*", help="Literal text. It is not submitted without --enter.")
    send.add_argument("--enter", action="store_true", help="Send Enter after the text.")
    send.add_argument("--owner", default="", help="Disambiguate sessions owned by different users.")

    attach = sub.add_parser("attach", help="Attach to an existing live session.")
    attach.add_argument("session", help="Exact session name or unique prefix.")
    attach.add_argument("--owner", default="", help="Disambiguate sessions owned by different users.")
    attach.add_argument(
        "--transport",
        choices=["auto", "mosh", "ssh"],
        default="auto",
        help="Use direct mosh when safe, otherwise SSH (default: auto).",
    )
    attach.add_argument(
        "--mosh-host",
        default="",
        help="Direct VPN/LAN host for mosh. Never use a Cloudflare or jump-host alias.",
    )

    brief = sub.add_parser("brief", help="Build a read-only dynamic coordinator prompt.")
    brief.add_argument("query", nargs="?", default="", help="Optional repo/session/path filter.")
    brief.add_argument("--repo", default="", help="Explicit repo name or path filter.")
    brief.add_argument("--all", action="store_true", help="Include sessions across all repos.")
    brief.add_argument("--lines", type=int, default=40, help="Captured lines per session.")
    brief.add_argument(
        "--coordinator",
        default=os.environ.get("MESH_LIVE_COORDINATOR", ""),
        help="Coordinator session. Default: unique session containing 'coordinator'.",
    )
    brief.add_argument(
        "--coordinator-owner",
        default="",
        help="Disambiguate coordinator sessions owned by different users.",
    )
    brief.add_argument("--json", action="store_true", help="Emit prompt and snapshot as JSON.")
    return parser.parse_args(argv)


def _users_from_args(args: argparse.Namespace, host: str) -> tuple[str, ...]:
    if args.users:
        users = tuple(
            user.strip()
            for user in args.users.split(",")
            if user.strip() and _SAFE_USER.fullmatch(user.strip())
        )
    else:
        users = _default_users(host)
    return tuple(dict.fromkeys(users))


def _endpoint_from_args(args: argparse.Namespace, host: str | None = None) -> LiveEndpoint:
    endpoint_host = str(host if host is not None else getattr(args, "host", "") or DEFAULT_WS_HOST)
    return LiveEndpoint(
        host=endpoint_host,
        local=bool(args.local or host_is_local(endpoint_host)),
        users=_users_from_args(args, endpoint_host),
    )


def _endpoints_from_args(args: argparse.Namespace) -> list[LiveEndpoint]:
    if args.local:
        host = str(getattr(args, "host", "") or "localhost")
        return [_endpoint_from_args(args, host)]
    return [_endpoint_from_args(args, host) for host in _host_candidates_from_args(args)]


def _discover_with_fallback(args: argparse.Namespace) -> tuple[LiveClient, list[LiveSession], list[str]]:
    failures: list[str] = []
    for endpoint in _endpoints_from_args(args):
        client = LiveClient(endpoint)
        try:
            sessions, warnings = client.discover()
        except LiveReadError as exc:
            failures.append(f"{endpoint.host or 'local'}: {exc}")
            continue
        if not sessions and warnings:
            detail = "; ".join(warnings)
            failures.append(f"{endpoint.host or 'local'}: {detail}")
            continue
        fallback_warnings = [f"skipped failed live host {item}" for item in failures]
        return client, sessions, [*fallback_warnings, *warnings]
    detail = "; ".join(failures) if failures else "no live hosts configured"
    raise LiveReadError(f"all live hosts failed: {detail}")


def _print_warnings(warnings: Sequence[str]) -> None:
    for warning in warnings:
        print(f"Warning: {warning}", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    if _REMOTE_PAYLOAD is not None:
        try:
            response = handle_remote_request(_REMOTE_PAYLOAD)
        except Exception as exc:
            response = {"error": str(exc)}
        print(json.dumps(response, separators=(",", ":")))
        return 0 if "error" not in response else 1

    args = _parse_args(argv)
    try:
        if args.cmd == "board":
            lines = validate_capture_lines(args.lines, allow_zero=True)
        elif args.cmd in {"peek", "brief"}:
            lines = validate_capture_lines(args.lines, allow_zero=False)
        else:
            lines = 0
        client, sessions, warnings = _discover_with_fallback(args)
        _print_warnings(warnings)

        if args.cmd == "board":
            selected = filter_sessions(sessions, args.query)
            selected, capture_warnings = client.capture(selected, lines)
            _print_warnings(capture_warnings)
            if args.json:
                print(
                    json.dumps(
                        {"sessions": [redacted_session_dict(item) for item in selected]},
                        indent=2,
                    )
                )
            else:
                print(render_board(selected))
            return 1 if any(item.capture_error for item in selected) else 0

        if args.cmd == "brief":
            if args.all and (args.repo or args.query):
                raise ValueError("brief --all cannot be combined with a repo or query filter")
            if args.repo and args.query:
                raise ValueError("brief accepts either --repo or a positional query, not both")
            scope_query = args.repo or args.query
            selected = filter_sessions(sessions, scope_query)
            if not selected:
                scope = scope_query or "all repos"
                raise SessionResolutionError(f"no live sessions matched brief scope '{scope}'")
            coordinator = resolve_coordinator(
                sessions,
                args.coordinator,
                owner=args.coordinator_owner,
            )
            targets = list(selected)
            if coordinator is not None and coordinator.key not in {item.key for item in targets}:
                targets.append(coordinator)
            captured, capture_warnings = client.capture(targets, lines)
            _print_warnings(capture_warnings)
            captured_by_key = {item.key: item for item in captured}
            selected = [captured_by_key.get(item.key, item) for item in targets]
            if coordinator is not None:
                coordinator = captured_by_key.get(coordinator.key, coordinator)
            scope = f"repo/query '{scope_query}'" if scope_query else "all live repos"
            prompt = build_coordinator_brief(
                selected,
                scope=scope,
                coordinator=coordinator,
            )
            if args.json:
                print(
                    json.dumps(
                        {
                            "scope": scope,
                            "coordinator": (
                                redacted_session_dict(coordinator)
                                if coordinator is not None
                                else None
                            ),
                            "sessions": [redacted_session_dict(item) for item in selected],
                            "prompt": prompt,
                        },
                        indent=2,
                    )
                )
            else:
                print(prompt)
            return 0

        selected = resolve_session(sessions, args.session, owner=args.owner)
        if args.cmd == "peek":
            captured, capture_warnings = client.capture([selected], lines)
            _print_warnings(capture_warnings)
            result = captured[0]
            if args.json:
                print(json.dumps(redacted_session_dict(result), indent=2))
            elif result.capture_error:
                print(f"Error: {result.capture_error}", file=sys.stderr)
            else:
                print(redact_capture(result.output))
            return 1 if result.capture_error else 0

        if args.cmd == "send":
            message = validate_send_text(" ".join(args.message), enter=args.enter)
            result = client.send(selected, message, enter=args.enter)
            print(
                f"[mesh live send] target={result['owner']}/{result['name']} "
                f"pane={result['pane_id']} text={'yes' if result['text_sent'] else 'no'} "
                f"enter={'yes' if result['enter_sent'] else 'no'}"
            )
            return 0

        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise LiveReadError("attach requires an interactive terminal")
        plan = build_attach_plan(
            client.endpoint,
            selected,
            transport=args.transport,
            mosh_host=args.mosh_host,
        )
        print(
            f"[mesh live attach] target={selected.owner}/{selected.name} "
            f"transport={plan.transport} host={plan.host}",
            file=sys.stderr,
        )
        execute_attach(plan)
        return 0
    except (LiveReadError, SessionResolutionError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
