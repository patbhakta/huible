from __future__ import annotations

import argparse

from . import ingest_paths
from .config import IngestConfig


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m huible.vault_ingest",
        description="Vault ingestion pipeline v1 (CPU-only PDF router + media atoms)",
    )
    parser.add_argument("inputs", nargs="+", help="files: .pdf, audio, video, image")
    parser.add_argument("--out", required=True, help="output root (vault/ + derived/ layout)")
    args = parser.parse_args()

    config = IngestConfig.from_env()
    result = ingest_paths(args.inputs, args.out, config)
    print(f"atoms: {result['atom_count']} -> {result['manifest']}")
    for run in result["runs"]:
        summary = {k: v for k, v in run.items() if k not in ("pages", "probe")}
        print(summary)


if __name__ == "__main__":
    main()
