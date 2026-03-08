from __future__ import annotations

import textwrap

from src.router.provider_runtime import (
    default_provider_runtime_config_path,
    load_provider_runtime_rules,
    render_command_template,
    resolve_cli_command,
    resolve_session_service_identity,
)


def test_default_provider_runtime_config_path() -> None:
    assert default_provider_runtime_config_path().endswith("mapping/provider_runtime.yaml")


def test_load_provider_runtime_rules_from_yaml(tmp_path) -> None:
    config = tmp_path / "provider_runtime.yaml"
    config.write_text(
        textwrap.dedent(
            """
            version: 1
            providers:
              claude:
                strategy: ccs_account_profile
                command_template: "ccs {target_account}"
                session_service_user: "sam"
                session_service_group: "sam"
                session_service_supplementary_groups: ["mesh"]
              codex:
                strategy: cliproxy_provider
                command_template: "ccs codex"
            """
        ),
        encoding="utf-8",
    )
    rules = load_provider_runtime_rules(str(config))
    assert rules["claude"].strategy == "ccs_account_profile"
    assert rules["claude"].command_template == "ccs {target_account}"
    assert rules["claude"].session_service_user == "sam"
    assert rules["claude"].session_service_group == "sam"
    assert rules["claude"].session_service_supplementary_groups == ("mesh",)
    assert rules["codex"].command_template == "ccs codex"


def test_render_command_template_replaces_supported_placeholders() -> None:
    rendered = render_command_template(
        "ccs {provider} --use {target_account} --worker {worker_account_profile}",
        cli_type="codex",
        target_account="review-codex",
        worker_account_profile="worker-codex",
    )
    assert rendered == "ccs codex --use review-codex --worker worker-codex"


def test_resolve_cli_command_uses_provider_rule(tmp_path) -> None:
    config = tmp_path / "provider_runtime.yaml"
    config.write_text(
        textwrap.dedent(
            """
            version: 1
            providers:
              claude:
                strategy: ccs_account_profile
                command_template: "ccs {target_account}"
              codex:
                strategy: cliproxy_provider
                command_template: "ccs codex --effort high"
            """
        ),
        encoding="utf-8",
    )
    command = resolve_cli_command(
        cli_type="codex",
        target_account="review-codex",
        worker_account_profile="work-codex",
        fallback_command="ccs {target_account}",
        config_path=str(config),
    )
    assert command == "ccs codex --effort high"


def test_resolve_cli_command_falls_back_when_config_disabled() -> None:
    command = resolve_cli_command(
        cli_type="claude",
        target_account="claude-rektslug",
        worker_account_profile="work-claude",
        fallback_command="ccs {target_account} --worker {worker_account_profile}",
        config_path="",
    )
    assert command == "ccs claude-rektslug --worker work-claude"


def test_resolve_session_service_identity_uses_provider_rule(tmp_path) -> None:
    config = tmp_path / "provider_runtime.yaml"
    config.write_text(
        textwrap.dedent(
            """
            version: 1
            providers:
              claude:
                strategy: ccs_account_profile
                command_template: "ccs {target_account}"
                session_service_user: "sam"
                session_service_group: "sam"
                session_service_supplementary_groups:
                  - "mesh"
                  - "dialout"
            """
        ),
        encoding="utf-8",
    )
    identity = resolve_session_service_identity("claude", config_path=str(config))
    assert identity == {
        "user": "sam",
        "group": "sam",
        "supplementary_groups": ["mesh", "dialout"],
    }
