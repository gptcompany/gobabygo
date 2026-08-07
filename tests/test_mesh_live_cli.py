from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "mesh_live_cli.py"
    spec = importlib.util.spec_from_file_location("mesh_live_cli", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _completed(args: list[str], returncode: int = 0, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr=stderr)


def test_reader_discovers_current_users_tmux_session(monkeypatch) -> None:
    module = _load_module()
    sep = module._FIELD_SEPARATOR
    commands: list[list[str]] = []

    def fake_run(args: list[str], *, timeout: float = 10.0):
        commands.append(args)
        if "list-sessions" in args:
            return _completed(
                args,
                stdout=sep.join(["claude-rektslug", "100", "200", "1", "0"]) + "\n",
            )
        if "display-message" in args:
            return _completed(
                args,
                stdout=sep.join(["%7", "/data/sata/1TB/rektslug", "claude", "0"]) + "\n",
            )
        if args[-1] == "MESH_UI_ROLE":
            return _completed(args, stdout="MESH_UI_ROLE=lead\n")
        if args[-1] == "MESH_UI_REPO_NAME":
            return _completed(args, stdout="MESH_UI_REPO_NAME=rektslug\n")
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(module, "_current_username", lambda: "sam")
    monkeypatch.setattr(module, "_run_command", fake_run)

    result = module.handle_reader_request({"op": "discover", "users": ["sam"]})

    assert result["warnings"] == []
    assert result["sessions"] == [
        {
            "owner": "sam",
            "name": "claude-rektslug",
            "created_at": 100,
            "activity_at": 200,
            "windows": 1,
            "attached": 0,
            "pane_id": "%7",
            "pane_path": "/data/sata/1TB/rektslug",
            "pane_command": "claude",
            "pane_dead": False,
            "role": "lead",
            "repo_name": "rektslug",
        }
    ]
    assert all("sudo" not in command for command in commands)


def test_reader_reports_partial_discovery_when_sudo_is_denied(monkeypatch) -> None:
    module = _load_module()

    def fake_run(args: list[str], *, timeout: float = 10.0):
        if args[:3] == ["id", "-u", "mesh-worker"]:
            return _completed(args, stdout="1001\n")
        if args[:4] == ["sudo", "-n", "-u", "mesh-worker"]:
            return _completed(args, returncode=1, stderr="sudo: a password is required\n")
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(module, "_current_username", lambda: "sam")
    monkeypatch.setattr(module, "_run_command", fake_run)

    result = module.handle_reader_request({"op": "discover", "users": ["mesh-worker"]})

    assert result["sessions"] == []
    assert result["warnings"] == [
        "mesh-worker: unable to list tmux sessions: sudo: a password is required"
    ]


@pytest.mark.parametrize(
    "message",
    [
        "no server running on /tmp/tmux-1000/default\n",
        "error connecting to /private/tmp/tmux-501/default (No such file or directory)\n",
    ],
)
def test_reader_treats_missing_tmux_server_as_empty(monkeypatch, message: str) -> None:
    module = _load_module()
    monkeypatch.setattr(module, "_current_username", lambda: "sam")
    monkeypatch.setattr(
        module,
        "_run_command",
        lambda args, timeout=10.0: _completed(args, returncode=1, stderr=message),
    )

    result = module.handle_reader_request({"op": "discover", "users": ["sam"]})

    assert result == {"sessions": [], "warnings": []}


def test_reader_captures_exact_pane_with_bounded_history(monkeypatch) -> None:
    module = _load_module()
    seen: list[str] = []

    def fake_run(args: list[str], *, timeout: float = 10.0):
        seen.extend(args)
        return _completed(args, stdout="line one\nline two\n")

    monkeypatch.setattr(module, "_current_username", lambda: "sam")
    monkeypatch.setattr(module, "_run_command", fake_run)

    result = module.handle_reader_request(
        {
            "op": "capture",
            "lines": 80,
            "targets": [{"owner": "sam", "name": "claude-rektslug", "pane_id": "%7"}],
        }
    )

    assert result["captures"][0]["output"] == "line one\nline two"
    assert ["capture-pane", "-p", "-S", "-80", "-t", "%7"] == seen[-6:]


def test_capture_line_bounds_are_enforced() -> None:
    module = _load_module()

    assert module.validate_capture_lines(0, allow_zero=True) == 0
    assert module.validate_capture_lines(2000, allow_zero=False) == 2000
    with pytest.raises(ValueError, match="between 1 and 2000"):
        module.validate_capture_lines(0, allow_zero=False)
    with pytest.raises(ValueError, match="between 0 and 2000"):
        module.validate_capture_lines(2001, allow_zero=True)


def test_resolve_session_prefers_exact_then_unique_prefix() -> None:
    module = _load_module()
    sessions = [
        module.LiveSession(owner="sam", name="claude-rektslug"),
        module.LiveSession(owner="sam", name="claude-coordinator"),
    ]

    assert module.resolve_session(sessions, "claude-rektslug").name == "claude-rektslug"
    assert module.resolve_session(sessions, "claude-r").name == "claude-rektslug"
    with pytest.raises(module.SessionResolutionError, match="ambiguous"):
        module.resolve_session(sessions, "claude-")


def test_resolve_session_requires_owner_for_cross_user_collision() -> None:
    module = _load_module()
    sessions = [
        module.LiveSession(owner="sam", name="codex-progressive"),
        module.LiveSession(owner="mesh-worker", name="codex-progressive"),
    ]

    with pytest.raises(module.SessionResolutionError, match="multiple owners"):
        module.resolve_session(sessions, "codex-progressive")
    selected = module.resolve_session(sessions, "codex-progressive", owner="mesh-worker")
    assert selected.owner == "mesh-worker"


def test_filter_sessions_matches_repo_role_owner_and_command() -> None:
    module = _load_module()
    sessions = [
        module.LiveSession(
            owner="sam",
            name="claude-rektslug",
            pane_path="/data/sata/1TB/rektslug",
            pane_command="claude",
            role="lead",
            repo_name="rektslug",
        ),
        module.LiveSession(
            owner="mesh-worker",
            name="codex-progressive",
            pane_path="/data/sata/1TB/other",
            pane_command="codex",
        ),
    ]

    assert [item.name for item in module.filter_sessions(sessions, "rektslug")] == [
        "claude-rektslug"
    ]
    assert [item.name for item in module.filter_sessions(sessions, "mesh-worker")] == [
        "codex-progressive"
    ]
    assert [item.name for item in module.filter_sessions(sessions, "lead")] == [
        "claude-rektslug"
    ]


def test_live_client_enriches_discovery_with_capture_results() -> None:
    module = _load_module()
    requests: list[dict] = []

    def fake_request(endpoint, payload):
        requests.append(payload)
        if payload["op"] == "discover":
            return {
                "sessions": [
                    {
                        "owner": "sam",
                        "name": "claude-rektslug",
                        "pane_id": "%7",
                        "pane_path": "/data/sata/1TB/rektslug",
                    }
                ],
                "warnings": [],
            }
        return {
            "captures": [
                {
                    "owner": "sam",
                    "name": "claude-rektslug",
                    "output": "ready",
                    "error": "",
                }
            ],
            "warnings": [],
        }

    endpoint = module.LiveEndpoint(host="dell-vpn", local=False, users=("sam",))
    client = module.LiveClient(endpoint, request_fn=fake_request)

    sessions, _ = client.discover()
    captured, _ = client.capture(sessions, 40)

    assert captured[0].output == "ready"
    assert requests[1]["targets"] == [
        {"owner": "sam", "name": "claude-rektslug", "pane_id": "%7"}
    ]


def test_remote_request_keeps_payload_out_of_ssh_arguments(monkeypatch) -> None:
    module = _load_module()
    observed: dict = {}
    hostile = "$(touch /tmp/mesh-live-owned); 'quoted'"

    def fake_run(args, **kwargs):
        observed["args"] = args
        observed["input"] = kwargs["input"]
        return _completed(args, stdout='{"sessions":[],"warnings":[]}\n')

    monkeypatch.setattr(module, "_ssh_options", lambda: [])
    monkeypatch.setattr(module.subprocess, "run", fake_run)
    endpoint = module.LiveEndpoint(host="dell-vpn", local=False, users=("sam",))

    response = module.request_endpoint(
        endpoint,
        {"op": "discover", "users": ["sam"], "query": hostile},
    )

    assert response == {"sessions": [], "warnings": []}
    assert observed["args"] == ["ssh", "dell-vpn", "python3", "-"]
    assert hostile not in " ".join(observed["args"])
    assert hostile in observed["input"]


def test_default_users_are_valid_and_deduplicated(monkeypatch) -> None:
    module = _load_module()
    monkeypatch.delenv("MESH_LIVE_USERS", raising=False)
    monkeypatch.setattr(module, "_current_username", lambda: "sam")

    assert module._default_users("sam@10.0.0.2") == ("sam", "mesh-worker", "mesh")


def test_read_only_module_contains_no_tmux_mutation_primitives() -> None:
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "mesh_live_cli.py"
    source = script_path.read_text(encoding="utf-8")

    forbidden = ("send-keys", "kill-session", "new-session", "attach-session")
    assert all(token not in source for token in forbidden)


def test_mesh_dispatcher_exposes_live_help() -> None:
    repo_root = Path(__file__).resolve().parents[1]

    proc = subprocess.run(
        [str(repo_root / "scripts" / "mesh"), "live", "--help"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0
    assert "mesh live board" in proc.stdout
    assert "mesh live peek" in proc.stdout
    assert "does not require the router or iTerm2" in proc.stdout


def test_mesh_dispatcher_runs_live_board_without_router_env() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env.pop("MESH_ROUTER_URL", None)
    env.pop("MESH_AUTH_TOKEN", None)
    env["MESH_LIVE_LOCAL"] = "1"
    env["MESH_LIVE_USERS"] = "definitely-not-a-local-user"

    proc = subprocess.run(
        [str(repo_root / "scripts" / "mesh"), "live", "board", "--lines", "0"],
        cwd=repo_root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0
    assert proc.stdout.strip() == "No live tmux sessions matched."
    assert "router" not in proc.stderr.lower()


def test_mesh_live_dispatch_branch_has_no_router_or_iterm_initialization() -> None:
    mesh_script = Path(__file__).resolve().parents[1] / "scripts" / "mesh"
    source = mesh_script.read_text(encoding="utf-8")
    branch = source.split('  live)\n', 1)[1].split('  sessions)\n', 1)[0]

    assert "mesh_live_cli.py" in branch
    assert "ensure_router_env" not in branch
    assert "ensure_mac_router_tunnel" not in branch
    assert "run_iterm_control" not in branch
