from __future__ import annotations

import importlib.util
import json
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

    result = module.handle_remote_request({"op": "discover", "users": ["sam"]})

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

    result = module.handle_remote_request({"op": "discover", "users": ["mesh-worker"]})

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

    result = module.handle_remote_request({"op": "discover", "users": ["sam"]})

    assert result == {"sessions": [], "warnings": []}


def test_reader_captures_exact_pane_with_bounded_history(monkeypatch) -> None:
    module = _load_module()
    seen: list[str] = []

    def fake_run(args: list[str], *, timeout: float = 10.0):
        seen.extend(args)
        return _completed(args, stdout="line one\nline two\nline three\nline four\n")

    monkeypatch.setattr(module, "_current_username", lambda: "sam")
    monkeypatch.setattr(module, "_run_command", fake_run)

    result = module.handle_remote_request(
        {
            "op": "capture",
            "lines": 2,
            "targets": [{"owner": "sam", "name": "claude-rektslug", "pane_id": "%7"}],
        }
    )

    assert result["captures"][0]["output"] == "line three\nline four"
    assert ["capture-pane", "-p", "-S", "-2", "-t", "%7"] == seen[-6:]


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

    monkeypatch.setattr(module, "_ssh_options", lambda host="": [])
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


def test_ssh_options_disable_multiplexing_for_proxy_hosts(monkeypatch, tmp_path: Path) -> None:
    module = _load_module()
    monkeypatch.setenv("MESH_SSH_CONTROL_DIR", str(tmp_path / "control"))
    monkeypatch.delenv("MESH_SSH_CONTROL_MASTER", raising=False)
    monkeypatch.setattr(module, "ssh_host_uses_proxy", lambda host: host == "dell7670")

    proxy_options = module._ssh_options("dell7670")
    direct_options = module._ssh_options("sam@10.0.0.2")

    assert "ControlMaster=no" in proxy_options
    assert "ControlPath=none" in proxy_options
    assert "ControlPersist=30m" not in proxy_options
    assert "ControlMaster=auto" in direct_options
    assert "ControlPersist=30m" in direct_options


def test_send_rejects_empty_multiline_control_and_oversized_text() -> None:
    module = _load_module()

    with pytest.raises(ValueError, match="requires text"):
        module.validate_send_text("", enter=False)
    assert module.validate_send_text("", enter=True) == ""
    with pytest.raises(ValueError, match="control characters"):
        module.validate_send_text("first\nsecond", enter=True)
    with pytest.raises(ValueError, match="exceeds"):
        module.validate_send_text("x" * (module.MAX_SEND_CHARS + 1), enter=False)


def test_remote_send_uses_literal_text_then_explicit_enter(monkeypatch) -> None:
    module = _load_module()
    commands: list[list[str]] = []
    hostile = "$(touch /tmp/not-created); --help"

    def fake_run(args: list[str], *, timeout: float = 10.0):
        commands.append(args)
        return _completed(args)

    monkeypatch.setattr(module, "_current_username", lambda: "sam")
    monkeypatch.setattr(module, "_run_command", fake_run)

    result = module.handle_remote_request(
        {
            "op": "send",
            "target": {"owner": "sam", "name": "claude-rektslug", "pane_id": "%7"},
            "text": hostile,
            "enter": True,
        }
    )

    assert result["text_sent"] is True
    assert result["enter_sent"] is True
    assert commands == [
        ["tmux", "send-keys", "-t", "%7", "-l", "--", hostile],
        ["tmux", "send-keys", "-t", "%7", "Enter"],
    ]


def test_remote_send_does_not_send_enter_after_text_failure(monkeypatch) -> None:
    module = _load_module()
    commands: list[list[str]] = []

    def fake_run(args: list[str], *, timeout: float = 10.0):
        commands.append(args)
        return _completed(args, returncode=1, stderr="pane disappeared")

    monkeypatch.setattr(module, "_current_username", lambda: "sam")
    monkeypatch.setattr(module, "_run_command", fake_run)

    result = module.handle_remote_request(
        {
            "op": "send",
            "target": {"owner": "sam", "name": "claude-rektslug", "pane_id": "%7"},
            "text": "status?",
            "enter": True,
        }
    )

    assert result == {"error": "pane disappeared"}
    assert len(commands) == 1


def test_live_client_send_uses_discovered_owner_and_pane() -> None:
    module = _load_module()
    observed: dict = {}

    def fake_request(endpoint, payload):
        observed.update(payload)
        return {
            "owner": "sam",
            "name": "claude-rektslug",
            "pane_id": "%7",
            "text_sent": True,
            "enter_sent": False,
        }

    endpoint = module.LiveEndpoint(host="dell-vpn", local=False, users=("sam",))
    client = module.LiveClient(endpoint, request_fn=fake_request)
    session = module.LiveSession(owner="sam", name="claude-rektslug", pane_id="%7")

    client.send(session, "status?", enter=False)

    assert observed == {
        "op": "send",
        "target": {"owner": "sam", "name": "claude-rektslug", "pane_id": "%7"},
        "text": "status?",
        "enter": False,
    }


