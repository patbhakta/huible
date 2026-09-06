#!/usr/bin/env python3
"""CI config guard: reject fake-embedding configurations (HU-2732 M1.2).

The M-0 verdict (``m0_fake_embeddings``, 2026-08-31) was caused by a
production deployment silently running token-hash embeddings — the vault was
never semantically read. This guard is the CI-side tripwire so that class
cannot ship again.

Failure conditions (exit 1):

* ``EMBEDDING_PROVIDER`` set to ``fake`` or ``legacy`` in any deploy-facing
  config (compose files, deploy/ and docker/ trees, workflows, and a local
  ``.env`` when present). The production provider is ``local_onnx`` only.
* The dead ``EMBEDDING_MODEL`` legacy variable present in any deploy-facing
  config. W1 renamed the real knob to ``EMBEDDINGS_MODEL`` (bge-small-en-v1.5);
  the old name is unused, misleading drift (flagged in the M1.1 baseline) and
  is rejected outright.

``tests/`` and ``src/`` are deliberately not scanned: the key-free ``fake``
provider is the sanctioned test fixture (``settings.embedding_provider``
defaults to it so the suite runs without a model download). This guard gates
*deployment configurations*, not test fixtures.

Exit 0 prints the scanned file count. Exit 2 means the guard itself could not
run (missing path), which CI must treat as failure too.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Deploy-facing files/trees scanned (tracked). Trees are walked recursively.
SCANNED_PATHS = [
    "docker-compose.yml",
    "docker-compose.failover.yml",
    "docker",
    "deploy",
    ".github/workflows",
]

#: Untracked local env files scanned when present (deploy-window check; the
#: live deployment's actual provider comes from these).
LOCAL_ENV_FILES = [".env", ".env.failover"]

#: Provider values that mean the vault is not semantically read.
REJECTED_PROVIDERS = {"fake", "legacy"}

#: The only provider allowed in deploy-facing configs.
REQUIRED_PROVIDER = "local_onnx"

#: Dead pre-W1 variable name (real knob is ``EMBEDDINGS_MODEL``).
DEAD_EMBEDDING_MODEL_VAR = "EMBEDDING_MODEL"


class GuardError(RuntimeError):
    """A rejected configuration was found."""


def _iter_scanned_files() -> list[Path]:
    files: list[Path] = []
    for rel in SCANNED_PATHS:
        path = REPO_ROOT / rel
        if not path.exists():
            raise GuardError(f"expected deploy-facing path is missing: {path}")
        if path.is_file():
            files.append(path)
        else:
            files.extend(sorted(p for p in path.rglob("*") if p.is_file()))
    for rel in LOCAL_ENV_FILES:
        path = REPO_ROOT / rel
        if path.is_file():
            files.append(path)
    return files


def _reject(line: str, path: Path, lineno: int, reasons: list[str]) -> str | None:
    """Check one line; return the EMBEDDING_PROVIDER value seen, if any."""
    stripped = line.strip()
    if stripped.startswith("#"):
        return None
    # Normalize compose list items (``- VAR=...``) and map form (``VAR: ...``).
    normalized = stripped.lstrip("-").strip().replace('"', "").replace("'", "")
    upper = normalized.upper()
    for sep in ("=", ":"):
        if upper.startswith(f"{DEAD_EMBEDDING_MODEL_VAR}{sep}"):
            reasons.append(
                f"{path.relative_to(REPO_ROOT)}:{lineno}: dead legacy variable "
                f"{DEAD_EMBEDDING_MODEL_VAR} is set (real knob since W1 is "
                f"EMBEDDINGS_MODEL); remove it"
            )
            return None
        if upper.startswith(f"EMBEDDING_PROVIDER{sep}"):
            value = normalized.split(sep, 1)[1].strip().strip("\"'").lower()
            if value in REJECTED_PROVIDERS:
                reasons.append(
                    f"{path.relative_to(REPO_ROOT)}:{lineno}: EMBEDDING_PROVIDER="
                    f"{value!r} would serve token-hash embeddings (the "
                    f"m0_fake_embeddings failure class); deploy configs must "
                    f"pin {REQUIRED_PROVIDER!r}"
                )
            return value
    return None


def main() -> int:
    try:
        files = _iter_scanned_files()
    except GuardError as exc:
        print(f"CONFIG GUARD ERROR: {exc}", file=sys.stderr)
        return 2

    reasons: list[str] = []
    providers_seen: dict[str, str] = {}
    for path in files:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            print(f"CONFIG GUARD ERROR: cannot read {path}: {exc}", file=sys.stderr)
            return 2
        for lineno, line in enumerate(lines, start=1):
            value = _reject(line, path, lineno, reasons)
            if value is not None:
                providers_seen.setdefault(value, str(path.relative_to(REPO_ROOT)))

    if reasons:
        print("CONFIG GUARD: REJECTED — fake-embedding configuration detected:")
        for reason in reasons:
            print(f"  - {reason}")
        return 1

    pinned = {v: p for v, p in providers_seen.items() if v}
    bad = set(pinned) - {REQUIRED_PROVIDER}
    if bad:
        print(
            "CONFIG GUARD: REJECTED — EMBEDDING_PROVIDER values outside the "
            f"approved set: {sorted(bad)} (approved: {REQUIRED_PROVIDER!r})"
        )
        return 1

    print(
        f"CONFIG GUARD: OK — {len(files)} deploy-facing files scanned, "
        f"no fake/legacy embedding provider, no dead {DEAD_EMBEDDING_MODEL_VAR} var. "
        f"Providers pinned: {pinned or 'none (defaults are test-only)'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
