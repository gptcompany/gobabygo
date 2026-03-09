"""User-editable provider account pool loading for router/runtime logic."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from pathlib import Path

import yaml

logger = logging.getLogger("mesh.account_pool")


@dataclass(frozen=True)
class AccountPool:
    """Configured accounts for a provider."""

    default_account: str = ""
    accounts: tuple[str, ...] = ()


def default_account_pool_config_path() -> str:
    """Return the repository default account pool config path."""
    return str(Path(__file__).resolve().parents[2] / "mapping" / "account_pools.yaml")


def load_account_pools(config_path: str | None = None) -> dict[str, AccountPool]:
    """Load provider account pools from YAML.

    ``config_path`` semantics:
    - ``None``: use ``MESH_ACCOUNT_POOL_CONFIG`` or repository default
    - ``""``: disable pool lookup and return no pools
    - any other string: use that file path
    """
    if config_path == "":
        return {}

    path_value = config_path
    if path_value is None:
        path_value = os.environ.get("MESH_ACCOUNT_POOL_CONFIG") or default_account_pool_config_path()

    path = Path(path_value)
    if not path.is_file():
        return {}

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as e:
        logger.warning("Failed to read account pool config %s: %s", path, e)
        return {}

    providers = raw.get("providers")
    if not isinstance(providers, dict):
        return {}

    pools: dict[str, AccountPool] = {}
    for provider, entry in providers.items():
        if not isinstance(entry, dict):
            continue
        default_account = str(entry.get("default_account", "")).strip()
        raw_accounts = entry.get("accounts")
        accounts: list[str] = []
        if isinstance(raw_accounts, list):
            for candidate in raw_accounts:
                value = str(candidate).strip()
                if value and value not in accounts:
                    accounts.append(value)
        if default_account and default_account not in accounts:
            accounts.insert(0, default_account)
        pools[str(provider).strip()] = AccountPool(
            default_account=default_account,
            accounts=tuple(accounts),
        )
    return pools


def get_account_pool(provider: str, *, config_path: str | None = None) -> AccountPool | None:
    """Return the configured pool for *provider*, if any."""
    return load_account_pools(config_path).get(str(provider).strip())


def next_account_for_provider(
    provider: str,
    current_account: str,
    *,
    config_path: str | None = None,
) -> str | None:
    """Return the next configured account for *provider* after *current_account*.

    Rotation is deterministic and wraps once, but never returns the current account.
    """
    pool = get_account_pool(provider, config_path=config_path)
    if pool is None:
        return None

    accounts = [account for account in pool.accounts if account]
    if not accounts:
        return None

    current = str(current_account).strip()
    if not current:
        return accounts[0]

    if current in accounts:
        idx = accounts.index(current)
        ordered = accounts[idx + 1 :] + accounts[:idx]
    else:
        ordered = accounts

    for candidate in ordered:
        if candidate != current:
            return candidate
    return None
