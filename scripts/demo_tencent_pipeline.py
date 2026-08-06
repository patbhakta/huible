#!/usr/bin/env python3
"""
TencentDB L0-L3 pipeline wired into the Huible persona engine (Phase 3)
=========================================================================
Demonstrates the full flow:

1. Load raw uploads (conversations/photos) as L0 records.
2. Run TencentDB distillation L0 -> L1 -> L2 -> L3.
3. Write consolidated Markdown (source of truth) with temporal frontmatter
   (valid_from / valid_to) and memory TYPES.
4. The Huible persona answers from that Markdown — not raw vectors —
   with evidence back-links to the raw L0 source.

Usage:
    python -m scripts.demo_tencent_pipeline [--csv data/sample_memories.csv] [--query ...]
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import csv
from datetime import UTC, datetime
from pathlib import Path

from huible.distillation import (
    Distiller,
    L0Record,
    MarkdownMemoryStore,
    MarkdownPersonaResponder,
)


def _load_raw(csv_path: str | Path) -> list[L0Record]:
    records: list[L0Record] = []
    with open(csv_path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            content = (row.get("content") or "").strip()
            if not content:
                continue
            occurred_at = None
            with contextlib.suppress(ValueError):
                occurred_at = datetime.fromisoformat(
                    row.get("memory_date", "")
                ).replace(tzinfo=UTC)
            records.append(
                L0Record(
                    kind="conversation",
                    title=content[:48],
                    content=content,
                    occurred_at=occurred_at,
                    metadata={"memory_date": row.get("memory_date")},
                )
            )
    return records


async def main(csv_path: str, query: str, out_dir: str) -> None:
    records = _load_raw(csv_path)
    print(f"Loaded {len(records)} L0 raw records from {csv_path}")

    distiller = Distiller()
    result = await distiller.distill(records)
    print(
        f"Distilled: {len(result.facts)} L1 facts, "
        f"{len(result.scenarios)} L2 scenarios, {len(result.profiles)} L3 profiles"
    )

    store = MarkdownMemoryStore(out_dir)
    store.write_result(result)
    print(f"Wrote consolidated Markdown to {out_dir}/")

    print("\n=== L3 profiles (temporal Markdown frontmatter) ===")
    for profile in result.profiles:
        print(
            f"  [{profile.memory_type.value}] {profile.rule!r} "
            f"(valid_from={profile.valid_from}, evidence-> {profile.evidence[0].source_id[:8]})"
        )

    print("\n=== Temporal query: active current states + durable rules ===")
    states = store.current_states()
    rules = store.active_rules()
    print(f"  current_state records: {len(states)}")
    print(f"  durable_rule records:  {len(rules)}")

    print("\n=== Persona responds from Markdown (not raw vectors) ===")
    responder = MarkdownPersonaResponder(store, persona_name="Pat")
    response = responder.respond(query)
    print(f"Q: {query}\n")
    print(response.reply)
    print(f"\nMatched {len(response.hits)} markdown memory records.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", default="data/sample_memories.csv")
    parser.add_argument("--query", default="What tea does Pat like?")
    parser.add_argument("--out", default="/tmp/huible_markdown_memory")
    args = parser.parse_args()
    asyncio.run(main(args.csv, args.query, args.out))
