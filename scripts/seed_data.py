#!/usr/bin/env python3
"""Seed script for Huible F1 testing.

Generates a synthetic persona and 1000+ memory nodes with random embeddings,
plus memory edges connecting them. Designed to validate the full schema.

Usage:
    python -m scripts.seed_data  [--url postgresql+asyncpg://user:pass@host:5432/db]
                                  [--memories 1000]
                                  [--edges 3000]
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

try:
    import asyncpg
except ImportError:
    print("Requires asyncpg. Install: pip install asyncpg pgvector")
    sys.exit(1)


PERSONA_UUID = "a0000000-0000-0000-0000-000000000001"

CONTENT_TYPES = ["narrative", "fact", "sensory", "relationship", "preference"]
TIERS = ["canonical", "derived", "accrued", "world"]
SOURCE_TYPES = ["extraction", "family_upload", "canonical_seed", "inference"]
DISCLOSURE_SCOPES = ["private", "family", "close_friends", "all_contacts"]
EDGE_TYPES = [
    "shared_participant",
    "temporal_proximity",
    "thematic",
    "causal",
    "contradiction",
    "elaboration",
]

CANONICAL_MEMORIES = [
    {
        "tier": "canonical",
        "content_type": "fact",
        "content": "Born on March 15, 1952 in Austin, Texas",
        "memory_date": "1952-03-15",
        "source_type": "canonical_seed",
    },
    {
        "tier": "canonical",
        "content_type": "fact",
        "content": "Married Eleanor Mitchell on June 8, 1975",
        "memory_date": "1975-06-08",
        "source_type": "canonical_seed",
    },
    {
        "tier": "canonical",
        "content_type": "fact",
        "content": "Had two children: Sarah (born 1978) and James (born 1981)",
        "memory_date": "1978-01-15",
        "source_type": "canonical_seed",
    },
    {
        "tier": "canonical",
        "content_type": "fact",
        "content": "Worked as a mechanical engineer at Tricon Industries for 32 years",
        "memory_date": "1974-09-01",
        "source_type": "canonical_seed",
    },
    {
        "tier": "canonical",
        "content_type": "fact",
        "content": "Passed away on November 3, 2021 at age 69",
        "memory_date": "2021-11-03",
        "source_type": "canonical_seed",
    },
    {
        "tier": "canonical",
        "content_type": "fact",
        "content": "Served in the U.S. Navy from 1970 to 1974 aboard the USS Constellation",
        "memory_date": "1970-06-01",
        "source_type": "canonical_seed",
    },
    {
        "tier": "canonical",
        "content_type": "fact",
        "content": "Lived at 2147 Oak Valley Drive, Austin, TX from 1976 to 2021",
        "memory_date": "1976-02-01",
        "source_type": "canonical_seed",
    },
    {
        "tier": "canonical",
        "content_type": "fact",
        "content": "Graduated from the University of Texas at Austin with a B.S. in Mechanical Engineering",
        "memory_date": "1970-05-20",
        "source_type": "canonical_seed",
    },
    {
        "tier": "canonical",
        "content_type": "relationship",
        "content": "Best friend was Tom Delgado since childhood; they met in first grade",
        "memory_date": "1958-09-01",
        "source_type": "canonical_seed",
    },
    {
        "tier": "canonical",
        "content_type": "relationship",
        "content": "Younger sister named Patricia, who lived in Houston",
        "memory_date": "1955-08-22",
        "source_type": "canonical_seed",
    },
]

ACCRUED_TEMPLATES = [
    ("narrative", "Always said the best barbecue in Texas was at Louie Mueller's in Taylor"),
    ("narrative", "Used to tell the kids that a good day of fishing was better than any day at the office"),
    ("narrative", "Had a habit of humming jazz tunes while working on projects in the garage"),
    ("narrative", "Would stay up late reading history books, especially about World War II"),
    ("narrative", "Kept a workshop in the garage where he built birdhouses and restored old furniture"),
    ("narrative", "Every Sunday morning he made pancakes from scratch, insisting on buttermilk"),
    ("narrative", "Told stories about his Navy days but always got quiet when talking about shore leave"),
    ("narrative", "Had a signature whistle — three short notes — that the family could recognize anywhere"),
    ("narrative", "Refused to use a GPS, insisting he could navigate anywhere with a paper map"),
    ("narrative", "Would fix any neighbor's appliance for free, saying 'machines deserve respect'"),
    ("narrative", "Coached Sarah's softball team for three seasons, even though he knew nothing about softball"),
    ("narrative", "Used to say that patience was the only tool that fixed everything else"),
    ("narrative", "Had a collection of vintage Zippo lighters from his Navy days, kept in a cedar box"),
    ("narrative", "Would call Patricia every Saturday morning without fail, rain or shine"),
    ("sensory", "The smell of sawdust and machine oil in his garage workshop"),
    ("sensory", "Sound of his old truck's engine rumbling down the street"),
    ("sensory", "Taste of his secret-recipe chili that he only made on Thanksgiving"),
    ("sensory", "The feel of his calloused hands when he'd pat you on the shoulder"),
    ("sensory", "Sight of him in his faded denim overalls, always with a pencil behind his ear"),
    ("sensory", "The sound of the screen door slamming shut every summer evening"),
    ("fact", "Favorite color was forest green, but he'd never admit it"),
    ("fact", "Drank black coffee exclusively — never added sugar or cream after 1978"),
    ("fact", "Drove a 1989 Ford F-150 that he maintained himself until the day he died"),
    ("fact", "Liked to listen to Willie Nelson, Merle Haggard, and old Sinatra records"),
    ("fact", "Could name every president in order and their vice presidents"),
    ("fact", "Never learned to use a smartphone, preferring his flip phone until the end"),
    ("fact", "Kept a pocket knife on him at all times — a Buck knife his father gave him"),
    ("fact", "Voted in every election from 1972 onward, considering it a duty"),
    ("relationship", "Had a warm but competitive friendship with his brother-in-law Carl"),
    ("relationship", "Was fiercely protective of Patricia, especially after her divorce in 1992"),
    ("relationship", "Mentored a young engineer named Danny Chen at Tricon for over a decade"),
    ("relationship", "Had a teasing but loving relationship with his daughter Sarah"),
    ("relationship", "Was quietly proud of James for following him into engineering"),
    ("preference", "Preferred sunsets over sunrises, saying 'the day earned its beauty by then'"),
    ("preference", "Liked his steaks medium-rare and would send back anything well-done"),
    ("preference", "Preferred paper books over e-readers, saying you can't smell a screen"),
    ("preference", "Always sat in the back pew at church, claiming it was the best acoustic spot"),
    ("preference", "Favored mechanical watches, owning two that he wound every night"),
    ("preference", "Liked old Westerns, especially anything with John Wayne"),
    ("preference", "Preferred dogs over cats, having owned three golden retrievers over his lifetime"),
    ("preference", "Would always order iced tea at restaurants, even in winter"),
]

CONTENT_VARIANTS = [
    "Remembered the summer of {year} when {event}",
    "Would sometimes mention {event} from back in the day",
    "Had a story about {event} that he'd tell at every family gathering",
    "Used to laugh about {event}",
    "Often recalled {event}",
    "Had strong feelings about {event}",
    "Brought up {event} whenever the topic came up",
    "Was sentimental about {event}",
    "Never forgot {event}",
    "Associated the smell of autumn with {event}",
]

EVENTS = [
    "the time the truck broke down on the way to Marfa",
    "the camping trip to Big Bend where James saw his first rattlesnake",
    "the Christmas when Sarah asked for a pony and got a bicycle instead",
    "the summer Tom taught him to water-ski at Lake Travis",
    "Eleanor's 40th birthday surprise party that almost got ruined by the rain",
    "the Navy reunion in San Diego in 1995",
    "building the treehouse for Sarah when she was eight",
    "the year the oak tree in the front yard blew down in a storm",
    "teaching James to drive in the empty church parking lot",
    "the fishing trip where he caught a 12-pound bass",
    "the chili cookoff at the office that he won three years running",
    "the road trip to Graceland with Tom in 1985",
    "watching the Apollo moon landing on the black-and-white TV with his father",
    "the day he got his engineering license",
    "the time he fixed the church's HVAC system the week before Christmas",
    "the summer of the great Texas drought when he hand-watered the garden",
    "Patricia's wedding at the small chapel in Houston",
    "the day James graduated from UT Austin",
    "the neighborhood Fourth of July block parties he organized",
    "the winter of 1983 when the pipes froze and he had to fix them at 3 AM",
]


def deterministic_float(seed: bytes, idx: int, dim: int) -> float:
    h = hashlib.sha256(seed + idx.to_bytes(4, "big") + dim.to_bytes(4, "big")).digest()
    val = int.from_bytes(h[:8], "big") / (2**64)
    return val * 2.0 - 1.0


def make_embedding(seed: bytes, idx: int, dims: int) -> list[float]:
    raw = [deterministic_float(seed, idx, d) for d in range(dims)]
    norm = sum(v * v for v in raw) ** 0.5
    return [v / norm for v in raw]


def generate_memories(count: int, seed_val: int = 42) -> list[dict[str, Any]]:
    rng = random.Random(seed_val)
    memories: list[dict[str, Any]] = []

    for mem_def in CANONICAL_MEMORIES:
        memories.append({
            "persona_id": PERSONA_UUID,
            "tier": mem_def["tier"],
            "content": mem_def["content"],
            "content_type": mem_def["content_type"],
            "memory_date": mem_def["memory_date"],
            "source_type": mem_def["source_type"],
            "disclosure_scope": "family",
            "metadata": json.dumps({"seed": True}),
        })

    for i in range(count - len(CANONICAL_MEMORIES)):
        if rng.random() < 0.4 and ACCRUED_TEMPLATES:
            tmpl = rng.choice(ACCRUED_TEMPLATES)
            content = tmpl[1]
            ct = tmpl[0]
        else:
            variant = rng.choice(CONTENT_VARIANTS)
            event = rng.choice(EVENTS)
            content = variant.format(year=rng.randint(1960, 2020), event=event)
            ct = rng.choice(CONTENT_TYPES)

        base_date = date(1955, 1, 1) + timedelta(days=rng.randint(0, 24000))
        tier_weights = [0.15, 0.25, 0.45, 0.15]
        tier = rng.choices(TIERS, weights=tier_weights, k=1)[0]

        memories.append({
            "persona_id": PERSONA_UUID,
            "tier": tier,
            "content": content,
            "content_type": ct,
            "memory_date": base_date.isoformat(),
            "source_type": rng.choice(["extraction", "family_upload", "inference"]),
            "disclosure_scope": rng.choices(
                DISCLOSURE_SCOPES, weights=[0.1, 0.4, 0.3, 0.2], k=1
            )[0],
            "metadata": json.dumps({"seed": True, "index": i}),
        })

    return memories


def generate_edges(memory_ids: list[str], count: int, seed_val: int = 42) -> list[dict[str, Any]]:
    rng = random.Random(seed_val + 1000)
    edges: list[dict[str, Any]] = []
    used_pairs: set[tuple[str, str, str]] = set()

    attempts = 0
    while len(edges) < count and attempts < count * 3:
        attempts += 1
        src = rng.choice(memory_ids)
        tgt = rng.choice(memory_ids)
        if src == tgt:
            continue
        et = rng.choice(EDGE_TYPES)
        pair = (src, tgt, et)
        if pair in used_pairs:
            continue
        used_pairs.add(pair)
        edges.append({
            "source_id": src,
            "target_id": tgt,
            "edge_type": et,
            "weight": round(rng.uniform(0.3, 1.0), 2),
            "metadata": json.dumps({"seed": True}),
        })

    return edges


async def run_seed(url: str, memory_count: int, edge_count: int) -> None:
    conn = await asyncpg.connect(url)
    try:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector IF NOT EXISTS")

        existing = await conn.fetchval("SELECT count(*) FROM personas WHERE id = $1", PERSONA_UUID)
        if existing:
            print(f"Persona {PERSONA_UUID} already exists, skipping seed")
            return

        await conn.execute(
            """
            INSERT INTO personas (id, name, display_name, age_at_death, death_date, birth_date,
                                   voice_instructions, era_knowledge_boundary, metadata)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            """,
            PERSONA_UUID,
            "Robert James Mitchell",
            "Bob Mitchell",
            69,
            date(2021, 11, 3),
            date(1952, 3, 15),
            "Speak like a warm, straightforward Texan. Use occasional 'y'all' and 'fixin to'. "
            "Dry sense of humor. Short sentences. Never overly formal. "
            "Occasionally reference Navy service, engineering work, or fishing.",
            "2021-11-03",
            json.dumps({"seed": True, "era": "1950-2021"}),
        )
        print(f"Created persona: Robert James Mitchell ({PERSONA_UUID})")

        memories = generate_memories(memory_count)
        print(f"Generated {len(memories)} memories (inserting in batches)...")

        batch_size = 100
        memory_ids: list[str] = []
        for batch_start in range(0, len(memories), batch_size):
            batch = memories[batch_start : batch_start + batch_size]
            rows = []
            for i, mem in enumerate(batch):
                global_idx = batch_start + i
                emb_content = make_embedding(b"content", global_idx, 1536)
                emb_sensory = make_embedding(b"sensory", global_idx, 1536)
                emb_affect = make_embedding(b"affect", global_idx, 512)

                rows.append((
                    mem["persona_id"],
                    mem["tier"],
                    mem["content"],
                    mem["content_type"],
                    emb_content,
                    emb_sensory,
                    emb_affect,
                    mem["memory_date"],
                    mem["source_type"],
                    mem["disclosure_scope"],
                    mem["metadata"],
                ))

            result = await conn.executemany(
                """
                INSERT INTO memories
                    (persona_id, tier, content, content_type,
                     embedding_content, embedding_sensory, embedding_affect,
                     memory_date, source_type, disclosure_scope, metadata)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8::date, $9, $10, $11)
                RETURNING id
                """,
                rows,
            )
            memory_ids.extend(r[0] for r in result if r)
            print(f"  Inserted {min(batch_start + batch_size, len(memories))}/{len(memories)} memories")

        print(f"Inserted {len(memory_ids)} memories total")

        edges = generate_edges(memory_ids, edge_count)
        print(f"Generated {len(edges)} edges (inserting in batches)...")

        for batch_start in range(0, len(edges), batch_size):
            batch = edges[batch_start : batch_start + batch_size]
            await conn.executemany(
                """
                INSERT INTO memory_edges (source_id, target_id, edge_type, weight, metadata)
                VALUES ($1, $2, $3, $4, $5)
                """,
                [(e["source_id"], e["target_id"], e["edge_type"], e["weight"], e["metadata"]) for e in batch],
            )
            print(f"  Inserted {min(batch_start + batch_size, len(edges))}/{len(edges)} edges")

        print(f"Seed complete: {len(memory_ids)} memories, {len(edges)} edges")

        stats = await conn.fetchrow(
            """
            SELECT
                count(*) as total_memories,
                count(*) FILTER (WHERE tier = 'canonical') as canonical,
                count(*) FILTER (WHERE tier = 'accrued') as accrued,
                count(*) FILTER (WHERE tier = 'derived') as derived,
                count(*) FILTER (WHERE tier = 'world') as world,
                count(*) FILTER (WHERE embedding_content IS NOT NULL) as has_content_emb,
                count(*) FILTER (WHERE embedding_sensory IS NOT NULL) as has_sensory_emb,
                count(*) FILTER (WHERE embedding_affect IS NOT NULL) as has_affect_emb,
                count(*) FILTER (WHERE disclosure_scope = 'private') as private,
                count(*) FILTER (WHERE disclosure_scope = 'family') as family,
                count(*) FILTER (WHERE disclosure_scope = 'close_friends') as close_friends,
                count(*) FILTER (WHERE disclosure_scope = 'all_contacts') as all_contacts
            FROM memories
            """
        )
        print("\nSchema statistics:")
        for k, v in stats.items():
            print(f"  {k}: {v}")

        edge_count_row = await conn.fetchrow("SELECT count(*) as total FROM memory_edges")
        print(f"  total_edges: {edge_count_row['total']}")

        print("\nVerifying HNSW indexes exist...")
        indexes = await conn.fetch(
            "SELECT indexname FROM pg_indexes WHERE indexname LIKE 'idx_memories_%_emb'"
        )
        for idx_row in indexes:
            print(f"  Index found: {idx_row['indexname']}")
        assert len(indexes) == 3, f"Expected 3 HNSW indexes, found {len(indexes)}"
        print("All 3 HNSW indexes verified.")
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed Huible database for F1 testing")
    parser.add_argument(
        "--url",
        default="postgresql://postgres:postgres@localhost:5432/huible",
        help="Database connection URL",
    )
    parser.add_argument("--memories", type=int, default=1050, help="Number of memories to generate")
    parser.add_argument("--edges", type=int, default=3000, help="Number of edges to generate")
    args = parser.parse_args()

    asyncio.run(run_seed(args.url, args.memories, args.edges))


if __name__ == "__main__":
    main()
