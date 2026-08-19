#!/usr/bin/env python3
"""Provision an onboarded persona into the live huible-app store (HU-1909).

Loads the grounded L1/L2/L3 distillation memory produced by the onboarding
pipeline (``huible.distillation.cli`` --strict) plus the structured persona
profile into the running Postgres/pgvector store, so the boot-time
``_hydrate_persona_registry`` hook (HU-1435) registers the persona and
``/api/v1/chat/{persona_id}`` serves retrieval-grounded replies.

Retrieval contract (Stage-1 fake-posture): the API query embedder is the
token-hash from ``huible.conversation.simple_embedding`` emitted at the
``memories.embedding_content`` schema dim (1536, HU-1909); stored
``embedding_content`` vectors MUST use the same function/dimension or
``PostgresMemoryBackend`` skips the vector search (HU-1435 dim guard).

Memory mapping (distillation tier → store tier):
  L1 facts      → accrued   (content_type=fact,  source_type=extraction)
  L2 scenarios  → derived   (content_type=narrative, source_type=inference)
  L3 profiles   → canonical (content_type=fact,  source_type=canonical_seed)

All memories land with ``disclosure_scope=close_friends`` (visible to the
default family-tier requester), ``memory_date=NULL`` (era gate passes), and
the frontmatter numeric ``confidence`` copied into row metadata so the
ContextBuilder provenance gate admits HIGH/MEDIUM only.

Usage:
  python scripts/provision_persona.py \
      --memory-dir /tmp/onboarding/chandler/memory \
      --persona-name "Chandler Bing" --display-name Chandler \
      --slug chandler-bing --era-boundary 2004-05-06 \
      --voice-profile /root/repos/personas/chandler-bing/02-clean/persona-profile.md \
      --corpus "friends-v2.csv" \
      --database-url postgresql://huible:***@127.0.0.1:5432/huible
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from huible.conversation import simple_embedding  # noqa: E402

QUERY_EMBEDDING_DIM = 1536  # memories.embedding_content schema dim (HU-1909)

PERSONA_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "huible:persona")


def _parse_frontmatter(path: Path) -> tuple[dict, str]:
    """Split an OKF markdown memory file into (frontmatter dict, body)."""
    raw = path.read_text(encoding="utf-8")
    if not raw.startswith("---"):
        return {}, raw.strip()
    parts = raw.split("---", 2)
    if len(parts) < 3:
        return {}, raw.strip()
    fm: dict[str, str] = {}
    for line in parts[1].strip().splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            fm[key.strip()] = value.strip()
    return fm, parts[2].strip()


def _confidence_tag(numeric: str | None) -> str | None:
    """Mirror the ContextBuilder numeric→categorical thresholds."""
    try:
        score = float(numeric) if numeric else None
    except ValueError:
        return None
    if score is None:
        return None
    if score >= 0.75:
        return "high"
    if score >= 0.50:
        return "medium"
    return "low"


def load_voice_instructions(profile_path: Path) -> str:
    """Extract the voice-bearing sections from the structured persona profile."""
    text = profile_path.read_text(encoding="utf-8")
    wanted = ("Communication Style", "Humor Type", "Core Traits", "Catchphrases")
    chunks: list[str] = []
    lines = text.splitlines()
    current: str | None = None
    buf: list[str] = []
    for line in lines:
        heading = re.match(r"^##\s+(.+)$", line)
        if heading:
            if current in wanted and buf:
                chunks.append(f"{current}: " + " ".join(x.strip("- ") for x in buf if x.strip()))
            current = heading.group(1).strip()
            buf = []
        elif current in wanted and line.strip():
            buf.append(line.strip())
    if current in wanted and buf:
        chunks.append(f"{current}: " + " ".join(x.strip("- ") for x in buf if x.strip()))
    return "\n".join(chunks)[:4000]


def collect_memories(memory_dir: Path) -> list[dict]:
    """Flatten the distillation store into insertable memory dicts."""
    out: list[dict] = []
    for fact_file in sorted((memory_dir / "facts").glob("*.md")):
        fm, body = _parse_frontmatter(fact_file)
        if not body:
            continue
        out.append(
            {
                "id": uuid.uuid5(PERSONA_NAMESPACE, f"l1:{fact_file.stem}"),
                "tier": "accrued",
                "content_type": "fact",
                "source_type": "extraction",
                "content": body[:4000],
                "confidence": fm.get("confidence"),
                "metadata": {
                    "okf_tier": "L1",
                    "memory_type": fm.get("memory_type"),
                    "subject": fm.get("subject"),
                    "predicate": fm.get("predicate"),
                    "object": fm.get("object"),
                    "evidence": fm.get("source"),
                },
            }
        )
    for scen_file in sorted((memory_dir / "scenarios").glob("*.md")):
        fm, body = _parse_frontmatter(scen_file)
        title = fm.get("scenario", "scenario")
        domain = fm.get("domain", "general")
        content = f"[{domain}] {title}:\n{body}"[:4000]
        out.append(
            {
                "id": uuid.uuid5(PERSONA_NAMESPACE, f"l2:{scen_file.stem}"),
                "tier": "derived",
                "content_type": "narrative",
                "source_type": "inference",
                "content": content,
                "confidence": fm.get("confidence"),
                "metadata": {
                    "okf_tier": "L2",
                    "domain": domain,
                    "scenario": title,
                    "evidence": fm.get("evidence_sources"),
                },
            }
        )
    for prof_file in sorted((memory_dir / "profiles").glob("*.md")):
        fm, body = _parse_frontmatter(prof_file)
        key = fm.get("key", "general")
        label = key.replace(":", " — ")
        content = f"{label}: {body}"[:4000] if body else label[:4000]
        out.append(
            {
                "id": uuid.uuid5(PERSONA_NAMESPACE, f"l3:{prof_file.stem}"),
                "tier": "canonical",
                "content_type": "fact",
                "source_type": "canonical_seed",
                "content": content,
                "confidence": fm.get("confidence"),
                "metadata": {
                    "okf_tier": "L3",
                    "memory_type": fm.get("memory_type"),
                    "key": key,
                    "evidence": fm.get("source"),
                },
            }
        )
    return out


async def provision(
    database_url: str,
    persona_id: uuid.UUID,
    persona_name: str,
    display_name: str,
    voice_instructions: str,
    era_boundary: str,
    persona_metadata: dict,
    memories: list[dict],
    batch_size: int = 500,
) -> dict:
    import asyncpg
    from pgvector.asyncpg import register_vector

    conn = await asyncpg.connect(database_url)
    try:
        await register_vector(conn)
        # Idempotent re-provision: clear prior rows for this persona.
        deleted = await conn.fetchval("DELETE FROM memories WHERE persona_id = $1", persona_id)
        await conn.execute("DELETE FROM personas WHERE id = $1", persona_id)
        await conn.execute(
            """
            INSERT INTO personas (id, name, display_name, voice_instructions,
                                  era_knowledge_boundary, metadata)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            persona_id,
            persona_name,
            display_name,
            voice_instructions,
            era_boundary,
            json.dumps(persona_metadata),
        )
        inserted = 0
        for start in range(0, len(memories), batch_size):
            batch = memories[start : start + batch_size]
            rows = [
                (
                    m["id"],
                    persona_id,
                    m["tier"],
                    m["content"],
                    m["content_type"],
                    simple_embedding(m["content"], dim=QUERY_EMBEDDING_DIM),
                    m["source_type"],
                    json.dumps({**(m["metadata"] or {}), "confidence": float(m["confidence"] or 0.6)}),
                )
                for m in batch
            ]
            await conn.executemany(
                """
                INSERT INTO memories (id, persona_id, tier, content, content_type,
                                      embedding_content, source_type, disclosure_scope,
                                      metadata, is_active)
                VALUES ($1, $2, $3, $4, $5, $6, $7, 'close_friends', $8, true)
                """,
                rows,
            )
            inserted += len(batch)
        counts = {
            "l1_facts": sum(1 for m in memories if m["metadata"]["okf_tier"] == "L1"),
            "l2_scenarios": sum(1 for m in memories if m["metadata"]["okf_tier"] == "L2"),
            "l3_profiles": sum(1 for m in memories if m["metadata"]["okf_tier"] == "L3"),
        }
        return {"persona_id": str(persona_id), "deleted_prior": deleted or 0, "inserted": inserted, **counts}
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Provision an onboarded persona into the live store")
    parser.add_argument("--memory-dir", required=True, type=Path, help="Distillation memory store (L1/L2/L3)")
    parser.add_argument("--persona-name", required=True)
    parser.add_argument("--display-name", required=True)
    parser.add_argument("--slug", required=True, help="Stable persona slug (deterministic UUID seed)")
    parser.add_argument("--era-boundary", required=True, help="ISO date walling the persona's universe")
    parser.add_argument("--voice-profile", required=True, type=Path, help="Structured persona-profile.md")
    parser.add_argument("--corpus", default="unknown", help="Source corpus label for metadata")
    parser.add_argument("--structure-model", default=None, help="LLM used by the structure stage")
    parser.add_argument(
        "--database-url",
        default=os.environ.get("PROVISION_DATABASE_URL", "postgresql://huible:huible@127.0.0.1:5432/huible"),
    )
    args = parser.parse_args()

    manifest_path = args.memory_dir / "distill-manifest.json"
    manifest: dict = {}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    memories = collect_memories(args.memory_dir)
    if not memories:
        raise SystemExit(f"[FATAL] no memories parsed from {args.memory_dir}")

    persona_id = uuid.uuid5(PERSONA_NAMESPACE, f"persona:{args.slug}")
    metadata = {
        "source": "huible-onboarding-pipeline",
        "vault": f"personas/{args.slug}/02-clean",
        "corpus": args.corpus,
        "distill": manifest.get("counts", {}),
        "structure_model": args.structure_model,
        "provisioned_at": datetime.now(UTC).isoformat(),
    }
    result = asyncio.run(
        provision(
            database_url=args.database_url,
            persona_id=persona_id,
            persona_name=args.persona_name,
            display_name=args.display_name,
            voice_instructions=load_voice_instructions(args.voice_profile),
            era_boundary=args.era_boundary,
            persona_metadata=metadata,
            memories=memories,
        )
    )
    print(json.dumps({"ok": True, **result}, indent=2))
    print(
        "::".join(
            [
                "",
                json.dumps({"outputs": result}),
                "",
            ]
        )
    )


if __name__ == "__main__":
    main()