def test_attach_auto_uses_mosh_only_for_reachable_direct_host(monkeypatch) -> None:
    module = _load_module()
    endpoint = module.LiveEndpoint(host="sam@10.0.0.2", local=False, users=("sam",))
    session = module.LiveSession(owner="sam", name="claude-rektslug")
    monkeypatch.setattr(module.shutil, "which", lambda name: "/opt/local/bin/mosh")
    monkeypatch.setattr(module, "ssh_host_uses_proxy", lambda host: False)
    monkeypatch.setattr(module, "_direct_host_reachable", lambda host: True)
    monkeypatch.setattr(module, "_remote_login_user", lambda host: "sam")

    plan = module.build_attach_plan(endpoint, session)

    assert plan.transport == "mosh"
    assert plan.host == "sam@10.0.0.2"
    assert plan.argv[0] == "/opt/local/bin/mosh"
    assert plan.argv[-3:] == ("bash", "-lc", "exec tmux attach -t claude-rektslug")


def test_attach_on_workstation_uses_local_tmux(monkeypatch) -> None:
    module = _load_module()
    endpoint = module.LiveEndpoint(host="sam@10.0.0.2", local=True, users=("mesh-worker",))
    session = module.LiveSession(owner="mesh-worker", name="codex-progressive")
    monkeypatch.setattr(module, "_current_username", lambda: "sam")

    plan = module.build_attach_plan(endpoint, session)

    assert plan.transport == "local"
    assert plan.argv == (
        "sudo",
        "-n",
        "-u",
        "mesh-worker",
        "tmux",
        "attach",
        "-t",
        "codex-progressive",
    )


def test_attach_auto_uses_ssh_for_cloudflare_or_jump_alias(monkeypatch) -> None:
    module = _load_module()
    endpoint = module.LiveEndpoint(host="dell7670", local=False, users=("sam",))
    session = module.LiveSession(owner="sam", name="claude-rektslug")
    monkeypatch.setattr(module.shutil, "which", lambda name: "/opt/local/bin/mosh")
    monkeypatch.setattr(module, "ssh_host_uses_proxy", lambda host: True)
    monkeypatch.setattr(module, "_remote_login_user", lambda host: "sam")
    monkeypatch.setattr(module, "_ssh_options", lambda host="": ["-o", "ConnectTimeout=10"])

    plan = module.build_attach_plan(endpoint, session)

    assert plan.transport == "ssh"
    assert plan.argv == (
        "ssh",
        "-o",
        "ConnectTimeout=10",
        "-t",
        "dell7670",
        "exec tmux attach -t claude-rektslug",
    )


def test_attach_cross_user_uses_noninteractive_sudo_and_shell_quoting(monkeypatch) -> None:
    module = _load_module()
    endpoint = module.LiveEndpoint(host="dell7670", local=False, users=("mesh-worker",))
    session = module.LiveSession(owner="mesh-worker", name="codex odd'name")
    monkeypatch.setattr(module, "_remote_login_user", lambda host: "sam")
    monkeypatch.setattr(module, "_ssh_options", lambda host="": [])

    plan = module.build_attach_plan(endpoint, session, transport="ssh")

    assert plan.argv[:4] == ("ssh", "-t", "dell7670", plan.argv[-1])
    assert plan.argv[-1].startswith("exec sudo -n -u mesh-worker tmux attach -t ")
    assert "'\"'\"'" in plan.argv[-1]


def test_forced_mosh_rejects_proxy_host(monkeypatch) -> None:
    module = _load_module()
    endpoint = module.LiveEndpoint(host="dell7670", local=False, users=("sam",))
    session = module.LiveSession(owner="sam", name="claude-rektslug")
    monkeypatch.setattr(module.shutil, "which", lambda name: "/opt/local/bin/mosh")
    monkeypatch.setattr(module, "ssh_host_uses_proxy", lambda host: True)

    with pytest.raises(module.LiveReadError, match="direct VPN/LAN"):
        module.build_attach_plan(endpoint, session, transport="mosh")


def test_resolve_coordinator_auto_detects_unique_session_and_rejects_ambiguity() -> None:
    module = _load_module()
    sessions = [
        module.LiveSession(owner="sam", name="claude-coordinator"),
        module.LiveSession(owner="sam", name="codex-progressive"),
    ]

    assert module.resolve_coordinator(sessions).name == "claude-coordinator"

    sessions.append(module.LiveSession(owner="mesh-worker", name="codex-coordinator"))
    with pytest.raises(module.SessionResolutionError, match="multiple coordinator sessions"):
        module.resolve_coordinator(sessions)


