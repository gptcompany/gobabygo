"""Tests for deployment scripts and configuration files."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

DEPLOY_DIR = Path(__file__).parent.parent / "deploy"


class TestShellScriptSyntax:
    """Validate shell scripts with bash -n (syntax check)."""

    @pytest.mark.parametrize("script", [
        "install.sh",
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

    def test_worker_template_uses_instance_variable(self):
        content = (DEPLOY_DIR / "mesh-worker@.service").read_text()
        assert "%i" in content


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
    def test_worker_env_has_placeholder_token(self, worker_env):
        content = (DEPLOY_DIR / worker_env).read_text()
        assert "__REPLACE_WITH_TOKEN__" in content
        assert "MESH_WORKER_ID" in content
        assert "MESH_ROUTER_URL" in content

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
