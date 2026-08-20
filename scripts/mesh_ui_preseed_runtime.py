#!/usr/bin/env python3
"""Preseed provider runtime state for direct mesh UI role shells."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preseed provider runtime for mesh ui role shells.")
    parser.add_argument("provider", help="Provider runtime name, for example gemini or codex.")
    parser.add_argument("work_dir", help="Target repo/work directory.")
    parser.add_argument(
        "--target-account",
        default="",
        help="Provider target account/profile. Defaults to provider-specific UI mapping.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    provider = str(args.provider).strip()
    work_dir = os.path.abspath(os.path.expanduser(str(args.work_dir).strip()))
    target_account = str(args.target_account).strip()

    if not provider or not work_dir:
        return 0

    sys.path.insert(0, str(_repo_root()))
    try:
        from src.router.session_worker import MeshSessionWorker, SessionWorkerConfig
    except Exception as exc:  # pragma: no cover - defensive runtime fallback
        print(f"[mesh-ui-preseed] import failed: {exc}", file=sys.stderr)
        return 1

    try:
        worker = MeshSessionWorker(
            SessionWorkerConfig(
                cli_type=provider,
                account_profile=target_account or provider,
            )
        )
        worker._prepare_cli_runtime(work_dir, target_account or provider)
    except Exception as exc:  # pragma: no cover - defensive runtime fallback
        print(f"[mesh-ui-preseed] preseed failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
