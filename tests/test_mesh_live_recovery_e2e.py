from __future__ import annotations

import getpass
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

import pytest


def _load_module():
    script = Path(__file__).resolve().parents[1] / "scripts" / "mesh_live_cli.py"
    spec = importlib.util.spec_from_file_location("mesh_live_recovery_e2e", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.skipif(
    not all(shutil.which(command) for command in ("tmux", "git"))
    or not (shutil.which("cc") or shutil.which("clang")),
    reason="tmux, git, and a C compiler are required",
)
@pytest.mark.parametrize("entrypoint", ["manual", "tick"])
def test_real_tmux_coordinator_recovery(
    tmp_path: Path, monkeypatch, capsys, entrypoint: str
) -> None:
    module = _load_module()
    tmux_tmp = Path(tempfile.mkdtemp(prefix="mesh-tmux-", dir="/tmp"))
    tmux_tmp.chmod(0o700)
    root = tmp_path / "coordination"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    source = tmp_path / "claude.c"
    source.write_text(
        """
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
int main(int argc, char **argv) {
    const char *path = getenv("MESH_E2E_CLAUDE_ARGS");
    FILE *output = path ? fopen(path, "w") : NULL;
    if (output) {
        for (int i = 1; i < argc; i++) fprintf(output, "%s\\n", argv[i]);
        fclose(output);
    }
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--resume") == 0 && i + 1 < argc)
            printf("RESUME:%s\\n", argv[i + 1]);
        if (strstr(argv[i], "MESH_COORDINATOR_CONTRACT: mesh.live.coordinator.v1"))
            printf("CONTRACT:present\\n");
    }
    fflush(stdout);
    sleep(30);
    return 0;
}
""".lstrip(),
        encoding="utf-8",
    )
    compiler = shutil.which("cc") or shutil.which("clang")
    assert compiler is not None
    subprocess.run(
        [compiler, "-O0", "-o", str(fake_bin / "claude"), str(source)], check=True
    )
    idle_bin = tmp_path / "idle"
    idle_bin.mkdir()
    shutil.copy2(fake_bin / "claude", idle_bin / "bash")
    if shutil.which("flock") is None:
        flock = fake_bin / "flock"
        flock.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        flock.chmod(0o755)

    resume_id = "8e34759f-4706-4573-8dff-353749499ffe"
    config = tmp_path / "claude-config"
    history = (
        config / "projects" / str(root).replace("/", "-") / f"{resume_id}.jsonl"
    )
    history.parent.mkdir(parents=True)
    history.write_text("{}\n", encoding="utf-8")
    state_file = tmp_path / "state" / "tick.json"
    session_name = "claude-e2e-coordinator"
    args_file = tmp_path / "claude.args"

    monkeypatch.setenv("TMUX_TMPDIR", str(tmux_tmp))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    monkeypatch.setenv("MESH_E2E_CLAUDE_ARGS", str(args_file))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")
    subprocess.run(
        [
            "tmux",
            "new-session",
            "-d",
            "-s",
            session_name,
            "-c",
            str(root),
            str(idle_bin / "bash"),
        ],
        check=True,
    )
    markers = {
        "MESH_LIVE_COORDINATOR": "1",
        "MESH_LIVE_CLAUDE_RESUME_ID": resume_id,
        "MESH_LIVE_COORDINATOR_ROOT": str(root),
        "MESH_LIVE_COORDINATOR_SCOPE": "all",
        "MESH_LIVE_COORDINATOR_WORKFLOW": "adaptive",
    }
    for key, value in markers.items():
        subprocess.run(
            ["tmux", "set-environment", "-t", session_name, key, value], check=True
        )
    assert subprocess.run(
        ["tmux", "has-session", "-t", session_name], check=False
    ).returncode == 0
    listed = module._run_command(["tmux", "list-sessions", "-F", "#{session_name}"])
    assert listed.returncode == 0, listed.stderr
    assert listed.stdout.strip() == session_name
    session_format = module._FIELD_SEPARATOR.join(
        [
            "#{session_name}",
            "#{session_created}",
            "#{session_activity}",
            "#{session_windows}",
            "#{session_attached}",
        ]
    )
    formatted = module._run_command(["tmux", "list-sessions", "-F", session_format])
    assert len(module._split_tmux_fields(formatted.stdout)) == 5
    discovered, warnings = module._discover_owner(getpass.getuser())
    assert warnings == []
    assert [item["name"] for item in discovered] == [session_name]

    key = f"{getpass.getuser()}/{session_name}"
    module.save_live_tick_state(
        str(state_file),
        {
            "version": 1,
            "sessions": {},
            "supervisor": {
                "signals": {
                    f"session/{key}": {
                        "stable_state": "coordinator_not_running_recoverable"
                    }
                },
                "events": [],
            },
        },
    )
    args_file.unlink(missing_ok=True)

    try:
        if entrypoint == "manual":
            argv = [
                "--local",
                "--users",
                getpass.getuser(),
                "recover-coordinator",
                session_name,
                "--apply",
                "--state-file",
                str(state_file),
                "--json",
            ]
        else:
            argv = [
                "--local",
                "--users",
                getpass.getuser(),
                "tick",
                "--apply",
                "--recover-coordinator",
                "--coordinator",
                session_name,
                "--verify-delay",
                "0",
                "--state-file",
                str(state_file),
                "--json",
            ]
        result = module.main(argv)

        assert result == 0
        payload = json.loads(capsys.readouterr().out)
        recovery = (
            payload
            if entrypoint == "manual"
            else next(
                item
                for item in payload["results"]
                if item["action"] == "recover_coordinator"
            )
        )
        assert recovery["status"] == "applied"
        assert recovery["verified"] is True
        for _attempt in range(20):
            if args_file.exists() and args_file.stat().st_size:
                break
            time.sleep(0.1)
        argv = args_file.read_text(encoding="utf-8").splitlines()
        assert argv[:2] == ["--resume", resume_id]
        assert "MESH_COORDINATOR_CONTRACT: mesh.live.coordinator.v1" in argv
    finally:
        subprocess.run(["tmux", "kill-server"], check=False)
        shutil.rmtree(tmux_tmp, ignore_errors=True)
