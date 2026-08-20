from __future__ import annotations

import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install-mesh-live-cron.sh"
MESH = ROOT / "scripts" / "mesh"


def _fake_crontab(tmp_path: Path) -> tuple[dict[str, str], Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    store = tmp_path / "crontab"
    command = bin_dir / "crontab"
    command.write_text(
        """#!/bin/sh
set -eu
case "${1:-}" in
  -l)
    if test "${CRONTAB_FAIL_READ:-0}" = 1; then
      echo "permission denied" >&2
      exit 1
    fi
    test -f "$CRONTAB_STORE" || {
      echo "no crontab for test-user" >&2
      exit 1
    }
    cat "$CRONTAB_STORE"
    ;;
  -)
    cat >"$CRONTAB_STORE"
    ;;
  *) exit 2 ;;
esac
""",
        encoding="utf-8",
    )
    command.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["CRONTAB_STORE"] = str(store)
    env["HOME"] = str(tmp_path / "home")
    return env, store


def _run_installer(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(INSTALLER), *args],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def test_cron_installer_is_idempotent_and_remove_is_scoped(tmp_path: Path) -> None:
    env, store = _fake_crontab(tmp_path)
    state = tmp_path / "state" / "tick.json"
    speckit_state = tmp_path / "state" / "speckit.json"
    log = tmp_path / "log" / "tick.log"
    store.write_text("15 2 * * * /usr/local/bin/unrelated\n", encoding="utf-8")
    args = (
        "--interval",
        "30",
        "--mesh-script",
        str(MESH),
        "--state-file",
        str(state),
        "--speckit-state-file",
        str(speckit_state),
        "--log-file",
        str(log),
    )

    for _ in range(2):
        proc = _run_installer(env, *args)
        assert proc.returncode == 0, proc.stderr

    content = store.read_text(encoding="utf-8")
    assert content.count("# >>> gobabygo-mesh-live-tick >>>") == 1
    assert content.count("# <<< gobabygo-mesh-live-tick <<<") == 1
    assert "15 2 * * * /usr/local/bin/unrelated" in content
    assert "*/30 * * * * MESH_LIVE_LOCAL=1" in content
    assert f"'{MESH}' live tick --apply" in content
    assert f"--state-file '{state}'" in content
    assert "17 3 * * * MESH_SPECKIT_UPDATE_STATE=" in content
    assert f"MESH_SPECKIT_UPDATE_STATE='{speckit_state}'" in content
    assert f"'{MESH}' speckit update-check --json" in content
    assert f">>'{log}' 2>&1" in content
    assert log.stat().st_mode & 0o777 == 0o600

    removed = _run_installer(env, "--remove")
    assert removed.returncode == 0, removed.stderr
    content = store.read_text(encoding="utf-8")
    assert "gobabygo-mesh-live-tick" not in content
    assert "15 2 * * * /usr/local/bin/unrelated" in content


def test_cron_installer_dry_run_does_not_modify_crontab(tmp_path: Path) -> None:
    env, store = _fake_crontab(tmp_path)
    original = "0 3 * * * /usr/local/bin/backup\n"
    store.write_text(original, encoding="utf-8")

    proc = _run_installer(
        env,
        "--dry-run",
        "--mesh-script",
        str(MESH),
        "--state-file",
        str(tmp_path / "state.json"),
        "--log-file",
        str(tmp_path / "tick.log"),
    )

    assert proc.returncode == 0, proc.stderr
    assert "live tick --apply" in proc.stdout
    assert store.read_text(encoding="utf-8") == original


def test_cron_installer_rejects_unsafe_or_invalid_values(tmp_path: Path) -> None:
    env, _store = _fake_crontab(tmp_path)

    invalid_interval = _run_installer(env, "--interval", "60")
    assert invalid_interval.returncode == 2
    assert "1 to 59" in invalid_interval.stderr

    unsafe_path = _run_installer(
        env,
        "--mesh-script",
        str(MESH),
        "--state-file",
        str(tmp_path / "bad%path"),
        "--log-file",
        str(tmp_path / "tick.log"),
        "--dry-run",
    )
    assert unsafe_path.returncode == 2
    assert "must not contain" in unsafe_path.stderr


def test_cron_installer_fails_closed_on_read_error_or_malformed_markers(
    tmp_path: Path,
) -> None:
    env, store = _fake_crontab(tmp_path)
    original = "0 3 * * * /usr/local/bin/backup\n"
    store.write_text(original, encoding="utf-8")
    args = ("--mesh-script", str(MESH), "--dry-run")

    env["CRONTAB_FAIL_READ"] = "1"
    failed_read = _run_installer(env, *args)
    assert failed_read.returncode == 2
    assert "unable to read existing crontab" in failed_read.stderr
    assert store.read_text(encoding="utf-8") == original

    env.pop("CRONTAB_FAIL_READ")
    malformed = f"{original}# >>> gobabygo-mesh-live-tick >>>\n"
    store.write_text(malformed, encoding="utf-8")
    failed_marker = _run_installer(env, *args)
    assert failed_marker.returncode == 2
    assert "marker block is malformed" in failed_marker.stderr
    assert store.read_text(encoding="utf-8") == malformed
