"""Tests for deployment scripts and configuration files."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

DEPLOY_DIR = Path(__file__).parent.parent / "deploy"
REPO_ROOT = Path(__file__).parent.parent


class TestShellScriptSyntax:
    """Validate shell scripts with bash -n (syntax check)."""

    @pytest.mark.parametrize("script", [
        "deploy-workers.sh",
        "install.sh",
        "live-compose.sh",
        "verify-network.sh",
        "ufw-setup.sh",
    ])
    def test_bash_syntax_valid(self, script):
        script_path = DEPLOY_DIR / script
        assert script_path.exists(), f"{script} not found"
        result = subprocess.run(
            ["bash", "-n", str(script_path)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Syntax error in {script}: {result.stderr}"


class TestSystemdUnits:
    """Validate systemd unit files exist and have required sections."""

    def test_router_service_exists(self):
        path = DEPLOY_DIR / "mesh-router.service"
        assert path.exists()
        content = path.read_text()
        assert "[Unit]" in content
        assert "[Service]" in content
        assert "[Install]" in content

    def test_router_service_has_required_fields(self):
        content = (DEPLOY_DIR / "mesh-router.service").read_text()
        assert "Type=notify" in content
        assert "WatchdogSec=" in content
        assert "Restart=always" in content
        assert "User=mesh" in content
        assert "NoNewPrivileges=true" in content

    def test_worker_template_exists(self):
        path = DEPLOY_DIR / "mesh-worker@.service"
        assert path.exists()
        content = path.read_text()
        assert "[Unit]" in content
        assert "[Service]" in content
        assert "[Install]" in content

    def test_worker_template_has_required_fields(self):
        content = (DEPLOY_DIR / "mesh-worker@.service").read_text()
        assert "Restart=always" in content
        assert "%i" in content  # Template variable
        assert "EnvironmentFile=" in content
        assert "NoNewPrivileges=true" in content
        assert "ReadWritePaths=/home/mesh-worker/.mesh /tmp/mesh-tasks" in content

    def test_worker_template_uses_instance_variable(self):
        content = (DEPLOY_DIR / "mesh-worker@.service").read_text()
        assert "%i" in content

    @pytest.mark.parametrize("unit_name", [
        "mesh-worker@.service",
        "mesh-review-worker@.service",
        "mesh-session-worker@.service",
    ])
    def test_worker_units_keep_group_writable_umask(self, unit_name):
        content = (DEPLOY_DIR / unit_name).read_text()
        assert "UMask=0002" in content

    @pytest.mark.parametrize("unit_name", [
        "mesh-worker@.service",
        "mesh-review-worker@.service",
        "mesh-session-worker@.service",
    ])
    def test_worker_units_load_shared_common_env(self, unit_name):
        content = (DEPLOY_DIR / unit_name).read_text()
        assert "EnvironmentFile=-/etc/mesh-worker/common.env" in content

    @pytest.mark.parametrize("unit_name", [
        "mesh-worker@.service",
        "mesh-review-worker@.service",
        "mesh-session-worker@.service",
    ])
    def test_worker_units_disable_private_tmp_for_shared_task_roots(self, unit_name):
        content = (DEPLOY_DIR / unit_name).read_text()
        assert "PrivateTmp=false" in content

    def test_worker_template_loads_batch_defaults_before_instance_env(self):
        content = (DEPLOY_DIR / "mesh-worker@.service").read_text()
        assert "EnvironmentFile=-/etc/mesh-worker/mesh-worker.batch.common.env" in content
        assert content.index("mesh-worker.batch.common.env") < content.index("/etc/mesh-worker/%i.env")

    def test_session_template_loads_session_defaults_before_instance_env(self):
        content = (DEPLOY_DIR / "mesh-session-worker@.service").read_text()
        assert "EnvironmentFile=-/etc/mesh-worker/mesh-session.common.env" in content
        assert content.index("mesh-session.common.env") < content.index("/etc/mesh-worker/%i.env")

    def test_deploy_workers_normalizes_shared_task_root(self):
        content = (DEPLOY_DIR / "deploy-workers.sh").read_text()
        assert 'local task_root="/tmp/mesh-tasks"' in content
        assert "mesh-worker:sam" in content
        assert 'chmod 2775 "$task_root"' in content
        assert 'find "$task_root" -type d -exec chmod 2775 {} +' in content
        assert '/etc/tmpfiles.d/mesh-worker.conf' in content
        assert 'systemd-tmpfiles --create /etc/tmpfiles.d/mesh-worker.conf' in content
        assert '"${PROJECT_ROOT}"/deploy/*.common.env' in content
        assert 'BATCH_WORKERS+=("$(basename "$src_env" .env | sed ' in content
        assert 'prepare_worker_uv_env' in content
        assert 'UV_PYTHON_INSTALL_DIR=/opt/mesh-worker/.uv/python' in content

    def test_install_worker_normalizes_shared_task_root(self):
        content = (DEPLOY_DIR / "install.sh").read_text()
        assert 'local task_root="/tmp/mesh-tasks"' in content
        assert "mesh-worker:sam" in content
        assert 'chmod 2775 "$task_root"' in content
        assert '/etc/tmpfiles.d/mesh-worker.conf' in content
        assert 'systemd-tmpfiles --create /etc/tmpfiles.d/mesh-worker.conf' in content
        assert 'for common_env in deploy/*.common.env; do' in content
        assert "mesh-review-worker@.service" in content

    def test_deploy_worker_installs_review_worker_template(self):
        content = (DEPLOY_DIR / "deploy-workers.sh").read_text()
        assert "mesh-review-worker@.service" in content
        assert 'mesh-session-worker@${worker}.service' in content
        assert 'mesh-review-worker@${worker}.service' in content

    def test_install_worker_strips_batch_template_prefix(self):
        content = (DEPLOY_DIR / "install.sh").read_text()
        assert 'name="${name#mesh-worker-}"' in content

    def test_install_worker_enables_session_and_review_instances(self):
        content = (DEPLOY_DIR / "install.sh").read_text()
        assert 'for env_path in /etc/mesh-worker/*.env; do' in content
        assert 'mesh-session-worker@${name}.service' in content
        assert 'mesh-review-worker@${name}.service' in content
        assert 'UV_PYTHON_INSTALL_DIR=/opt/mesh-worker/.uv/python' in content


class TestEnvironmentFiles:
    """Validate environment file templates."""

    def test_router_env_exists(self):
        path = DEPLOY_DIR / "mesh-router.env"
        assert path.exists()

    def test_router_env_has_placeholder_token(self):
        content = (DEPLOY_DIR / "mesh-router.env").read_text()
        assert "__REPLACE_WITH_TOKEN__" in content
        assert "MESH_ROUTER_PORT" in content
        assert "MESH_DB_PATH" in content

    @pytest.mark.parametrize("worker_env", [
        "mesh-worker-claude-work.env",
        "mesh-worker-codex-work.env",
        "mesh-worker-gemini-work.env",
    ])
    def test_worker_env_exists(self, worker_env):
        path = DEPLOY_DIR / worker_env
        assert path.exists()

    @pytest.mark.parametrize("worker_env", [
        "mesh-worker-claude-work.env",
        "mesh-worker-codex-work.env",
        "mesh-worker-gemini-work.env",
    ])
    def test_worker_env_omits_shared_router_fields(self, worker_env):
        content = (DEPLOY_DIR / worker_env).read_text()
        assert "MESH_WORKER_ID" in content
        assert "MESH_CLI_TYPE" in content
        assert "MESH_ROUTER_URL" not in content
        assert "MESH_AUTH_TOKEN" not in content
        assert "MESH_ALLOWED_WORK_DIRS" not in content

    def test_worker_common_env_exists(self):
        path = DEPLOY_DIR / "mesh-worker.common.env"
        assert path.exists()

    def test_worker_common_env_has_shared_placeholders(self):
        content = (DEPLOY_DIR / "mesh-worker.common.env").read_text()
        assert "MESH_ROUTER_URL" in content
        assert "MESH_AUTH_TOKEN=__REPLACE_WITH_TOKEN__" in content
        assert "MESH_ALLOWED_WORK_DIRS=" in content

    def test_batch_common_env_exists(self):
        path = DEPLOY_DIR / "mesh-worker.batch.common.env"
        assert path.exists()

    def test_batch_common_env_has_shared_batch_defaults(self):
        content = (DEPLOY_DIR / "mesh-worker.batch.common.env").read_text()
        assert "MESH_LONGPOLL_TIMEOUT_S=25" in content
        assert "MESH_DRY_RUN=1" in content
        assert "MESH_WORK_DIR=/tmp/mesh-tasks" in content
        assert "MESH_TASK_TIMEOUT_S=1800" in content

    def test_session_common_env_exists(self):
        path = DEPLOY_DIR / "mesh-session.common.env"
        assert path.exists()

    def test_session_common_env_has_shared_session_defaults(self):
        content = (DEPLOY_DIR / "mesh-session.common.env").read_text()
        assert "MESH_HEARTBEAT_TIMEOUT_S=5" in content
        assert "MESH_EXECUTION_MODES=session" in content
        assert "MESH_WORK_DIR=/tmp/mesh-tasks" in content
        assert "MESH_AUTO_COMPLETE_ON_EXIT=1" in content

    @pytest.mark.parametrize("session_env", [
        "mesh-session-claude-work.env",
        "mesh-session-codex-work.env",
        "mesh-session-gemini-work.env",
    ])
    def test_session_worker_env_exists(self, session_env):
        path = DEPLOY_DIR / session_env
        assert path.exists()

    @pytest.mark.parametrize("session_env", [
        "mesh-session-claude-work.env",
        "mesh-session-codex-work.env",
        "mesh-session-gemini-work.env",
    ])
    def test_session_worker_env_omits_shared_router_fields(self, session_env):
        content = (DEPLOY_DIR / session_env).read_text()
        assert "MESH_WORKER_ID" in content
        assert "MESH_EXECUTION_MODES=session" not in content
        assert "MESH_ROUTER_URL" not in content
        assert "MESH_AUTH_TOKEN" not in content
        assert "MESH_ALLOWED_WORK_DIRS" not in content

    def test_review_worker_env_omits_shared_router_fields(self):
        content = (DEPLOY_DIR / "mesh-review-codex.env").read_text()
        assert "MESH_REVIEWER_ID=" in content
        assert "MESH_ROUTER_URL" not in content
        assert "MESH_AUTH_TOKEN" not in content
        assert "MESH_ALLOWED_WORK_DIRS" not in content

    def test_no_plaintext_tokens(self):
        """Ensure no real tokens are accidentally committed."""
        for env_file in DEPLOY_DIR.glob("*.env"):
            content = env_file.read_text()
            # Should only have placeholder tokens
            for line in content.strip().split("\n"):
                if "TOKEN" in line and "=" in line:
                    value = line.split("=", 1)[1].strip()
                    assert value == "__REPLACE_WITH_TOKEN__", (
                        f"Possible real token in {env_file.name}: {line}"
                    )

    def test_compose_env_example_documents_external_bridge_config(self):
        content = (DEPLOY_DIR / "compose.env.example").read_text()
        assert "MESH_AUTH_TOKEN=__REPLACE_WITH_TOKEN__" in content
        assert "MESH_MATRIX_ACCESS_TOKEN=__REPLACE_WITH_MATRIX_TOKEN__" in content
        assert "MESH_MATRIX_BRIDGE_DOCKER_ENV_FILE=" in content
        assert "MESH_MATRIX_BRIDGE_CONFIG_DIR=" in content
        assert "MESH_ROUTER_BIND_HOST=" in content

    def test_matrix_bridge_env_example_documents_sender_allowlist(self):
        content = (DEPLOY_DIR / "mesh-matrix-bridge.docker.env").read_text()
        assert "MESH_MATRIX_ALLOWED_SENDERS=" in content


class TestDockerComposeConfig:
    def test_compose_uses_overrideable_bridge_env_file(self):
        content = (DEPLOY_DIR / "compose.yml").read_text()
        assert "${MESH_MATRIX_BRIDGE_DOCKER_ENV_FILE:-./mesh-matrix-bridge.docker.env}" in content

    def test_compose_mounts_bridge_config_dir(self):
        content = (DEPLOY_DIR / "compose.yml").read_text()
        assert "${MESH_MATRIX_BRIDGE_CONFIG_DIR:-./config}:/app/config:ro" in content

    def test_bridge_config_dir_is_tracked(self):
        assert (DEPLOY_DIR / "config" / ".gitkeep").exists()

    def test_live_compose_prefers_external_env_file(self):
        content = (DEPLOY_DIR / "live-compose.sh").read_text()
        assert "/etc/mesh-router/compose.env" in content
        assert "docker compose --env-file" in content
        assert "compose env file is not readable" in content
        assert "COMPOSE_DISABLE_ENV_FILE=1" in content

    def test_live_compose_script_is_executable(self):
        script_path = DEPLOY_DIR / "live-compose.sh"
        assert script_path.stat().st_mode & 0o111


class TestOperatorEnvFallbacks:
    def test_mesh_wrapper_prefers_worker_common_env(self):
        content = (REPO_ROOT / "scripts" / "mesh").read_text()
        assert '"/etc/mesh-worker/common.env"' in content

    def test_iterm_shell_supports_worker_common_env(self):
        content = (REPO_ROOT / "scripts" / "iterm-mesh-shell.sh").read_text()
        assert 'WORKER_COMMON_ENV="/etc/mesh-worker/common.env"' in content
        assert "set -a" in content

    def test_set_mesh_token_restarts_all_worker_families(self):
        content = (REPO_ROOT / "scripts" / "set-mesh-token.sh").read_text()
        assert 'case "$name" in' in content
        assert 'mesh-session-worker@${name}' in content
        assert 'mesh-review-worker@${name}' in content
        assert 'mesh-worker@${name}' in content


class TestBootOrderDoc:
    """Validate boot order documentation."""

    def test_boot_order_exists(self):
        path = DEPLOY_DIR / "BOOT-ORDER.md"
        assert path.exists()

    def test_boot_order_has_sections(self):
        content = (DEPLOY_DIR / "BOOT-ORDER.md").read_text()
        assert "VPS Startup Sequence" in content
        assert "Workstation Startup Sequence" in content
        assert "Verification" in content
        assert "Failure Recovery" in content

    def test_session_first_e2e_runbook_exists(self):
        path = DEPLOY_DIR / "SESSION-FIRST-E2E-RUNBOOK.md"
        assert path.exists()
        content = path.read_text()
        assert "Session-First E2E Runbook" in content
        assert ".111" in content
        assert ".112" in content
        assert "10.0.0.1" in content


class TestMeshScript:
    """Regression tests for the operator shell wrapper."""

    def test_mesh_status_does_not_fall_through_from_uv_to_python(self, tmp_path):
        fakebin = tmp_path / "bin"
        fakebin.mkdir()
        log_path = tmp_path / "invocations.log"

        uv_path = fakebin / "uv"
        uv_path.write_text(
            "#!/usr/bin/env bash\n"
            f"echo \"uv:$*\" >> {log_path}\n"
            "exit 0\n",
            encoding="utf-8",
        )
        uv_path.chmod(0o755)

        python_path = fakebin / "python3"
        python_path.write_text(
            "#!/usr/bin/env bash\n"
            f"echo \"python3:$*\" >> {log_path}\n"
            "exit 0\n",
            encoding="utf-8",
        )
        python_path.chmod(0o755)

        env = os.environ.copy()
        env["PATH"] = f"{fakebin}:{env['PATH']}"
        env["HOME"] = str(tmp_path)
        env["MESH_ROUTER_URL"] = "http://127.0.0.1:8780"
        env["MESH_AUTH_TOKEN"] = "test-token"
        env["MESH_ENV_FILE"] = str(tmp_path / ".env.mesh")
        env["MESH_PIPELINE_TEMPLATE"] = "gemini_team_demo"

        result = subprocess.run(
            ["bash", str(REPO_ROOT / "scripts" / "mesh"), "status"],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, result.stderr
        log_lines = log_path.read_text(encoding="utf-8").splitlines()
        assert log_lines == ["uv:run -- python -m src.meshctl status"]

    def test_mesh_ui_close_preserves_ws_repo_path_argument(self, tmp_path):
        fakebin = tmp_path / "bin"
        fakebin.mkdir()
        log_path = tmp_path / "invocations.log"

        python_path = fakebin / "python3"
        python_path.write_text(
            "#!/usr/bin/env bash\n"
            f"echo \"$*\" >> {log_path}\n"
            "exit 0\n",
            encoding="utf-8",
        )
        python_path.chmod(0o755)

        env = os.environ.copy()
        env["PATH"] = f"{fakebin}:{env['PATH']}"
        env["HOME"] = str(tmp_path)
        env["MESH_ROUTER_URL"] = "http://127.0.0.1:8780"
        env["MESH_AUTH_TOKEN"] = "test-token"

        result = subprocess.run(
            [
                "bash",
                str(REPO_ROOT / "scripts" / "mesh"),
                "ui",
                "close",
                "/media/sam/1TB/rektslug",
            ],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, result.stderr
        invocation = log_path.read_text(encoding="utf-8").strip()
        assert invocation.endswith("close /media/sam/1TB/rektslug")
