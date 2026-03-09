from __future__ import annotations

from src.router.account_pool import get_account_pool, next_account_for_provider


def test_get_account_pool_loads_default_and_accounts(tmp_path) -> None:
    config = tmp_path / "account_pools.yaml"
    config.write_text(
        """
providers:
  claude:
    default_account: claude-samuele
    accounts:
      - claude-samuele
      - claude-gptprojectmanager
""".strip()
        + "\n",
        encoding="utf-8",
    )

    pool = get_account_pool("claude", config_path=str(config))

    assert pool is not None
    assert pool.default_account == "claude-samuele"
    assert pool.accounts == ("claude-samuele", "claude-gptprojectmanager")


def test_next_account_for_provider_rotates_without_returning_current(tmp_path) -> None:
    config = tmp_path / "account_pools.yaml"
    config.write_text(
        """
providers:
  claude:
    default_account: claude-samuele
    accounts:
      - claude-samuele
      - claude-gptprojectmanager
      - claude-gptcoderassistant
""".strip()
        + "\n",
        encoding="utf-8",
    )

    assert (
        next_account_for_provider(
            "claude",
            "claude-samuele",
            config_path=str(config),
        )
        == "claude-gptprojectmanager"
    )
    assert (
        next_account_for_provider(
            "claude",
            "claude-gptcoderassistant",
            config_path=str(config),
        )
        == "claude-samuele"
    )
