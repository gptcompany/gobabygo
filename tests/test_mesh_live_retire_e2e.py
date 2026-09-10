"""Opt-in isolated tmux exercise for the guarded worker retirement path."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

import pytest


def _load_module():
    script = Path(__file__).resolve().parents[1] / "scripts" / "mesh_live_retire.py"
    sys.path.insert(0, str(script.parent))
    try:
        spec = importlib.util.spec_from_file_location("mesh_live_retire_e2e", script)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


@pytest.mark.skipif(
    not all(shutil.which(command) for command in ("tmux", "git"))
    or not (shutil.which("cc") or shutil.which("clang")),
    reason="tmux, git, and a C compiler are required",
)
def test_real_isolated_tmux_worker_retirement(tmp_path: Path, monkeypatch) -> None:
    module = _load_module()
    tmux_tmp = Path(tempfile.mkdtemp(prefix="mesh-retire-tmux-", dir="/tmp"))
    tmux_tmp.chmod(0o700)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    handoff = tmp_path / "handoff.md"
    handoff.write_text("DLG-OLD was superseded; no composer text was submitted.\n", encoding="utf-8")
    source = tmp_path / "codex.c"
    executable = tmp_path / "codex"
    source.write_text(
        "#include <stdio.h>\n#include <unistd.h>\n"
        "int main(void) { puts(\"idle\"); puts(\"› \"); puts(\"  gpt-5.4 · repo\"); fflush(stdout); sleep(30); return 0; }\n",
        encoding="utf-8",
    )
    compiler = shutil.which("cc") or shutil.which("clang")
    assert compiler is not None
    subprocess.run([compiler, "-O0", "-o", str(executable), str(source)], check=True)
    session = "codex-retire-e2e"
    state = tmp_path / "retire.json"
    monkeypatch.setenv("TMUX_TMPDIR", str(tmux_tmp))
    # This Mac tmux fixture does not preserve Codex's Unicode prompt glyph. The
    # provider UI classifier itself is unit-tested; this exercise covers real
    # isolated tmux identity, Git, durable state, and termination ordering.
    monkeypatch.setattr(module, "session_screen_state", lambda _session: "idle")
    subprocess.run(
        ["tmux", "new-session", "-d", "-s", session, "-c", str(repo), str(executable)],
        check=True,
    )
    try:
        for _attempt in range(20):
            pane = subprocess.run(
                ["tmux", "display-message", "-p", "-t", f"={session}:0.0", "#{pane_current_command}"],
                check=True, capture_output=True, text=True,
            ).stdout.strip()
            if pane == "codex":
                break
            time.sleep(0.1)
        assert pane == "codex"

        planned = module.retire_worker(
            session, str(handoff), "superseded by tracked delegation",
            apply=False, confirm="", state_file=str(state),
        )
        assert planned["status"] == "planned"
        assert subprocess.run(["tmux", "has-session", "-t", session], check=False).returncode == 0

        applied = module.retire_worker(
            session, str(handoff), "superseded by tracked delegation",
            apply=True, confirm=session, state_file=str(state),
        )
        assert applied["status"] == "applied"
        assert subprocess.run(["tmux", "has-session", "-t", session], check=False).returncode != 0
    finally:
        subprocess.run(["tmux", "kill-server"], check=False)
        shutil.rmtree(tmux_tmp, ignore_errors=True)
