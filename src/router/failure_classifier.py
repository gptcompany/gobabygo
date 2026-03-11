"""Failure classification helpers for worker-reported CLI errors."""

from __future__ import annotations


_GENERIC_ACCOUNT_EXHAUSTED_PATTERNS = (
    "you've hit your limit",
    "you hit your limit",
    "you're out of extra usage",
    "you are out of extra usage",
    "hit your limit",
    "rate_limit_error",
    "rate limit error",
    "rate limit",
    "would exceed your account's rate limit",
    "api error: 429",
    " 429 ",
    "429 {",
    "429\t",
)

_PROVIDER_ACCOUNT_EXHAUSTED_PATTERNS = {
    "codex": (
        "rate_limit_exceeded",
        "rate limit reached",
        "rate limit exceeded",
        "insufficient_quota",
        "too many requests",
    ),
    "gemini": (
        "resource_exhausted",
        "resource exhausted",
        "quota exceeded",
        "exceeded your current quota",
        "429 resource",
    ),
}


def classify_cli_failure(cli_type: str, text: str) -> str:
    """Return a machine-readable failure kind for *text*.

    The current classification surface is intentionally small:
    - ``account_exhausted`` for Claude subscription/account limit failures
    - ``""`` when no classification applies
    """
    provider = str(cli_type or "").strip().lower()
    body = str(text or "").strip().lower()
    if not body:
        return ""

    patterns = list(_GENERIC_ACCOUNT_EXHAUSTED_PATTERNS)
    patterns.extend(_PROVIDER_ACCOUNT_EXHAUSTED_PATTERNS.get(provider, ()))
    for pattern in patterns:
        if pattern in body:
            return "account_exhausted"
    return ""
