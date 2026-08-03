from __future__ import annotations

import hashlib
import math
from datetime import date
from uuid import uuid4

from huible.memory.protocol import (
    ContentType,
    DisclosureScope,
    MemoryNode,
    MemoryTier,
)


def _make_node(persona_id, idx, emb_dim=128):
    h = hashlib.sha512(f"node-{idx}".encode()).digest()
    emb = []
    for i in range(emb_dim):
        chunk = h[i % len(h) : i % len(h) + 4]
        val = int.from_bytes(chunk, "big") / 0xFFFFFFFF
        emb.append(val * 2.0 - 1.0)
    norm = math.sqrt(sum(x * x for x in emb))
    emb = [x / norm for x in emb]
    return MemoryNode(
        id=uuid4(),
        persona_id=persona_id,
        tier=MemoryTier.ACCRUED,
        content=f"memory {idx}",
        content_type=list(ContentType)[idx % 5],
        embedding_content=emb,
        embedding_sensory=emb[:],
        embedding_affect=emb[:64],
        memory_date=date(2000, 1, 1),
        disclosure_scope=list(DisclosureScope)[idx % 4],
        metadata={"topic": f"topic_{idx % 20}"},
    )