def test_redact_capture_removes_credentials_private_keys_and_terminal_sequences() -> None:
    module = _load_module()
    raw = "\n".join(
        [
            "\x1b[31mstatus\x1b[0m",
            "Authorization: Bearer very-secret-token",
            "api_key=sk-abcdefghijklmnopqrstuvwxyz",
            "github=ghp_abcdefghijklmnopqrstuvwxyz",
            "-----BEGIN OPENSSH PRIVATE KEY-----",
            "private-body",
            "-----END OPENSSH PRIVATE KEY-----",
        ]
    )

    redacted = module.redact_capture(raw)

    assert "status" in redacted
    assert "very-secret-token" not in redacted
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in redacted
    assert "ghp_abcdefghijklmnopqrstuvwxyz" not in redacted
    assert "private-body" not in redacted
    assert "[REDACTED PRIVATE KEY]" in redacted
    assert "\x1b" not in redacted


def test_redacted_session_dict_does_not_expose_raw_capture_or_error() -> None:
    module = _load_module()
    session = module.LiveSession(
        owner="sam",
        name="claude-coordinator",
        output="api_key=secret-output",
        capture_error="Authorization: Bearer secret-error",
    )

    encoded = json.dumps(module.redacted_session_dict(session))

    assert "secret-output" not in encoded
    assert "secret-error" not in encoded
    assert "[REDACTED]" in encoded


def test_build_coordinator_brief_requires_debate_decision_and_delegation() -> None:
    module = _load_module()
    coordinator = module.LiveSession(
        owner="sam",
        name="claude-coordinator",
        pane_path="/data/sata/1TB",
        pane_command="claude",
        output="ready",
    )
    worker = module.LiveSession(
        owner="sam",
        name="codex-progressive",
        pane_path="/data/sata/1TB/progressive-deploy",
        pane_command="codex",
        output="auth_token=secret-value\nwaiting for review",
    )

    brief = module.build_coordinator_brief(
        [worker, coordinator],
        scope="all live repos",
        coordinator=coordinator,
    )

    assert "sam/claude-coordinator [COORDINATOR]" in brief
    assert "sam/codex-progressive" in brief
    assert "secret-value" not in brief
    assert "Decision debate" in brief
    assert "Recommended decision" in brief
    assert "Delegation plan" in brief
    assert "wait for explicit human confirmation" in brief
    assert "mesh live send only for an existing tmux session" in brief
    assert "mesh thread create/add-step" in brief
    assert "Never imply that a router task targets an existing manual tmux session" in brief


def test_main_brief_is_read_only_and_supports_repo_scope(monkeypatch, capsys) -> None:
    module = _load_module()
    calls: list[str] = []

    class FakeClient:
        def __init__(self, endpoint) -> None:
            self.endpoint = endpoint

        def discover(self):
            calls.append("discover")
            return (
                [
                    module.LiveSession(
                        owner="sam",
                        name="claude-coordinator",
                        pane_path="/data/sata/1TB",
                        pane_command="claude",
                    ),
                    module.LiveSession(
                        owner="sam",
                        name="claude-rektslug",
                        pane_path="/data/sata/1TB/rektslug",
                        pane_command="claude",
                        repo_name="rektslug",
                    ),
                    module.LiveSession(
                        owner="sam",
                        name="codex-progressive",
                        pane_path="/data/sata/1TB/progressive-deploy",
                        pane_command="codex",
                    ),
                ],
                [],
            )

        def capture(self, sessions, lines):
            calls.append(f"capture:{lines}")
            return [module.replace(item, output=f"tail for {item.name}") for item in sessions], []

    monkeypatch.setattr(module, "LiveClient", FakeClient)

    code = module.main(
        [
            "--local",
            "--users",
            "sam",
            "brief",
            "--repo",
            "rektslug",
            "--lines",
            "12",
        ]
    )

    output = capsys.readouterr().out
    assert code == 0
    assert calls == ["discover", "capture:12"]
    assert "sam/claude-rektslug" in output
    assert "sam/claude-coordinator [COORDINATOR]" in output
    assert "codex-progressive" not in output


def test_live_module_contains_no_tmux_lifecycle_mutation_primitives() -> None:
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "mesh_live_cli.py"
    source = script_path.read_text(encoding="utf-8")

    forbidden = ("kill-session", "new-session")
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
    assert "mesh live send" in proc.stdout
    assert "mesh live attach" in proc.stdout
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
