#!/usr/bin/env python3
"""W1 one-window cutover: 1536 → 384 dims + full memory re-embed (HU-2309 v1.8 M-0R-A).

Runs the whole W1 data migration against the live store in a single deploy
window (no mixed-dim state):

  1. Verify ``EMBEDDING_PROVIDER=local_onnx`` (refuses to run otherwise —
     re-embedding with the legacy token-hash lane would rebuild the exact
     vectors we are migrating away from).
  2. Run ``alembic upgrade head`` (migration ``007_w1_vector_384`` cuts both
     columns to ``vector(384)``; the ``USING NULL`` cast drops the derived
     token-hash vectors — content is preserved and re-embedded in step 3).
  3. Batch re-embed every active memory from its content:
     ``embedding_content`` ← passage embed of ``content``,
     ``embedding_sensory`` ← passage embed of ``sensory`` metadata when present.
  4. Verify: row counts, a dimension probe on sampled rows, and the null-rate
     for memories that should have been embedded.

Retrieval degrades to persona-voice-only (valid Class-B state, CA C3) while
the window is open; run this during a quiet period. Idempotent per batch:
re-running re-embeds only rows with NULL vectors once the columns are 384-dim
(step 3 filters ``embedding_content IS NULL`` unless ``--force``).

Usage (on the app host, with the app stopped or traffic paused):
  python scripts/migrate_embeddings_384.py            # full window
  python scripts/migrate_embeddings_384.py --dry-run  # steps 1-2 checks only

Requires DATABASE_URL (asyncpg URL) and the fastembed model cache (first run
downloads bge-small-en-v1.5 once, then fully offline).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import NoReturn

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

BATCH_SIZE = 256


def _fail(msg: str) -> NoReturn:
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


async def _count_active(conn) -> int:
    return await conn.fetchval("SELECT count(*) FROM memories WHERE is_active")


async def _column_dims(conn) -> dict[str, int | None]:
    rows = await conn.fetch(
        """
        SELECT attname, format_type(a.atttypid, a.atttypmod) AS coltype
        FROM pg_attribute a
        WHERE a.attrelid = 'memories'::regclass AND a.attname IN
              ('embedding_content', 'embedding_sensory') AND NOT a.attisdropped
        """
    )
    dims: dict[str, int | None] = {}
    for r in rows:
        coltype = r["coltype"]
        dims[r["attname"]] = int(coltype.rsplit("(", 1)[1].rstrip(")")) if "(" in coltype else None
    return dims


async def _reembed_batch(conn, embedder) -> int:
    rows = await conn.fetch(
        """
        SELECT id, content, metadata
        FROM memories
        WHERE is_active AND embedding_content IS NULL
        LIMIT $1
        """,
        BATCH_SIZE,
    )
    if not rows:
        return 0
    vectors = embedder.embed_passage([r["content"] for r in rows])
    updates = []
    for r, vec in zip(rows, vectors, strict=True):
        meta = r["metadata"] or {}
        sensory = meta.get("sensory")
        sensory_vec = None
        if isinstance(sensory, str) and sensory.strip():
            sensory_vec = embedder.embed_passage([sensory])[0]
        updates.append((list(vec), sensory_vec, r["id"]))
    await conn.executemany(
        """
        UPDATE memories
        SET embedding_content = $1,
            embedding_sensory = COALESCE($2::vector, embedding_sensory)
        WHERE id = $3
        """,
        updates,
    )
    return len(updates)


async def _verify(conn, embedder) -> None:
    total = await _count_active(conn)
    nulls = await conn.fetchval(
        "SELECT count(*) FROM memories WHERE is_active AND embedding_content IS NULL"
    )
    sample = await conn.fetch(
        "SELECT id, embedding_content IS NOT NULL AS has_vec FROM memories "
        "WHERE is_active AND embedding_content IS NOT NULL LIMIT 5"
    )
    probe = await conn.fetchval(
        "SELECT vector_dims(embedding_content) FROM memories "
        "WHERE is_active AND embedding_content IS NOT NULL LIMIT 1"
    )
    print(
        f"verify: active={total} remaining_null={nulls} "
        f"sample_rows={len(sample)} dim_probe={probe}"
    )
    if probe is not None and probe != embedder.dim:
        _fail(f"stored dim {probe} != embedder dim {embedder.dim}")
    if total and nulls == total:
        _fail("no vectors were written")


async def _clear_vectors(conn) -> int:
    return await conn.execute("UPDATE memories SET embedding_content = NULL WHERE is_active")


async def main_async(dry_run: bool, force: bool) -> None:
    from huible.api.settings import get_settings
    from huible.embeddings import build_embedder

    settings = get_settings()
    if settings.embedding_provider != "local_onnx":
        _fail(
            "EMBEDDING_PROVIDER must be 'local_onnx' for the W1 cutover "
            f"(got {settings.embedding_provider!r}); refusing to re-embed with "
            "the legacy token-hash lane."
        )

    database_url = settings.effective_database_url or os.environ.get("DATABASE_URL", "")
    if not database_url:
        _fail("DATABASE_URL is not set")
    # asyncpg requires postgresql:// (not postgres://)
    if database_url.startswith("postgres://"):
        database_url = "postgresql://" + database_url[len("postgres://"):]

    import asyncpg

    conn = await asyncpg.connect(dsn=database_url)
    try:
        dims = await _column_dims(conn)
        print(f"column dims before: {dims}")
        if any(d != 384 for d in dims.values()):
            print("running: alembic upgrade head (007_w1_vector_384 cuts columns to 384)")
            if dry_run:
                print("dry-run: stopping before alembic")
                return
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "alembic", "upgrade", "head",
                env={**os.environ},
            )
            if await proc.wait() != 0:
                _fail("alembic upgrade head failed")
            dims = await _column_dims(conn)
            print(f"column dims after migration: {dims}")
            if any(d != 384 for d in dims.values()):
                _fail(f"columns not 384 after migration: {dims}")
        else:
            print("columns already 384; skipping alembic")

        embedder = build_embedder(settings.embedding_provider, settings.embeddings_model)
        print(f"embedder: {settings.embeddings_model} dim={embedder.dim}")

        total = await _count_active(conn)
        print(f"active memories to consider: {total}")

        done = 0
        if force:
            cleared = await _clear_vectors(conn)
            print(f"force: cleared vectors for re-embed ({cleared})")
        while True:
            n = await _reembed_batch(conn, embedder)
            if n == 0:
                break
            done += n
            print(f"  re-embedded {done}")

        await _verify(conn, embedder)
        print("W1 cutover complete.")
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="checks only, no writes")
    parser.add_argument(
        "--force", action="store_true",
        help="re-embed rows that already have vectors (default: NULL rows only)",
    )
    args = parser.parse_args()
    if args.force:
        # Force mode re-embeds everything; implemented by clearing vectors first
        # in the caller's window. Kept explicit to avoid accidental full rewrites.
        print("force: existing vectors will be cleared then re-embedded")
    asyncio.run(main_async(args.dry_run, args.force))


if __name__ == "__main__":
    main()
