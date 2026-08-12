from __future__ import annotations

import os
from pathlib import Path
import shlex
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
HELPERS = ROOT / "scripts" / "mesh_live_shell_helpers.sh"
INSTALLER = ROOT / "scripts" / "install-shell-helpers.sh"


def _shells() -> list[str]:
    return [shell for shell in (shutil.which("bash"), shutil.which("zsh")) if shell]


def _run_shell(shell: str, body: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [shell, "-c", body],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def _fake_capture_command(directory: Path, name: str) -> None:
    command = directory / name
    command.write_text(
        "#!/bin/sh\nprintf '<%s>\\n' \"$@\" > \"$CAPTURE_FILE\"\n",
        encoding="utf-8",
    )
    command.chmod(0o755)


@pytest.mark.parametrize("shell", _shells())
def test_board_peek_and_send_are_thin_mesh_live_wrappers(shell: str) -> None:
    helper = shlex.quote(str(HELPERS))
    proc = _run_shell(
        shell,
        f"""
mesh() {{ printf '<%s>\\n' "$@"; }}
source {helper}
_ws_control_host() {{ printf '%s' 'dell7670'; }}
wboard 40
wboard rektslug 25
wpeek claude-rektslug 80
wsend claude-rektslug "status now" --enter
wbrief --repo rektslug
""",
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines() == [
        "<live>",
        "<board>",
        "<--lines>",
        "<40>",
        "<live>",
        "<board>",
        "<rektslug>",
        "<--lines>",
        "<25>",
        "<live>",
        "<peek>",
        "<claude-rektslug>",
        "<80>",
        "<live>",
        "<send>",
        "<claude-rektslug>",
        "<status now>",
        "<--enter>",
        "<live>",
        "<brief>",
        "<--repo>",
        "<rektslug>",
    ]


@pytest.mark.parametrize("shell", _shells())
def test_wsattach_passes_only_a_reachable_direct_host_to_mesh(shell: str) -> None:
    helper = shlex.quote(str(HELPERS))
    proc = _run_shell(
        shell,
        f"""
mesh() {{ printf 'host=%s mosh=%s\\n' "${{MESH_WS_HOST:-}}" "${{MESH_MOSH_HOST:-}}"; printf '<%s>\\n' "$@"; }}
mosh() {{ return 0; }}
source {helper}
_ws_mosh_host() {{ printf '%s' 'sam@10.0.0.2'; }}
wsattach claude-rektslug --owner sam
""",
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines() == [
        "host=sam@10.0.0.2 mosh=sam@10.0.0.2",
        "<live>",
        "<attach>",
        "<claude-rektslug>",
        "<--owner>",
        "<sam>",
    ]


@pytest.mark.parametrize("shell", _shells())
def test_wsattach_leaves_transport_auto_when_no_direct_host_exists(shell: str) -> None:
    helper = shlex.quote(str(HELPERS))
    proc = _run_shell(
        shell,
        f"""
mesh() {{ printf 'host=%s mosh=%s\\n' "${{MESH_WS_HOST:-}}" "${{MESH_MOSH_HOST:-}}"; printf '<%s>\\n' "$@"; }}
source {helper}
_ws_mosh_host() {{ return 1; }}
_ws_control_host() {{ printf '%s' 'dell7670'; }}
wsattach claude-rektslug
""",
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines() == [
        "host=dell7670 mosh=",
        "<live>",
        "<attach>",
        "<claude-rektslug>",
    ]


@pytest.mark.parametrize("shell", _shells())
def test_control_host_prefers_override_then_direct_configured_and_cloudflare(shell: str) -> None:
    helper = shlex.quote(str(HELPERS))
    proc = _run_shell(
        shell,
        f"""
source {helper}
unset MESH_WS_CONTROL_HOST MESH_WS_HOST
_ws_mosh_host() {{ printf '%s' 'sam@10.0.0.2'; }}
_ws_cloudflare_host() {{ printf '%s' 'dell7670'; }}
_ws_control_host; printf '\\n'
_ws_mosh_host() {{ return 1; }}
MESH_WS_HOST=dell-vpn _ws_control_host; printf '\\n'
unset MESH_WS_HOST
_ws_control_host; printf '\\n'
MESH_WS_CONTROL_HOST=forced-host _ws_control_host; printf '\\n'
""",
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines() == [
        "sam@10.0.0.2",
        "dell-vpn",
        "dell7670",
        "forced-host",
    ]


@pytest.mark.parametrize("shell", _shells())
def test_reachability_probe_handles_user_and_bracketed_hosts(shell: str) -> None:
    helper = shlex.quote(str(HELPERS))
    proc = _run_shell(
        shell,
        f"""
exec 3>&1
nc() {{ printf '<%s>\\n' "$@" >&3; return 0; }}
source {helper}
_ws_host_reachable sam@10.0.0.2
_ws_host_reachable 'sam@[fd00::2]'
""",
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines() == [
        "<-z>",
        "<-w>",
        "<1>",
        "<10.0.0.2>",
        "<22>",
        "<-z>",
        "<-w>",
        "<1>",
        "<fd00::2>",
        "<22>",
    ]


def test_installer_is_idempotent_and_sources_canonical_live_helpers(tmp_path: Path) -> None:
    zshrc = tmp_path / ".zshrc"
    bashrc = tmp_path / ".bashrc"
    env = os.environ.copy()
    env["TARGET_ZSHRC"] = str(zshrc)
    env["TARGET_BASHRC"] = str(bashrc)

    for _ in range(2):
        proc = subprocess.run(
            [str(INSTALLER)],
            cwd=ROOT,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr

    for rc_file in (zshrc, bashrc):
        content = rc_file.read_text(encoding="utf-8")
        assert content.count("# >>> gobabygo-shell-helpers >>>") == 1
        assert content.count("# <<< gobabygo-shell-helpers <<<") == 1
        assert 'source "$mesh_live_helpers"' in content
        assert "sudo -u mesh-worker tmux attach" not in content


@pytest.mark.parametrize("shell", _shells())
def test_installed_block_loads_live_and_persistent_helpers(shell: str, tmp_path: Path) -> None:
    rc_file = tmp_path / ".testrc"
    env = os.environ.copy()
    env["TARGET_ZSHRC"] = str(rc_file)
    env["TARGET_BASHRC"] = str(tmp_path / ".unused-bashrc")
    install = subprocess.run(
        [str(INSTALLER)],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert install.returncode == 0, install.stderr

    proc = _run_shell(
        shell,
        f"MESH_HOME={shlex.quote(str(ROOT))}; source {shlex.quote(str(rc_file))}; "
        "type wboard >/dev/null && type wsend >/dev/null && type wbrief >/dev/null && "
        "type wsattach >/dev/null && "
        "type mclaude >/dev/null && type mcodex >/dev/null && type mtmux >/dev/null && "
        "type mcoordinator >/dev/null",
    )

    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize("shell", _shells())
def test_mcoordinator_bootstraps_repo_coordinator(shell: str, tmp_path: Path) -> None:
    helper = shlex.quote(str(HELPERS))
    prompt_args_file = tmp_path / "prompt-args"
    proc = _run_shell(
        shell,
        f"""
source {helper}
PROMPT_ARGS_FILE={shlex.quote(str(prompt_args_file))}
_mesh_live_run() {{ printf '<%s>\n' "$@" > "$PROMPT_ARGS_FILE"; printf '%s' 'AUTONOMOUS PROMPT'; }}
_ws_mosh_attach_or_start() {{ printf 'session=%s\ndir=%s\nstartup=%s\nkind=%s\n' "$1" "$2" "$3" "$5"; }}
MESH_WS_REPO_BASE=/data/sata/1TB
MESH_COORDINATOR_MESH_SCRIPT=/data/sata/1TB/gobabygo/scripts/mesh
MESH_COORDINATOR_CLAUDE_CMD=claude
mcoordinator rektslug --worker codex-rektslug-worker
""",
    )

    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.splitlines()
    assert lines[:2] == [
        "session=claude-rektslug-coordinator",
        "dir=/data/sata/1TB/rektslug",
    ]
    assert lines[2].startswith(
        "startup=claude --name claude-rektslug-coordinator --append-system-prompt "
    )
    assert "AUTONOMOUS" in lines[2]
    assert lines[3] == "kind=coordinator"
    assert prompt_args_file.read_text(encoding="utf-8").splitlines() == [
        "<live>",
        "<coordinator-prompt>",
        "<--repo>",
        "<rektslug>",
        "<--session>",
        "<claude-rektslug-coordinator>",
        "<--mesh-script>",
        "</data/sata/1TB/gobabygo/scripts/mesh>",
        "<--worker>",
        "<codex-rektslug-worker>",
    ]


@pytest.mark.parametrize("shell", _shells())
def test_mcoordinator_bootstraps_multi_repo_coordinator(shell: str, tmp_path: Path) -> None:
    helper = shlex.quote(str(HELPERS))
    prompt_args_file = tmp_path / "prompt-args"
    proc = _run_shell(
        shell,
        f"""
source {helper}
PROMPT_ARGS_FILE={shlex.quote(str(prompt_args_file))}
_mesh_live_run() {{ printf '<%s>\n' "$@" > "$PROMPT_ARGS_FILE"; printf '%s' 'MULTI PROMPT'; }}
_ws_mosh_attach_or_start() {{ printf 'session=%s\ndir=%s\nstartup=%s\nkind=%s\n' "$1" "$2" "$3" "$5"; }}
MESH_WS_REPO_BASE=/data/sata/1TB
MESH_COORDINATOR_MESH_SCRIPT=/data/sata/1TB/gobabygo/scripts/mesh
MESH_COORDINATOR_CLAUDE_CMD=claude
mcoordinator --all --session claude-live-coordinator
""",
    )

    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.splitlines()
    assert lines[:2] == [
        "session=claude-live-coordinator",
        "dir=/data/sata/1TB",
    ]
    assert lines[2].startswith(
        "startup=claude --name claude-live-coordinator --append-system-prompt "
    )
    assert "MULTI" in lines[2]
    assert lines[3] == "kind=coordinator"
    assert prompt_args_file.read_text(encoding="utf-8").splitlines() == [
        "<live>",
        "<coordinator-prompt>",
        "<--all>",
        "<--session>",
        "<claude-live-coordinator>",
        "<--mesh-script>",
        "</data/sata/1TB/gobabygo/scripts/mesh>",
    ]


@pytest.mark.parametrize("shell", _shells())
def test_mcoordinator_resumes_with_fresh_gobabygo_contract(shell: str) -> None:
    helper = shlex.quote(str(HELPERS))
    resume_id = "b1a2f0f3-75cf-4693-9dc1-e5a5814a4c1c"
    proc = _run_shell(
        shell,
        f"""
source {helper}
_mesh_live_run() {{ printf '%s' 'FRESH GOBABYGO CONTRACT'; }}
_ws_mosh_attach_or_start() {{ printf 'session=%s\ndir=%s\nstartup=%s\nresume=%s\nkind=%s\n' "$1" "$2" "$3" "$4" "$5"; }}
MESH_WS_REPO_BASE=/data/sata/1TB
MESH_COORDINATOR_CLAUDE_CMD=claude
mcoordinator --all --resume {resume_id}
""",
    )

    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.splitlines()
    assert lines[:2] == ["session=claude-coordinator", "dir=/data/sata/1TB"]
    assert lines[2].startswith(
        f"startup=claude --resume {resume_id} --name claude-coordinator "
        "--append-system-prompt "
    )
    assert "FRESH" in lines[2]
    assert lines[3] == f"resume={resume_id}"
    assert lines[4] == "kind=coordinator"


@pytest.mark.parametrize("shell", _shells())
def test_mcoordinator_continues_latest_conversation_in_repo_scope(shell: str) -> None:
    helper = shlex.quote(str(HELPERS))
    proc = _run_shell(
        shell,
        f"""
source {helper}
_mesh_live_run() {{ printf '%s' 'CURRENT CONTRACT'; }}
_ws_mosh_attach_or_start() {{ printf 'session=%s\ndir=%s\nstartup=%s\nkind=%s\n' "$1" "$2" "$3" "$5"; }}
MESH_WS_REPO_BASE=/data/sata/1TB
MESH_COORDINATOR_CLAUDE_CMD=claude
mcoordinator rektslug --continue --worker codex-rektslug
""",
    )

    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.splitlines()
    assert lines[:2] == [
        "session=claude-rektslug-coordinator",
        "dir=/data/sata/1TB/rektslug",
    ]
    assert lines[2].startswith(
        "startup=claude --continue --name claude-rektslug-coordinator "
        "--append-system-prompt "
    )
    assert "CURRENT" in lines[2]
    assert lines[3] == "kind=coordinator"


@pytest.mark.parametrize("shell", _shells())
def test_mosh_fallback_forwards_coordinator_startup_to_ssh(shell: str) -> None:
    helper = shlex.quote(str(HELPERS))
    proc = _run_shell(
        shell,
        f"""
source {helper}
_ws_mosh_host() {{ return 1; }}
_ws_ssh_attach_or_start() {{ printf '<%s>\n' "$@"; }}
_ws_mosh_attach_or_start claude-coordinator /data/sata/1TB \
  'claude --name claude-coordinator' '' coordinator
""",
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines() == [
        "<claude-coordinator>",
        "</data/sata/1TB>",
        "<claude --name claude-coordinator>",
        "<>",
        "<coordinator>",
    ]


@pytest.mark.parametrize("shell", _shells())
def test_mosh_transport_failure_falls_back_to_ssh(shell: str, tmp_path: Path) -> None:
    helper = shlex.quote(str(HELPERS))
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    mosh = fake_bin / "mosh"
    mosh.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    mosh.chmod(0o755)
    proc = _run_shell(
        shell,
        f"""
source {helper}
export PATH={shlex.quote(str(fake_bin))}:$PATH
_ws_mosh_host() {{ printf '%s' 'sam@172.23.0.42'; }}
_ws_mosh_preflight_attach_or_start() {{ return 0; }}
_ws_ssh_attach_or_start() {{ printf '<%s>\n' "$@"; }}
_ws_mosh_attach_or_start claude-coordinator /data/sata/1TB \
  'claude --name claude-coordinator' resume-id coordinator
""",
    )

    assert proc.returncode == 0, proc.stderr
    assert "mosh attach failed (exit 1); falling back to SSH" in proc.stderr
    assert proc.stdout.splitlines() == [
        "<claude-coordinator>",
        "</data/sata/1TB>",
        "<claude --name claude-coordinator>",
        "<resume-id>",
        "<coordinator>",
    ]


@pytest.mark.parametrize("shell", _shells())
@pytest.mark.parametrize("exit_code", [130, 143])
def test_mosh_operator_stop_does_not_fall_back(
    shell: str, tmp_path: Path, exit_code: int
) -> None:
    helper = shlex.quote(str(HELPERS))
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    mosh = fake_bin / "mosh"
    mosh.write_text(f"#!/bin/sh\nexit {exit_code}\n", encoding="utf-8")
    mosh.chmod(0o755)
    proc = _run_shell(
        shell,
        f"""
source {helper}
export PATH={shlex.quote(str(fake_bin))}:$PATH
_ws_mosh_host() {{ printf '%s' 'sam@172.23.0.42'; }}
_ws_mosh_preflight_attach_or_start() {{ return 0; }}
_ws_ssh_attach_or_start() {{ echo unexpected-ssh; }}
_ws_mosh_attach_or_start claude-coordinator /data/sata/1TB \
  'claude --name claude-coordinator' '' coordinator
""",
    )

    assert proc.returncode == exit_code
    assert "unexpected-ssh" not in proc.stdout
    assert "falling back" not in proc.stderr


@pytest.mark.parametrize("shell", _shells())
@pytest.mark.parametrize("exit_code", [3, 4, 5])
def test_mosh_preflight_validation_stop_does_not_launch_or_fallback(
    shell: str, exit_code: int
) -> None:
    helper = shlex.quote(str(HELPERS))
    proc = _run_shell(
        shell,
        f"""
source {helper}
mosh() {{ echo unexpected-mosh; }}
_ws_mosh_host() {{ printf '%s' 'sam@172.23.0.42'; }}
_ws_mosh_preflight_attach_or_start() {{ return {exit_code}; }}
_ws_ssh_attach_or_start() {{ echo unexpected-ssh; }}
_ws_mosh_attach_or_start claude-coordinator /data/sata/1TB \
  'claude --name claude-coordinator' '' coordinator
""",
    )

    assert proc.returncode == exit_code
    assert "unexpected-mosh" not in proc.stdout
    assert "unexpected-ssh" not in proc.stdout
    assert "falling back" not in proc.stderr


@pytest.mark.parametrize("shell", _shells())
def test_mosh_preflight_transport_failure_falls_back_to_ssh(shell: str) -> None:
    helper = shlex.quote(str(HELPERS))
    proc = _run_shell(
        shell,
        f"""
source {helper}
mosh() {{ echo unexpected-mosh; }}
_ws_mosh_host() {{ printf '%s' 'sam@172.23.0.42'; }}
_ws_mosh_preflight_attach_or_start() {{ return 255; }}
_ws_ssh_attach_or_start() {{ printf '<%s>\n' "$@"; }}
_ws_mosh_attach_or_start claude-coordinator /data/sata/1TB \
  'claude --name claude-coordinator' resume-id coordinator
""",
    )

    assert proc.returncode == 0, proc.stderr
    assert "unexpected-mosh" not in proc.stdout
    assert "mosh preflight failed (exit 255); falling back to SSH" in proc.stderr
    assert proc.stdout.splitlines() == [
        "<claude-coordinator>",
        "</data/sata/1TB>",
        "<claude --name claude-coordinator>",
        "<resume-id>",
        "<coordinator>",
    ]


@pytest.mark.parametrize("shell", _shells())
def test_mosh_preflight_is_read_only_and_scoped(shell: str, tmp_path: Path) -> None:
    helper = shlex.quote(str(HELPERS))
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _fake_capture_command(fake_bin, "ssh")
    capture = tmp_path / "ssh-args"
    resume_id = "b1a2f0f3-75cf-4693-9dc1-e5a5814a4c1c"
    proc = _run_shell(
        shell,
        f"""
source {helper}
export CAPTURE_FILE={shlex.quote(str(capture))}
export PATH={shlex.quote(str(fake_bin))}:$PATH
_ws_mosh_preflight_attach_or_start claude-coordinator /data/sata/1TB \
  {resume_id} coordinator sam@172.23.0.42
""",
    )

    assert proc.returncode == 0, proc.stderr
    command = capture.read_text(encoding="utf-8")
    assert "tmux has-session" in command
    assert "tmux show-environment" in command
    assert "tmux display-message" in command
    assert "#{pane_pid}" in command
    assert "ps -o comm= --ppid" in command
    assert "ps -o args=" not in command
    assert "projects/$encoded_dir/$RESUME_ID.jsonl" in command
    assert "set -e" not in command
    assert "tmux new-session" not in command
    assert "tmux send-keys" not in command
    assert "tmux kill-session" not in command


@pytest.mark.parametrize("shell", _shells())
@pytest.mark.parametrize(
    ("child_command", "marker", "expected_rc"),
    [("claude", "", 0), ("", "", 5), ("", "MESH_LIVE_COORDINATOR=1", 5)],
)
def test_mosh_preflight_distinguishes_wrapped_claude_from_stale_shell(
    shell: str, tmp_path: Path, child_command: str, marker: str, expected_rc: int
) -> None:
    helper = shlex.quote(str(HELPERS))
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    ssh = fake_bin / "ssh"
    ssh.write_text(
        "#!/bin/bash\n"
        f"ps() {{ printf '%s\\n' {shlex.quote(child_command)}; }}\n"
        "export -f ps\n"
        "for remote; do :; done\nexec bash -c \"$remote\"\n",
        encoding="utf-8",
    )
    ssh.chmod(0o755)
    tmux = fake_bin / "tmux"
    tmux.write_text(
        "#!/bin/bash\n"
        "last=''\nfor last; do :; done\n"
        "case \"$1:$last\" in\n"
        "  has-session:*) exit 0 ;;\n"
        f"  show-environment:*) [[ -n {shlex.quote(marker)} ]] && echo {shlex.quote(marker)}; "
        f"[[ -n {shlex.quote(marker)} ]] ;;\n"
        "  display-message:'#{pane_current_command}') echo bash ;;\n"
        "  display-message:'#{pane_pid}') echo 12345 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    tmux.chmod(0o755)
    proc = _run_shell(
        shell,
        f"""
source {helper}
export PATH={shlex.quote(str(fake_bin))}:$PATH
_ws_mosh_preflight_attach_or_start claude-coordinator {shlex.quote(str(tmp_path))} \
  '' coordinator sam@172.23.0.42
""",
    )

    assert proc.returncode == expected_rc
    if child_command:
        assert "not a Claude coordinator" not in proc.stderr
    elif marker:
        assert "no longer has a running Claude process" in proc.stderr
    else:
        assert "not a Claude coordinator" in proc.stderr


@pytest.mark.parametrize("shell", _shells())
def test_ssh_start_fails_closed_for_missing_repo(shell: str, tmp_path: Path) -> None:
    helper = shlex.quote(str(HELPERS))
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _fake_capture_command(fake_bin, "ssh")
    capture = tmp_path / "ssh-args"
    proc = _run_shell(
        shell,
        f"""
source {helper}
export CAPTURE_FILE={shlex.quote(str(capture))}
export PATH={shlex.quote(str(fake_bin))}:$PATH
_ws_control_host() {{ printf '%s' 'dell7670'; }}
_ws_ssh_attach_or_start_once claude-typo /data/sata/1TB/typo ''
""",
    )

    assert proc.returncode == 0, proc.stderr
    command = capture.read_text(encoding="utf-8")
    assert "missing repo dir" in command
    assert "exit 3" in command
    assert 'TARGET_DIR="/data/sata/1TB"' not in command


@pytest.mark.parametrize("shell", _shells())
def test_coordinator_start_rejects_unmarked_shell_and_sets_marker(
    shell: str, tmp_path: Path
) -> None:
    helper = shlex.quote(str(HELPERS))
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _fake_capture_command(fake_bin, "ssh")
    capture = tmp_path / "ssh-args"
    proc = _run_shell(
        shell,
        f"""
source {helper}
export CAPTURE_FILE={shlex.quote(str(capture))}
export PATH={shlex.quote(str(fake_bin))}:$PATH
_ws_control_host() {{ printf '%s' 'dell-vpn'; }}
_ws_ssh_attach_or_start_once claude-coordinator /data/sata/1TB \
  'claude --name claude-coordinator' '' coordinator
""",
    )

    assert proc.returncode == 0, proc.stderr
    command = capture.read_text(encoding="utf-8")
    assert "MESH_LIVE_COORDINATOR=1" in command
    assert "existing session $SESSION is a shell, not a Claude coordinator" in command
    assert "exit 5" in command
    assert "tmux set-environment" in command


@pytest.mark.parametrize("shell", _shells())
def test_mosh_start_fails_closed_for_missing_repo(shell: str, tmp_path: Path) -> None:
    helper = shlex.quote(str(HELPERS))
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _fake_capture_command(fake_bin, "mosh")
    capture = tmp_path / "mosh-args"
    proc = _run_shell(
        shell,
        f"""
source {helper}
export CAPTURE_FILE={shlex.quote(str(capture))}
export PATH={shlex.quote(str(fake_bin))}:$PATH
_ws_mosh_host() {{ printf '%s' 'sam@10.0.0.2'; }}
_ws_mosh_preflight_attach_or_start() {{ return 0; }}
_ws_mosh_attach_or_start claude-typo /data/sata/1TB/typo ''
""",
    )

    assert proc.returncode == 0, proc.stderr
    command = capture.read_text(encoding="utf-8")
    assert "missing repo dir" in command
    assert "exit 3" in command
    assert 'TARGET_DIR="/data/sata/1TB"' not in command


@pytest.mark.parametrize("shell", _shells())
def test_persistent_cli_helpers_start_in_repo_directory(shell: str) -> None:
    helper = shlex.quote(str(HELPERS))
    proc = _run_shell(
        shell,
        f"""
source {helper}
MESH_WS_REPO_BASE=/data/sata/1TB
_ws_ssh_attach_or_start() {{ printf 'ssh:<%s>\n' "$@"; }}
_ws_mosh_attach_or_start() {{ printf 'mosh:<%s>\n' "$@"; }}
wclaude rektslug
wcodex rektslug
mclaude rektslug
mcodex rektslug
""",
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines() == [
        "ssh:<claude-rektslug>",
        "ssh:</data/sata/1TB/rektslug>",
        "ssh:<>",
        "ssh:<codex-rektslug>",
        "ssh:</data/sata/1TB/rektslug>",
        "ssh:<>",
        "mosh:<claude-rektslug>",
        "mosh:</data/sata/1TB/rektslug>",
        "mosh:<codex-rektslug>",
        "mosh:</data/sata/1TB/rektslug>",
    ]


@pytest.mark.parametrize("shell", _shells())
def test_mcoordinator_rejects_unsafe_session_override(shell: str) -> None:
    helper = shlex.quote(str(HELPERS))
    proc = _run_shell(
        shell,
        f"source {helper}; mcoordinator --all --session 'coordinator;rm'",
    )

    assert proc.returncode == 2
    assert "Usage: mcoordinator" in proc.stderr


@pytest.mark.parametrize("shell", _shells())
def test_mcoordinator_rejects_conflicting_or_unsafe_resume(shell: str) -> None:
    helper = shlex.quote(str(HELPERS))

    conflict = _run_shell(
        shell,
        f"source {helper}; mcoordinator --all --continue --resume session-id",
    )
    unsafe = _run_shell(
        shell,
        f"source {helper}; mcoordinator --all --resume=--dangerous",
    )
    malformed = _run_shell(
        shell,
        f"source {helper}; mcoordinator --all --resume not.a.real-session",
    )

    assert conflict.returncode == 2
    assert "Usage: mcoordinator" in conflict.stderr
    assert unsafe.returncode == 2
    assert malformed.returncode == 2


@pytest.mark.parametrize("shell", _shells())
def test_resume_validation_is_after_existing_tmux_attach_and_scoped_to_target_dir(
    shell: str, tmp_path: Path
) -> None:
    helper = shlex.quote(str(HELPERS))
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _fake_capture_command(fake_bin, "ssh")
    capture = tmp_path / "ssh-args"
    resume_id = "b1a2f0f3-75cf-4693-9dc1-e5a5814a4c1c"
    proc = _run_shell(
        shell,
        f"""
source {helper}
export CAPTURE_FILE={shlex.quote(str(capture))}
export PATH={shlex.quote(str(fake_bin))}:$PATH
_ws_control_host() {{ printf '%s' 'dell7670'; }}
_ws_ssh_attach_or_start_once claude-rektslug /data/sata/1TB/rektslug \
  'claude --resume {resume_id}' {resume_id}
""",
    )

    assert proc.returncode == 0, proc.stderr
    command = capture.read_text(encoding="utf-8")
    assert command.index("tmux has-session") < command.index("Claude resume session not found")
    assert r'encoded_dir="${TARGET_DIR//\//-}"' in command
    assert 'projects/$encoded_dir/$RESUME_ID.jsonl' in command
