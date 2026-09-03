"""W3 competence-wall tests — description-free prompt + deflection exemplars.

Covers HU-2309 v1.8 §1.7.2 W3 acceptance:

- The ``voice_instructions`` adjective sheet never reaches the rendered prompt
  (system prompt *or* flat render) even when populated.
- An out-of-domain turn (empty admissible retrieval) retrieves the persona's
  deflection-pattern exemplars and renders them as the VOICE EXEMPLARS section.
- Exemplar candidates pass the same hard gates as the prompt firewall
  (confidence fail-closed, disclosure scope, era boundary) and the activation
  floor.
- In-domain turns never fire the wall; no probe embedding disables it; the
  per-persona exemplar cache serves subsequent turns without a second search.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID, uuid4

from huible.memory.protocol import (
    ContentType,
    DisclosureScope,
    MemoryNode,
    MemoryTier,
    SearchResult,
    SourceType,
)
from huible.memory.retrieval import ActivatedMemory, RetrievalConfig
from huible.persona.context import (
    CONFIDENCE_LEVEL_METADATA_KEY,
    DEFLECTION_PROBE_TEXT,
    ConfidenceLevel,
    ContextBuilder,
    PersonaConfig,
    RelationshipTier,
)

PERSONA_ID = uuid4()
PROBE = [0.42, 0.17]

_DEFLECTION_LINES = [
    "general — is: could that BE any more boring? moving on.",
    "general — is: yeah, I don't know, ask somebody who cares.",
]


def _node(
    *,
    content: str = "general — is: could that BE any more boring?",
    content_type: ContentType = ContentType.FACT,
    disclosure_scope: DisclosureScope = DisclosureScope.FAMILY,
    memory_date: date | None = None,
    confidence_level: ConfidenceLevel | None = ConfidenceLevel.MEDIUM,
    persona_id: UUID = PERSONA_ID,
) -> MemoryNode:
    metadata: dict[str, Any] = {}
    if confidence_level is not None:
        metadata[CONFIDENCE_LEVEL_METADATA_KEY] = confidence_level.value
    return MemoryNode(
        id=uuid4(),
        persona_id=persona_id,
        tier=MemoryTier.ACCRUED,
        content=content,
        content_type=content_type,
        embedding_content=[0.5],
        memory_date=memory_date,
        source_type=SourceType.EXTRACTION,
        disclosure_scope=disclosure_scope,
        metadata=metadata,
    )


class _WallBackend:
    """Fake backend discriminating the turn lane from the probe lane.

    ``search_by_content`` with the PROBE embedding is the W3 deflection
    exemplar lane: it serves the scripted seed list and counts its calls so
    the cache behavior is observable. Any other embedding is the normal turn
    lane (``turn_seeds``, empty = out-of-domain turn).
    """

    def __init__(
        self,
        seeds: list[SearchResult] | None = None,
        turn_seeds: list[SearchResult] | None = None,
    ) -> None:
        self.seeds = seeds if seeds is not None else []
        self.turn_seeds = turn_seeds if turn_seeds is not None else []
        self.probe_calls = 0
        self._known: dict[UUID, MemoryNode] = {
            sr.node.id: sr.node for sr in (*self.seeds, *self.turn_seeds)
        }

    async def search_by_content(
        self,
        persona_id: Any,
        query_embedding: list[float],
        top_k: int = 20,
        disclosure_scope: DisclosureScope | None = None,
    ) -> list[SearchResult]:
        if query_embedding == PROBE:
            self.probe_calls += 1
            return self.seeds[:top_k]
        return self.turn_seeds[:top_k]

    async def get_memory(self, memory_id: Any) -> MemoryNode | None:
        return self._known.get(memory_id)

    async def search_by_sensory(
        self,
        persona_id: Any,
        query_embedding: list[float],
        top_k: int = 20,
        disclosure_scope: DisclosureScope | None = None,
    ) -> list[SearchResult]:
        return []

    async def search_by_affect(
        self,
        persona_id: Any,
        query_embedding: list[float],
        top_k: int = 20,
        disclosure_scope: DisclosureScope | None = None,
    ) -> list[SearchResult]:
        return []

    async def get_edges(self, memory_id: Any) -> list:
        return []


def _persona() -> PersonaConfig:
    return PersonaConfig(
        id=PERSONA_ID,
        name="Chandler",
        voice_instructions=(
            "Communication Style: Uses humor and sarcasm as a defense "
            "mechanism. Humor Type: Sarcastic, self-deprecating."
        ),
        era_knowledge_boundary="2004-05-06",
    )


def _seed(node: MemoryNode, score: float = 0.61) -> SearchResult:
    return SearchResult(node=node, score=score)


# ---------------------------------------------------------------------------
# Description-free prompt
# ---------------------------------------------------------------------------


class TestDescriptionFreePrompt:
    async def test_adjective_sheet_never_reaches_prompt(self):
        """W3: the RC-1 voice sheet is deleted from the render path."""
        persona = _persona()
        assert persona.voice_instructions  # fixture really carries the sheet
        ctx = await ContextBuilder().build(
            persona=persona,
            requester_tier=RelationshipTier.FAMILY,
            backend=_WallBackend([]),
            query_embedding_content=[0.1],
        )
        assert persona.voice_instructions not in ctx.system_prompt
        assert "Voice & style" not in ctx.render()
        assert "defense mechanism" not in ctx.render()

    async def test_probe_text_is_not_prompt_text(self):
        """The probe is a retrieval key; it must never appear in a prompt."""
        persona = _persona()
        ctx = await ContextBuilder().build(
            persona=persona,
            requester_tier=RelationshipTier.FAMILY,
            backend=_WallBackend([]),
            query_embedding_content=[0.1],
            deflection_probe_embedding=PROBE,
        )
        rendered = ctx.render()
        assert DEFLECTION_PROBE_TEXT not in rendered
        assert "deflecting a question" not in rendered


# ---------------------------------------------------------------------------
# Competence wall
# ---------------------------------------------------------------------------


class TestCompetenceWall:
    async def test_out_of_domain_turn_renders_deflection_exemplars(self):
        backend = _WallBackend([_seed(_node(content=line)) for line in _DEFLECTION_LINES])
        ctx = await ContextBuilder().build(
            persona=_persona(),
            requester_tier=RelationshipTier.FAMILY,
            backend=backend,
            query_embedding_content=[0.1],
            deflection_probe_embedding=PROBE,
        )
        assert ctx.included_memories == []
        assert ctx.competence_wall_fired
        rendered = ctx.render()
        assert "VOICE EXEMPLARS" in rendered
        assert "[EXEMPLAR] could that BE any more boring? moving on." in rendered
        # Atom relation prefix stripped; raw corpus label never rendered.
        assert "general — is:" not in rendered

    async def test_exemplars_pass_hard_gates(self):
        """Low-confidence / out-of-scope exemplar candidates are dropped."""
        bad_conf = _node(
            content="general — is: no confidence line",
            confidence_level=ConfidenceLevel.QUARANTINE,
        )
        missing_conf = _node(content="general — is: no tag line", confidence_level=None)
        private = _node(
            content="general — is: private line",
            disclosure_scope=DisclosureScope.PRIVATE,
        )
        out_of_era = _node(content="general — is: future line", memory_date=date(2024, 1, 1))
        good = _node(content="general — is: good deflection line")
        backend = _WallBackend(
            [_seed(n) for n in (bad_conf, missing_conf, private, out_of_era, good)]
        )
        ctx = await ContextBuilder().build(
            persona=_persona(),
            requester_tier=RelationshipTier.FAMILY,
            backend=backend,
            query_embedding_content=[0.1],
            deflection_probe_embedding=PROBE,
        )
        assert len(ctx.deflection_exemplars) == 1
        assert ctx.deflection_exemplars[0].id == good.id

    async def test_below_floor_seeds_are_skipped(self):
        backend = _WallBackend([_seed(_node(content="general — is: too weak"), score=0.1)])
        ctx = await ContextBuilder().build(
            persona=_persona(),
            requester_tier=RelationshipTier.FAMILY,
            backend=backend,
            query_embedding_content=[0.1],
            deflection_probe_embedding=PROBE,
            retrieval_config=RetrievalConfig(activation_threshold=0.3),
        )
        assert ctx.deflection_exemplars == []
        assert not ctx.competence_wall_fired

    async def test_in_domain_turn_never_fires_wall(self):
        """A turn with an admissible memory is served by retrieval alone."""
        memory = _node(content="general — is: in-domain line")
        backend = _WallBackend(turn_seeds=[SearchResult(node=memory, score=0.8)])
        ctx = await ContextBuilder().build(
            persona=_persona(),
            requester_tier=RelationshipTier.FAMILY,
            backend=backend,
            query_embedding_content=[0.1],
            deflection_probe_embedding=PROBE,
            current_message="hey, remember the apartment?",
        )
        assert len(ctx.included_memories) == 1
        assert not ctx.competence_wall_fired
        assert ctx.deflection_exemplars == []
        assert "VOICE EXEMPLARS" not in ctx.render()

    async def test_no_probe_embedding_disables_wall(self):
        backend = _WallBackend([_seed(_node())])
        ctx = await ContextBuilder().build(
            persona=_persona(),
            requester_tier=RelationshipTier.FAMILY,
            backend=backend,
            query_embedding_content=[0.1],
        )
        assert backend.probe_calls == 0
        assert not ctx.competence_wall_fired

    async def test_exemplar_cache_serves_second_turn(self):
        backend = _WallBackend([_seed(_node())])
        builder = ContextBuilder()
        for _ in range(2):
            ctx = await builder.build(
                persona=_persona(),
                requester_tier=RelationshipTier.FAMILY,
                backend=backend,
                query_embedding_content=[0.1],
                deflection_probe_embedding=PROBE,
            )
            assert ctx.competence_wall_fired
        assert backend.probe_calls == 1

    async def test_wall_with_gated_out_turn_still_fires(self):
        """Retrieved-but-all-gated turns count as out-of-domain."""
        gated = _node(confidence_level=ConfidenceLevel.LOW)
        backend = _WallBackend([_seed(_node())], turn_seeds=[_seed(gated, score=0.9)])
        ctx = await ContextBuilder().build(
            persona=_persona(),
            requester_tier=RelationshipTier.FAMILY,
            backend=backend,
            query_embedding_content=[0.1],
            deflection_probe_embedding=PROBE,
        )
        assert ctx.included_memories == []
        assert ctx.exclusion_counts.get("confidence_low") == 1
        assert ctx.competence_wall_fired


# ---------------------------------------------------------------------------
# filter_and_render surface (sync render path)
# ---------------------------------------------------------------------------


class TestFilterAndRenderExemplars:
    def test_exemplars_render_without_activation(self):
        exemplars = [_node(content="general — is: joke it off")]
        ctx = ContextBuilder().filter_and_render(
            [],
            _persona(),
            RelationshipTier.FAMILY,
            deflection_exemplars=exemplars,
        )
        assert ctx.competence_wall_fired
        assert "[EXEMPLAR] joke it off" in ctx.render()
        assert ctx.included_memories == []

    def test_activated_memory_untouched_by_exemplar_flag(self):
        am = ActivatedMemory(node=_node(content="general — is: real memory"), activation=0.9)
        exemplars = [_node()]
        ctx = ContextBuilder().filter_and_render(
            [am],
            _persona(),
            RelationshipTier.FAMILY,
            deflection_exemplars=exemplars,
        )
        assert len(ctx.included_memories) == 1
        assert "[FACT] general — is: real memory" in ctx.memory_blocks
        assert ctx.competence_wall_fired
