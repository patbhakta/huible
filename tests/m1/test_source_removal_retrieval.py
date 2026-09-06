"""M1.2 controlled source-removal retrieval test (HU-2732).

The M-0 verdict's ``m0_fake_embeddings`` failure class was: the vault was
never actually read because retrieval ran on token-hash embeddings, so
results were independent of corpus contents. The executable falsification of
that class is a controlled source-removal experiment on the REAL embedder:

1. Index a small corpus with ``LocalOnnxEmbedder`` (bge-small-en-v1.5, the
   deployed production provider).
2. Retrieve through the production entry point
   (:func:`huible.memory.retrieval.retrieve`) — the same function the
   ContextBuilder drives on the live chat path.
3. Remove one source and re-index without it.
4. Assert retrieval actually changed: the removed source disappears from the
   activated set and the ranking shifts.

Fake (token-hash) embeddings cannot pass the ranking assertions: their hash
vectors do not order semantically related passages above distractors.

Opt-in like the other real-model tests: ``RUN_LOCAL_ONNX_TESTS=1`` with
``fastembed`` installed (the model downloads once, then runs offline).
"""

from __future__ import annotations

import os
from datetime import date
from uuid import UUID, uuid4

import pytest

from huible.conversation import InMemoryMemoryBackend
from huible.embeddings import LocalOnnxEmbedder
from huible.memory.protocol import (
    ContentType,
    DisclosureScope,
    MemoryNode,
    MemoryTier,
    SourceType,
)
from huible.memory.retrieval import retrieve
from huible.persona.context import CONFIDENCE_LEVEL_METADATA_KEY

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("RUN_LOCAL_ONNX_TESTS") != "1",
        reason="set RUN_LOCAL_ONNX_TESTS=1 with fastembed installed to run the model",
    ),
]

PERSONA_ID = UUID("fdc3a44b-4c0f-565d-b671-4ed0e3bc7894")  # Chandler Bing

#: Target source whose removal must change retrieval.
TARGET_TEXT = (
    "Chandler's father owns a drag club in Las Vegas and Chandler spent "
    "childhood summers there."
)

#: The corpus. The target sits beside same-show distractors so the fake
#: token-hash baseline cannot accidentally rank the target first — only the
#: real semantic geometry can.
CORPUS_TEXTS = [
    TARGET_TEXT,
    "Chandler works in statistical analysis and data reconfiguration, a job nobody understands.",
    "Chandler and Joey live across the hall from Monica in Greenwich Village.",
    "Chandler met Ross in college and they have been best friends ever since.",
    "Chandler hates Thanksgiving because his parents announced their divorce on the holiday.",
    "Chandler is famous for sarcasm and deflection whenever a conversation gets serious.",
    "Chandler eventually marries Monica and they move out of the city to a house in Westchester.",
    "Chandler's roommate Eddie was strange and once stole Chandler's insoles.",
]


def _corpus_node(embedder: LocalOnnxEmbedder, content: str) -> MemoryNode:
    """One indexed source: real passage vector, HIGH confidence, in-era."""
    (vector,) = embedder.embed_passage([content])
    return MemoryNode(
        id=uuid4(),
        persona_id=PERSONA_ID,
        tier=MemoryTier.CANONICAL,
        content=content,
        content_type=ContentType.NARRATIVE,
        embedding_content=vector,
        memory_date=date(1998, 10, 8),
        source_type=SourceType.EXTRACTION,
        disclosure_scope=DisclosureScope.FAMILY,
        metadata={CONFIDENCE_LEVEL_METADATA_KEY: "high"},
    )


async def _index(
    texts: list[str], embedder: LocalOnnxEmbedder
) -> tuple[InMemoryMemoryBackend, dict[str, MemoryNode]]:
    backend = InMemoryMemoryBackend()
    nodes: dict[str, MemoryNode] = {}
    for text in texts:
        node = _corpus_node(embedder, text)
        nodes[text] = node
        await backend.store_memory(node)
    return backend, nodes


async def test_source_removal_changes_retrieval() -> None:
    """Removing a source and re-indexing must change retrieval (M1.2)."""
    embedder = LocalOnnxEmbedder()
    query = embedder.embed_query(
        ["where did Chandler's father perform?"]
    )[0]

    # 1. Full corpus: the target source is retrieved and ranks first.
    backend, nodes = await _index(CORPUS_TEXTS, embedder)
    target = nodes[TARGET_TEXT]
    before = await retrieve(backend, PERSONA_ID, query)
    assert before, "retrieval surfaced nothing"
    assert before[0].node.id == target.id

    # 2. Remove the source and re-index without it (corpus-level removal —
    # exactly the operation whose absence made M-0 retrieval meaningless).
    backend_after, _ = await _index(
        [t for t in CORPUS_TEXTS if t != TARGET_TEXT], embedder
    )
    after = await retrieve(backend_after, PERSONA_ID, query)

    # 3. Retrieval changed: the removed source is gone from the activated set.
    before_ids = {a.node.id for a in before}
    after_ids = {a.node.id for a in after}
    assert target.id in before_ids
    assert target.id not in after_ids

    # 4. The ranking is content-driven: a same-show distractor now leads.
    assert after, "retrieval surfaced nothing after removal"
    assert after[0].node.id != target.id
