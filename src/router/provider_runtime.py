"""Provider runtime resolution for mesh workers.

This module centralizes how a logical mesh target (CLI + account/profile)
maps to the concrete command executed on the host. The mapping is user-editable
via ``mapping/provider_runtime.yaml`` and can be overridden with
``MESH_PROVIDER_RUNTIME_CONFIG``.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path

import yaml

logger = logging.getLogger("mesh.provider_runtime")


@dataclass(frozen=True)
class ProviderRuntimeRule:
    """Concrete runtime rule for a provider."""

    strategy: str
    command_template: str
    session_service_user: str = ""
    session_service_group: str = ""
    session_service_supplementary_groups: tuple[str, ...] = ()


def default_provider_runtime_config_path() -> str:
    """Return the default user-editable provider runtime config path."""
    return str(Path(__file__).resolve().parents[2] / "mapping" / "provider_runtime.yaml")


def load_provider_runtime_rules(
    config_path: str | None = None,
) -> dict[str, ProviderRuntimeRule]:
    """Load provider runtime rules from YAML.

    ``config_path`` semantics:
    - ``None``: use repository default config path
    - ``""``: disable config lookup and return no rules
    - any other string: use that file path
    """
    if config_path == "":
        return {}

    path = Path(config_path or default_provider_runtime_config_path())
    if not path.is_file():
        logger.debug("Provider runtime config not found at %s; using worker fallback", path)
        return {}

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as e:
        logger.warning("Failed to read provider runtime config %s: %s", path, e)
        return {}

    providers = raw.get("providers")
    if not isinstance(providers, dict):
        logger.warning("Provider runtime config %s has no valid 'providers' mapping", path)
        return {}

    rules: dict[str, ProviderRuntimeRule] = {}
    for cli_type, entry in providers.items():
        if not isinstance(entry, dict):
            continue
        strategy = str(entry.get("strategy", "")).strip()
        command_template = str(entry.get("command_template", "")).strip()
        if not strategy or not command_template:
            continue
        supplementary_groups = tuple(
            str(group).strip()
            for group in (entry.get("session_service_supplementary_groups") or [])
            if str(group).strip()
        )
        rules[str(cli_type).strip()] = ProviderRuntimeRule(
            strategy=strategy,
            command_template=command_template,
            session_service_user=str(entry.get("session_service_user", "")).strip(),
            session_service_group=str(entry.get("session_service_group", "")).strip(),
            session_service_supplementary_groups=supplementary_groups,
        )
    return rules


def render_command_template(
    command_template: str,
    *,
    cli_type: str,
    target_account: str,
    worker_account_profile: str,
) -> str:
    """Render a command template using supported placeholders."""
    return (
        command_template
        .replace("{provider}", cli_type)
        .replace("{target_account}", target_account)
        .replace("{account_profile}", target_account)
        .replace("{worker_account_profile}", worker_account_profile)
    )


def resolve_cli_command(
    *,
    cli_type: str,
    target_account: str,
    worker_account_profile: str,
    fallback_command: str,
    config_path: str | None = None,
) -> str:
    """Resolve the concrete command to execute for a task."""
    rules = load_provider_runtime_rules(config_path)
    rule = rules.get(cli_type)
    if rule is None:
        return render_command_template(
            fallback_command,
            cli_type=cli_type,
            target_account=target_account,
            worker_account_profile=worker_account_profile,
        )
    return render_command_template(
        rule.command_template,
        cli_type=cli_type,
        target_account=target_account,
        worker_account_profile=worker_account_profile,
    )


def resolve_session_service_identity(
    cli_type: str,
    *,
    config_path: str | None = None,
) -> dict[str, str | list[str]]:
    """Resolve optional systemd identity overrides for session workers."""
    rules = load_provider_runtime_rules(config_path)
    rule = rules.get(cli_type)
    if rule is None:
        return {
            "user": "",
            "group": "",
            "supplementary_groups": [],
        }
    return {
        "user": rule.session_service_user,
        "group": rule.session_service_group,
        "supplementary_groups": list(rule.session_service_supplementary_groups),
    }
