"""INV-CI: Canonical Immutability

Invariant: Ground-truth facts (canonical tier) must never be modified or
superseded by the ingestion pipeline. Canonical memories are seeded by
family or the system and represent immutable truth about the person.

The ImmutabilityGate checks for canonical conflicts during ingestion.
Without a Tier 2 model, any canonical match returns AMBIGUOUS (routing to
quarantine), which prevents the conflicting memory from being accepted.
This is the enforcement mechanism: canonical conflicts are never silently
accepted.
"""

from __future__ import annotations

from huible.ingestion import GateContext, GateOutcome
from huible.ingestion.gate_immutability import ImmutabilityGate
from huible.ingestion.pipeline import IngestionPipeline
from huible.memory.protocol import MemoryTier
from tests.invariants.conftest import PERSONA_ID, make_node

SAME_VEC = [0.5] * 1536


class TestInvCICannotSupersedeCanonical:
    """INV-CIa: Canonical memories must not be silently superseded."""

    async def test_supersede_preserves_canonical_content(self, backend):
        canonical = make_node(
            PERSONA_ID,
            content="Bob was born in Austin, Texas on March 20, 1950",
            tier=MemoryTier.CANONICAL,
            embedding_content=SAME_VEC,
        )
        await backend.store_memory(canonical)

        challenger = make_node(
            PERSONA_ID,
            content="Bob was born in Houston",
            tier=MemoryTier.ACCRUED,
            embedding_content=SAME_VEC,
        )

        new_id = await backend.supersede_memory(canonical.id, challenger)

        original = await backend.get_memory(canonical.id)
        assert original is not None
        assert original.content == "Bob was born in Austin, Texas on March 20, 1950"

        new_mem = await backend.get_memory(new_id)
        assert new_mem is not None
        assert new_mem.id != canonical.id


class TestInvCIImmutabilityGateFlagsConflicts:
    """INV-CIb: Immutability gate must flag canonical conflicts during ingestion."""

    async def test_gate_flags_conflict_with_canonical(self, backend):
        canonical = make_node(
            PERSONA_ID,
            content="Core fact about the person",
            tier=MemoryTier.CANONICAL,
            embedding_content=SAME_VEC,
        )
        await backend.store_memory(canonical)

        gate = ImmutabilityGate()
        ctx = GateContext(
            persona_id=PERSONA_ID,
            backend=backend,
        )

        candidate = {
            "content": "Conflicting fact about the person",
            "embedding_content": SAME_VEC,
        }

        result = await gate.evaluate(candidate, ctx)

        assert result.outcome != GateOutcome.PASS

    async def test_gate_ambiguous_when_tier2_unavailable(self, backend):
        """Without Tier 2, canonical matches must route to quarantine (AMBIGUOUS)."""
        canonical = make_node(
            PERSONA_ID,
            content="A canonical fact",
            tier=MemoryTier.CANONICAL,
            embedding_content=SAME_VEC,
        )
        await backend.store_memory(canonical)

        gate = ImmutabilityGate()
        ctx = GateContext(
            persona_id=PERSONA_ID,
            backend=backend,
        )

        candidate = {
            "content": "Something that might conflict",
            "embedding_content": SAME_VEC,
        }

        result = await gate.evaluate(candidate, ctx)

        assert result.outcome == GateOutcome.AMBIGUOUS

    async def test_gate_passes_when_no_canonical_memories(self, backend):
        gate = ImmutabilityGate()
        ctx = GateContext(
            persona_id=PERSONA_ID,
            backend=backend,
        )

        candidate = {
            "content": "A brand new memory about fishing",
            "embedding_content": [0.9] * 1536,
        }

        result = await gate.evaluate(candidate, ctx)
        assert result.outcome == GateOutcome.PASS


class TestInvCICanonicalPreservedThroughIngestion:
    """INV-CIc: Running the full pipeline with a conflicting candidate does not
    modify the original canonical memory."""

    async def test_pipeline_does_not_modify_canonical(self, backend):
        canonical = make_node(
            PERSONA_ID,
            content="Bob was born in Austin",
            tier=MemoryTier.CANONICAL,
            embedding_content=SAME_VEC,
        )
        await backend.store_memory(canonical)

        pipeline = IngestionPipeline()

        conflicting = {
            "id": "ci-test-conflict",
            "content": "Bob was actually born in Dallas",
            "content_type": "fact",
            "tier": "derived",
            "source_type": "extraction",
            "disclosure_scope": "family",
            "embedding_content": SAME_VEC,
        }

        result = await pipeline.process(conflicting, PERSONA_ID, backend=backend)

        assert result.accepted is False

        original = await backend.get_memory(canonical.id)
        assert original is not None
        assert original.content == "Bob was born in Austin"
        assert original.tier == MemoryTier.CANONICAL
        assert original.is_active is True

    async def test_conflicting_memory_quarantined_not_accepted(self, backend):
        canonical = make_node(
            PERSONA_ID,
            content="Ground truth fact",
            tier=MemoryTier.CANONICAL,
            embedding_content=[0.5] * 1536,
        )
        await backend.store_memory(canonical)

        pipeline = IngestionPipeline()

        conflicting = {
            "id": "ci-quarantine-test",
            "content": "Conflicting ground truth about the same topic",
            "content_type": "fact",
            "tier": "derived",
            "source_type": "extraction",
            "disclosure_scope": "family",
            "embedding_content": [0.4] * 1536,
        }

        result = await pipeline.process(conflicting, PERSONA_ID, backend=backend)

        assert result.accepted is False

