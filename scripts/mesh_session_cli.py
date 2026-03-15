#!/usr/bin/env python3
"""List and resolve live mesh sessions from the router API."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlparse
from urllib.request import Request, urlopen

try:
    import yaml
except ImportError:  # pragma: no cover - optional dependency in ad-hoc environments
    yaml = None


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _extract_env_value(text: str, key: str) -> str:
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if not line.startswith(f"{key}="):
            continue
        value = line.split("=", 1)[1].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        return value
    return ""


def load_router_env() -> tuple[str, str]:
    router_url = os.environ.get("MESH_ROUTER_URL", "").strip()
    auth_token = os.environ.get("MESH_AUTH_TOKEN", "").strip()
    if router_url and auth_token:
        return router_url, auth_token

    candidates = [
        Path.home() / ".mesh" / "router.env",
        Path.home() / ".mesh" / ".env.mesh",
        Path("/etc/mesh-worker/common.env"),
    ]
    for path in candidates:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if not router_url:
            router_url = _extract_env_value(text, "MESH_ROUTER_URL")
        if not auth_token:
            auth_token = _extract_env_value(text, "MESH_AUTH_TOKEN")
        if router_url and auth_token:
            return router_url, auth_token
    return "", ""


def router_get_json(router_url: str, auth_token: str, path: str) -> Any:
    req = Request(router_url.rstrip("/") + path)
    req.add_header("Authorization", f"Bearer {auth_token}")
    with urlopen(req, timeout=10) as resp:
        return json.load(resp)


def _load_provider_session_users(config_path: str | None = None) -> dict[str, str]:
    if yaml is None:
        return {}
    path_value = config_path or str(_repo_root() / "mapping" / "provider_runtime.yaml")
    path = Path(path_value)
    if not path.is_file():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}

    providers = raw.get("providers")
    if not isinstance(providers, dict):
        return {}

    users: dict[str, str] = {}
    for cli_type, entry in providers.items():
        if not isinstance(entry, dict):
            continue
        user = str(entry.get("session_service_user", "")).strip()
        if user:
            users[str(cli_type).strip()] = user
    return users


def _basename(value: str) -> str:
    if not value:
        return ""
    return os.path.basename(value.rstrip("/"))


def _short(value: str, width: int) -> str:
    text = value.strip()
    if len(text) <= width:
        return text.ljust(width)
    if width <= 3:
        return text[:width]
    return (text[: width - 3] + "...").ljust(width)


@dataclass(frozen=True)
class SessionChoice:
    session_id: str
    worker_id: str
    cli_type: str
    account_profile: str
    state: str
    task_id: str
    task_status: str
    thread_id: str
    thread_name: str
    thread_status: str
    repo: str
    repo_name: str
    role: str
    title: str
    updated_at: str
    tmux_session: str
    attach_kind: str
    attach_target: str
    attach_owner: str


_ACTIVE_TASK_STATUSES = {"queued", "assigned", "blocked", "running", "review"}


def build_session_choices(
    router_url: str,
    auth_token: str,
    *,
    state: str = "open",
    provider_users: dict[str, str] | None = None,
) -> list[SessionChoice]:
    state_q = "" if state == "all" else f"state={quote(state)}&"
    payload = router_get_json(router_url, auth_token, f"/sessions?{state_q}limit=200")
    sessions = payload.get("sessions") if isinstance(payload, dict) else None
    if not isinstance(sessions, list):
        return []

    provider_users = provider_users or _load_provider_session_users()
    task_cache: dict[str, dict[str, Any] | None] = {}
    thread_cache: dict[str, dict[str, Any] | None] = {}
    choices: list[SessionChoice] = []

    for session in sessions:
        if not isinstance(session, dict):
            continue
        session_id = str(session.get("session_id", "")).strip()
        if not session_id:
            continue
        metadata = session.get("metadata") if isinstance(session.get("metadata"), dict) else {}
        task_id = str(session.get("task_id") or "").strip()
        task: dict[str, Any] | None = None
        if task_id:
            if task_id not in task_cache:
                try:
                    fetched = router_get_json(router_url, auth_token, f"/tasks/{quote(task_id)}")
                except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
                    fetched = None
                task_cache[task_id] = fetched if isinstance(fetched, dict) else None
            task = task_cache.get(task_id)

        thread_id = str((task or {}).get("thread_id") or "").strip()
        thread: dict[str, Any] | None = None
        if thread_id:
            if thread_id not in thread_cache:
                try:
                    fetched_thread = router_get_json(router_url, auth_token, f"/threads/{quote(thread_id)}")
                except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
                    fetched_thread = None
                thread_cache[thread_id] = fetched_thread if isinstance(fetched_thread, dict) else None
            thread = thread_cache.get(thread_id)

        repo = str(metadata.get("repo") or (task or {}).get("repo") or metadata.get("working_dir") or "").strip()
        role = str(metadata.get("ui_role") or (task or {}).get("role") or metadata.get("role") or "").strip()
        choices.append(
            SessionChoice(
                session_id=session_id,
                worker_id=str(session.get("worker_id", "")).strip(),
                cli_type=str(session.get("cli_type", "")).strip(),
                account_profile=str(session.get("account_profile", "")).strip(),
                state=str(session.get("state", "")).strip(),
                task_id=task_id,
                task_status=str((task or {}).get("status") or "").strip(),
                thread_id=thread_id,
                thread_name=str((thread or {}).get("name") or "").strip(),
                thread_status=str((thread or {}).get("status") or "").strip(),
                repo=repo,
                repo_name=_basename(repo),
                role=role,
                title=str((task or {}).get("title") or "").strip(),
                updated_at=str(session.get("updated_at", "")).strip(),
                tmux_session=str(metadata.get("tmux_session") or "").strip(),
                attach_kind=str(metadata.get("attach_kind") or "").strip(),
                attach_target=str(metadata.get("attach_target") or "").strip(),
                attach_owner=str(provider_users.get(str(session.get("cli_type", "")).strip(), "")).strip(),
            )
        )

    return sorted(choices, key=lambda item: (item.updated_at, item.session_id), reverse=True)


def filter_session_choices(choices: list[SessionChoice], query: str | None) -> list[SessionChoice]:
    if not query:
        return choices
    needle = query.strip().lower()
    if not needle:
        return choices

    matched: list[SessionChoice] = []
    for choice in choices:
        haystack = [
            choice.session_id,
            choice.repo,
            choice.repo_name,
            choice.role,
            choice.cli_type,
            choice.worker_id,
            choice.account_profile,
            choice.thread_name,
            choice.title,
            choice.tmux_session,
            choice.task_id,
        ]
        if any(needle in field.lower() for field in haystack if field):
            matched.append(choice)
    return matched


def filter_active_session_choices(choices: list[SessionChoice]) -> list[SessionChoice]:
    return [choice for choice in choices if _is_active_choice(choice)]


def render_choices_table(choices: list[SessionChoice]) -> str:
    header = _choice_table_header()
    rows = [header]
    for index, choice in enumerate(choices, start=1):
        rows.append(_choice_table_row(index, choice))
    return "\n".join(rows)


def select_choice(
    choices: list[SessionChoice],
    *,
    query: str = "",
    prompt_fn: Callable[[str], str] = input,
    interactive: bool = True,
) -> SessionChoice:
    filtered = filter_session_choices(choices, query)
    if not filtered:
        raise ValueError("no sessions matched")
    if len(filtered) == 1:
        return filtered[0]
    if interactive:
        try:
            selected = _questionary_select_choice(filtered)
            if selected is None:
                raise ValueError("selection cancelled")
            return selected
        except RuntimeError:
            # Fall back to a plain numeric prompt when questionary isn't available.
            pass
    if not interactive:
        raise ValueError("multiple sessions matched; refine the query")

    print(render_choices_table(filtered), file=sys.stderr)
    print(f"Select session [1-{len(filtered)}]: ", end="", file=sys.stderr, flush=True)
    raw = prompt_fn("").strip()
    if not raw:
        raise ValueError("selection cancelled")
    if not raw.isdigit():
        raise ValueError("invalid selection")
    index = int(raw)
    if index < 1 or index > len(filtered):
        raise ValueError("invalid selection")
    return filtered[index - 1]


def build_attach_spec(choice: SessionChoice, ws_host: str) -> dict[str, str]:
    if choice.attach_kind == "upterm" and choice.attach_target:
        parsed = urlparse(choice.attach_target)
        host = parsed.hostname or ""
        user = parsed.username or ""
        if host and user:
            return {
                "mode": "upterm",
                "ssh_target": f"{user}@{host}",
                "ssh_port": str(parsed.port or 22),
                "remote_cmd": "",
            }

    if choice.attach_kind == "ssh_tmux" and choice.attach_target:
        parsed = urlparse(choice.attach_target)
        host = parsed.hostname or ""
        user = parsed.username or ""
        tmux_session = parse_qs(parsed.query).get("tmux_session", [choice.tmux_session])[0]
        if host and user and tmux_session:
            return {
                "mode": "ssh_tmux",
                "ssh_target": f"{user}@{host}",
                "ssh_port": str(parsed.port or 22),
                "remote_cmd": build_tmux_attach_cmd(choice.attach_owner, tmux_session),
            }

    if not choice.tmux_session:
        return {
            "mode": "unavailable",
            "ssh_target": "",
            "ssh_port": "",
            "remote_cmd": "",
        }

    return {
        "mode": "ws_tmux",
        "ssh_target": ws_host,
        "ssh_port": "22",
        "remote_cmd": build_tmux_attach_cmd(choice.attach_owner, choice.tmux_session),
    }


def build_tmux_attach_cmd(owner: str, tmux_session: str) -> str:
    target = shlex.quote(tmux_session)
    if owner:
        return f"exec sudo -u {shlex.quote(owner)} tmux attach -t {target}"
    return f"exec tmux attach -t {target}"


def _choice_attach_label(choice: SessionChoice) -> str:
    return choice.attach_kind or ("tmux" if choice.tmux_session else "-")


def _is_active_choice(choice: SessionChoice) -> bool:
    if choice.state != "open":
        return False
    if not choice.repo:
        return False
    return choice.task_status in _ACTIVE_TASK_STATUSES


def _choice_table_header() -> str:
    return "  ".join(
        [
            _short("#", 3),
            _short("repo", 18),
            _short("role", 14),
            _short("cli", 8),
            _short("session", 12),
            _short("attach", 9),
            _short("thread", 28),
        ]
    )


def _choice_table_row(index: int, choice: SessionChoice) -> str:
    return "  ".join(
        [
            _short(str(index), 3),
            _short(choice.repo_name or choice.repo or "-", 18),
            _short(choice.role or "-", 14),
            _short(choice.cli_type or "-", 8),
            _short(choice.session_id[:12], 12),
            _short(_choice_attach_label(choice), 9),
            _short(choice.thread_name or choice.title or "-", 28),
        ]
    )


def _choice_label(choice: SessionChoice) -> str:
    summary = choice.thread_name or choice.title or "-"
    return " | ".join(
        [
            choice.repo_name or choice.repo or "-",
            choice.role or "-",
            choice.cli_type or "-",
            choice.session_id[:12],
            _choice_attach_label(choice),
            summary,
        ]
    )


def _questionary_select_choice(choices: list[SessionChoice]) -> SessionChoice | None:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise RuntimeError("interactive selector unavailable")
    try:
        import questionary
    except ImportError as exc:  # pragma: no cover - depends on local environment
        raise RuntimeError("questionary unavailable") from exc

    questionary_choices = [
        questionary.Choice(
            title=_choice_label(choice),
            value=choice,
        )
        for choice in choices
    ]
    return questionary.select(
        "Select live session to attach:",
        choices=questionary_choices,
        use_shortcuts=True,
        use_indicator=True,
    ).ask()


def detect_repo_context(cwd: str | None = None) -> tuple[str, str]:
    root = cwd or os.getcwd()
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        )
        repo_path = proc.stdout.strip()
        if repo_path:
            return repo_path, os.path.basename(repo_path.rstrip("/"))
    except (OSError, subprocess.CalledProcessError):
        pass
    repo_path = os.path.abspath(root)
    return repo_path, os.path.basename(repo_path.rstrip("/"))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="List or resolve live mesh sessions.")
    subparsers = parser.add_subparsers(dest="cmd", required=True)

    list_parser = subparsers.add_parser("list", help="List sessions from the router API.")
    list_parser.add_argument("query", nargs="?", default="", help="Optional repo/session/role filter.")
    list_parser.add_argument(
        "--all",
        action="store_true",
        help="Show sessions across all repos instead of defaulting to the current repo.",
    )
    list_parser.add_argument(
        "--state",
        default="open",
        choices=["open", "closed", "errored", "all"],
        help="Session state filter (default: open).",
    )

    resolve_parser = subparsers.add_parser("resolve", help="Resolve a session selection for attach.")
    resolve_parser.add_argument("query", nargs="?", default="", help="Optional repo/session/role filter.")
    resolve_parser.add_argument(
        "--all",
        action="store_true",
        help="Search sessions across all repos instead of defaulting to the current repo.",
    )
    resolve_parser.add_argument(
        "--state",
        default="open",
        choices=["open", "closed", "errored", "all"],
        help="Session state filter (default: open).",
    )
    resolve_parser.add_argument(
        "--ws-host",
        default=os.environ.get("MESH_WS_HOST", "sam@192.168.1.111"),
        help="WS SSH target for tmux fallback.",
    )
    resolve_parser.add_argument(
        "--output",
        default="",
        help="Optional file path for the resolved JSON payload.",
    )
    return parser.parse_args()


def _print_error(message: str) -> int:
    print(f"Error: {message}", file=sys.stderr)
    return 1


def _emit_payload(payload: dict[str, Any], output_path: str) -> None:
    encoded = json.dumps(payload)
    if output_path:
        Path(output_path).write_text(encoded, encoding="utf-8")
        return
    print(encoded)


def main() -> int:
    args = _parse_args()
    router_url, auth_token = load_router_env()
    if not router_url or not auth_token:
        return _print_error("mesh router env not configured (need MESH_ROUTER_URL and MESH_AUTH_TOKEN)")

    try:
        choices = build_session_choices(router_url, auth_token, state=args.state)
    except HTTPError as exc:
        return _print_error(f"/sessions returned HTTP {exc.code}")
    except URLError as exc:
        return _print_error(f"cannot connect to mesh router at {router_url}: {exc}")
    except (TimeoutError, OSError, json.JSONDecodeError) as exc:
        return _print_error(f"failed to query mesh router: {exc}")

    if args.state == "open":
        choices = filter_active_session_choices(choices)

    _, repo_name = detect_repo_context()
    default_query = "" if getattr(args, "all", False) else repo_name
    query = getattr(args, "query", "").strip() or default_query
    filtered = filter_session_choices(choices, query)
    if args.cmd == "list":
        if not filtered:
            scope = "all repos" if getattr(args, "all", False) else f"repo '{repo_name}'"
            print(f"No sessions matched for {scope}.")
            return 0
        print(render_choices_table(filtered))
        return 0

    try:
        selected = select_choice(choices, query=query, interactive=sys.stdin.isatty())
    except ValueError as exc:
        return _print_error(str(exc))

    payload = {
        "selection": asdict(selected),
        "attach": build_attach_spec(selected, args.ws_host),
    }
    _emit_payload(payload, getattr(args, "output", ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
