#!/usr/bin/env python3
"""CLI: monthly environment-gist refresh for registered clients (HU-2194).

Cron-compatible wrapper over ``modules/onboarding/env_context.py`` — silent on
success, prints + nonzero exit on failure (mirrors the Hermes
env-context-refresh contract). Replaces the Pat-hardcoded Hermes script with
the per-client registry; one cron line now serves every onboarded client.

Usage:
  python3 scripts/env_context_refresh.py --all            # every registered client
  python3 scripts/env_context_refresh.py --client pat     # one client
  python3 scripts/env_context_refresh.py --all --dry-run  # print gists, no push
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = REPO_ROOT / "onboarding" / "env-context-clients.json"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "modules_onboarding_env_context",
        REPO_ROOT / "modules" / "onboarding" / "env_context.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--all", action="store_true", help="refresh every registered client (default)")
    target.add_argument("--client", metavar="KEY", help="refresh one registry key")
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    parser.add_argument("--dry-run", action="store_true", help="print gists, do not push")
    parser.add_argument("--base-url", default=None, help="TencentDB TDAI base URL")
    args = parser.parse_args()

    env_context = _load_module()

    try:
        clients = env_context.load_registry(args.registry)
        if args.client:
            clients = [c for c in clients if c.key == args.client]
            if not clients:
                print(f"env-context: no registry entry for key {args.client!r}")
                return 1
        for client in clients:
            env_context.refresh(
                client,
                dry_run=args.dry_run,
                **({"base_url": args.base_url} if args.base_url else {}),
            )
            if args.dry_run:
                print(f"[dry-run] {client.atom_id}: refreshed OK (not pushed)")
    except Exception as e:
        print(f"env-context refresh failed: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
