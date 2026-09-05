"""Regression tests for the retrieval activation floor (HU-2673 C3 / HU-2707).

The floor is the anti-filler inclusion gate at the end of the
spreading-activation path (``retrieval.py`` inclusion ``>=`` gate) and doubles
as the W2 lexical lane's entry score (``lexical_floor``). C3 re-derived the
class-A value on the 384-dim bge-small corpus: **0.50** — inside the widest
structural gap between the filler band (≤ 0.390) and the weakest genuine
vector inclusion (0.577), and above the embedder's irrelevance baseline
(~0.51) where the legacy 0.3 default sat.

Covered here (config landing spec, derivation doc §Config landing spec):

- the floor is respected at inclusion (parameterized, boundary semantics);
- the lexical lane enters *exactly at* the floor and ranks below real
  vector matches;
- empty retrieval stays a first-class state at a high floor (B2 path —
  no filler injection, no crash);
- the per-persona Class B override
  (``persona.metadata["retrieval_activation_floor"]``) wins over the
  settings-threaded default, and an invalid override is rejected
  (never clamped) in favor of the safe default;
- ``Settings.retrieval_activation_floor`` (default 0.50, [0.05, 0.95]
  band) is threaded into the default ``ContextBuilder`` at ``create_app``.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from huible.api.app import create_app
from huible.api.settings import Settings
from huible.memory.protocol import (
    ContentType,
    DisclosureScope,
    MemoryEdge,
    MemoryNode,
    MemoryTier,
    SearchResult,
    SourceType,
)
from huible.memory.retrieval import RetrievalConfig, retrieve
from huible.persona.context import (
    ACTIVATION_FLOOR_MAX,
    ACTIVATION_FLOOR_MIN,
    CONFIDENCE_LEVEL_METADATA_KEY,
    RETRIEVAL_ACTIVATION_FLOOR_KEY,
    ConfidenceLevel,
    ContextBuilder,
    PersonaConfig,
    RelationshipTier,
    is_valid_activation_floor,
    resolve_activation_floor,
)

PERSONA_ID = uuid4()

#: The derived class-A floor (HU-2673): must remain the shipped default.
DERIVED_CLASS_A_FLOOR = 0.50


def _node(
    content: str,
    *,
    confidence: ConfidenceLevel = ConfidenceLevel.HIGH,
    disclosure_scope: DisclosureScope = DisclosureScope.FAMILY,
) -> MemoryNode:
    return MemoryNode(
        id=uuid4(),
        persona_id=PERSONA_ID,
        tier=MemoryTier.ACCRUED,
        content=content,
        content_type=ContentType.NARRATIVE,
        embedding_content=[0.5],
        memory_date=None,
        source_type=SourceType.EXTRACTION,
        disclosure_scope=disclosure_scope,
        metadata={CONFIDENCE_LEVEL_METADATA_KEY: confidence.value},
    )


def _persona(metadata: dict[str, Any] | None = None) -> PersonaConfig:
    return PersonaConfig(
        id=PERSONA_ID,
        name="Bob",
        era_knowledge_boundary="2020-06-15",
        metadata=metadata or {},
    )


class _ScriptedBackend:
    """Backend with scripted per-node scores for the vector and lexical lanes.

    Vector lane returns exactly the scripted (node, score) pairs; the lexical
    lane (``search_lexical``) returns its own scripted pairs. Deterministic —
    no cosine math — so floor semantics can be asserted on exact values.
    """

    def __init__(self) -> None:
        self._memories: dict[UUID, MemoryNode] = {}
        self._content: list[tuple[MemoryNode, float]] = []
        self._lexical: list[tuple[MemoryNode, float]] = []
        self.lexical_queried = False

    def add_memory(self, node: MemoryNode, content_score: float | None = None) -> MemoryNode:
        self._memories[node.id] = node
        if content_score is not None:
            self._content.append((node, content_score))
        return node

    def index_lexical(self, node: MemoryNode, score: float = 1.0) -> None:
        self._memories[node.id] = node
        self._lexical.append((node, score))

    async def store_memory(self, node: MemoryNode) -> UUID:
        return self.add_memory(node).id

    async def get_memory(self, memory_id: UUID) -> MemoryNode | None:
        return self._memories.get(memory_id)

    async def search_by_content(
        self,
        persona_id: UUID,
        query_embedding: list[float],
        top_k: int = 20,
        disclosure_scope: DisclosureScope | None = None,
    ) -> list[SearchResult]:
        return [
            SearchResult(node=node, score=score)
            for node, score in self._content[:top_k]
        ]

    async def search_by_sensory(self, *args: Any, **kwargs: Any) -> list[SearchResult]:
        return []

    async def search_by_affect(self, *args: Any, **kwargs: Any) -> list[SearchResult]:
        return []

    async def get_edges(self, memory_id: UUID) -> list[MemoryEdge]:
        return []

    async def get_active_memories(
        self, persona_id: UUID, limit: int = 50
    ) -> list[MemoryNode]:
        return list(self._memories.values())[:limit]

    async def search_lexical(
        self,
        persona_id: UUID,
        query: str,
        top_k: int = 20,
        disclosure_scope: DisclosureScope | None = None,
    ) -> list[SearchResult]:
        self.lexical_queried = True
        return [
            SearchResult(node=node, score=score) for node, score in self._lexical[:top_k]
        ]


# ---------------------------------------------------------------------------
# Floor respected at inclusion (parameterized)
# ---------------------------------------------------------------------------


class TestFloorRespectedAtInclusion:
    @staticmethod
    async def _run(floor: float, scores: list[float]) -> list[tuple[str, float]]:
        backend = _ScriptedBackend()
        for i, score in enumerate(scores):
            backend.add_memory(_node(f"memory at {score}"), content_score=score)
        config = RetrievalConfig(
            activation_threshold=floor,
            motif_threshold=99,  # keep scripted scores exact (no ×1.3 boost)
        )
        results = await retrieve(
            backend,
            PERSONA_ID,
            [0.5],
            config=config,
            disclosure_tier=DisclosureScope.FAMILY,
        )
        return [(r.node.content, r.activation) for r in results]

    @pytest.mark.parametrize(
        ("floor", "included_min", "excluded_max"),
        [
            (0.50, 0.577, 0.390),  # the derived class-A pair: filler out, genuine in
            (0.40, 0.577, 0.390),  # legacy-ish floor keeps both
            (0.60, 0.660, 0.577),  # higher floor drops the OOD tail
            (0.95, 1.082, 0.949),  # extreme floor keeps only the near-1.0 band
        ],
    )
    async def test_floor_gates_inclusion(
        self,
        floor: float,
        included_min: float | None,
        excluded_max: float,
    ) -> None:
        scores = [0.390, 0.577, 0.660, 0.949, 1.082]
        included = await self._run(floor, scores)
        got = {round(act, 3): content for content, act in included}
        if included_min is None:
            assert got == {}
        else:
            assert round(included_min, 3) in got
        assert round(excluded_max, 3) not in got

    async def test_boundary_score_exactly_at_floor_is_included(self):
        # W2 lexical doctrine rides this: entry floor == inclusion floor with
        # ``>=`` semantics — an at-floor seed must not be dropped by rounding.
        included = await self._run(0.50, [0.50])
        assert [act for _, act in included] == pytest.approx([0.50])

    async def test_below_floor_excluded(self):
        included = await self._run(0.50, [0.49])
        assert included == []


# ---------------------------------------------------------------------------
# Lexical lane: enters exactly at floor, ranks below vector matches
# ---------------------------------------------------------------------------


class TestLexicalLaneEntersAtFloor:
    async def test_lexical_only_seed_enters_exactly_at_floor(self):
        vec = _node("vector match")
        lexical = _node("My full name is Chandler Muriel Bing.")
        backend = _ScriptedBackend()
        backend.add_memory(vec, content_score=0.9)
        backend.index_lexical(lexical)

        results = await retrieve(
            backend,
            PERSONA_ID,
            [0.5],
            config=RetrievalConfig(activation_threshold=DERIVED_CLASS_A_FLOOR),
            query_text="What is your last name?",
        )
        assert backend.lexical_queried
        by_content = {r.node.content: r.activation for r in results}
        # Enters AT the derived floor — proper-noun recall survives the flip.
        assert by_content[lexical.content] == pytest.approx(DERIVED_CLASS_A_FLOOR)
        assert by_content[vec.content] == pytest.approx(0.9)

    async def test_lexical_seed_ranks_below_vector_matches(self):
        vec_high, vec_low = _node("strong vector match"), _node("weak vector match")
        lexical = _node("exact-topic proper noun")
        backend = _ScriptedBackend()
        backend.add_memory(vec_high, content_score=1.1)
        backend.add_memory(vec_low, content_score=0.55)
        backend.index_lexical(lexical)

        results = await retrieve(
            backend,
            PERSONA_ID,
            [0.5],
            config=RetrievalConfig(
                activation_threshold=DERIVED_CLASS_A_FLOOR,
                motif_threshold=99,  # keep scripted scores exact (no ×1.3 boost)
            ),
            query_text="surname?",
        )
        order = [r.node.content for r in results]
        assert order == [vec_high.content, vec_low.content, lexical.content]
        assert results[-1].activation == pytest.approx(DERIVED_CLASS_A_FLOOR)

    async def test_personal_override_lifts_lexical_entry_too(self):
        lexical = _node("exact-topic proper noun")
        backend = _ScriptedBackend()
        backend.index_lexical(lexical)

        results = await retrieve(
            backend,
            PERSONA_ID,
            [0.5],
            config=RetrievalConfig(activation_threshold=0.8),
            query_text="surname?",
        )
        # The lane rides the floor wherever it is set — entry stays exactly
        # at the floor, so the Class B override lifts the lane consistently.
        assert [r.activation for r in results] == pytest.approx([0.8])


# ---------------------------------------------------------------------------
# Empty retrieval is a first-class state at a high floor (B2 path)
# ---------------------------------------------------------------------------


class TestEmptyRetrievalB2AtHighFloor:
    async def test_high_floor_yields_empty_injection_no_filler(self):
        backend = _ScriptedBackend()
        backend.add_memory(_node("weak 0.6 match"), content_score=0.6)
        builder = ContextBuilder(
            RetrievalConfig(activation_threshold=0.9, motif_threshold=99)
        )
        ctx = await builder.build(
            persona=_persona(),
            requester_tier=RelationshipTier.FAMILY,
            backend=backend,
            query_embedding_content=[0.5],
            current_message="remember those days at work?",
            retrieval_config=RetrievalConfig(
                activation_threshold=0.9, motif_threshold=99
            ),
        )
        # B2: empty retrieval is the correct outcome — nothing injected, no
        # filler invented, no error.
        assert ctx.included_memories == []
        assert ctx.memory_blocks == ""

    async def test_same_corpus_served_at_default_floor(self):
        # Control for the test above: the identical turn IS served once the
        # floor comes back down — the empty turn is the floor's doing.
        backend = _ScriptedBackend()
        backend.add_memory(_node("weak 0.6 match"), content_score=0.6)
        ctx = await ContextBuilder().build(
            persona=_persona(),
            requester_tier=RelationshipTier.FAMILY,
            backend=backend,
            query_embedding_content=[0.5],
            current_message="remember those days at work?",
            retrieval_config=RetrievalConfig(
                activation_threshold=DERIVED_CLASS_A_FLOOR, motif_threshold=99
            ),
        )
        assert [m.content for m in ctx.included_memories] == ["weak 0.6 match"]

    async def test_b2_prompt_shape_at_high_floor(self):
        backend = _ScriptedBackend()
        backend.add_memory(_node("weak 0.6 match"), content_score=0.6)
        ctx = await ContextBuilder().build(
            persona=_persona(),
            requester_tier=RelationshipTier.FAMILY,
            backend=backend,
            query_embedding_content=[0.5],
            current_message="hey who r u?",
            deflection_probe_embedding=[0.5],  # wall armed, corpus below floor
            retrieval_config=RetrievalConfig(
                activation_threshold=0.9, motif_threshold=99
            ),
        )
        assert ctx.included_memories == []
        assert ctx.memory_blocks == ""
        # Nothing above the floor exists, so the wall renders no exemplars
        # either — an honest empty-admissible state (no fabricated grounding).
        assert ctx.deflection_exemplars == []
        prompt = ctx.render()
        assert "ACTIVATED MEMORIES:" in prompt


# ---------------------------------------------------------------------------
# Per-persona override precedence (Class B gate)
# ---------------------------------------------------------------------------


class TestPerPersonaOverridePrecedence:
    async def test_override_wins_over_settings_default(self):
        backend = _ScriptedBackend()
        backend.add_memory(_node("0.6 genuine match"), content_score=0.6)
        persona = _persona({RETRIEVAL_ACTIVATION_FLOOR_KEY: 0.8})
        ctx = await ContextBuilder(
            RetrievalConfig(activation_threshold=DERIVED_CLASS_A_FLOOR)
        ).build(
            persona=persona,
            requester_tier=RelationshipTier.FAMILY,
            backend=backend,
            query_embedding_content=[0.5],
            current_message="remember those days at work?",
        )
        # Persona's own 0.8 floor beats the settings-threaded 0.50 default:
        # the 0.6 memory stays out.
        assert ctx.included_memories == []

    async def test_lower_override_admits_what_default_excludes(self):
        backend = _ScriptedBackend()
        backend.add_memory(_node("0.55 borderline match"), content_score=0.55)
        persona = _persona({RETRIEVAL_ACTIVATION_FLOOR_KEY: 0.45})
        ctx = await ContextBuilder(
            RetrievalConfig(activation_threshold=DERIVED_CLASS_A_FLOOR)
        ).build(
            persona=persona,
            requester_tier=RelationshipTier.FAMILY,
            backend=backend,
            query_embedding_content=[0.5],
            current_message="remember those days at work?",
        )
        assert [m.content for m in ctx.included_memories] == ["0.55 borderline match"]

    async def test_no_override_uses_default(self):
        backend = _ScriptedBackend()
        backend.add_memory(_node("0.6 genuine match"), content_score=0.6)
        ctx = await ContextBuilder(
            RetrievalConfig(activation_threshold=DERIVED_CLASS_A_FLOOR)
        ).build(
            persona=_persona(),
            requester_tier=RelationshipTier.FAMILY,
            backend=backend,
            query_embedding_content=[0.5],
            current_message="remember those days at work?",
        )
        assert [m.content for m in ctx.included_memories] == ["0.6 genuine match"]

    @pytest.mark.parametrize("bad", ["garbage", 0.01, 1.5, "0.02", True])
    async def test_invalid_override_rejected_falls_back_to_default(self, bad: Any):
        backend = _ScriptedBackend()
        backend.add_memory(_node("0.6 genuine match"), content_score=0.6)
        persona = _persona({RETRIEVAL_ACTIVATION_FLOOR_KEY: bad})
        ctx = await ContextBuilder(
            RetrievalConfig(activation_threshold=DERIVED_CLASS_A_FLOOR)
        ).build(
            persona=persona,
            requester_tier=RelationshipTier.FAMILY,
            backend=backend,
            query_embedding_content=[0.5],
            current_message="remember those days at work?",
        )
        # A present-but-invalid override is ignored, never clamped: the safe
        # class-A default governs the turn (0.6 > 0.50 → served).
        assert [m.content for m in ctx.included_memories] == ["0.6 genuine match"]

    def test_resolver_band_boundaries_inclusive(self):
        assert is_valid_activation_floor(ACTIVATION_FLOOR_MIN)
        assert is_valid_activation_floor(ACTIVATION_FLOOR_MAX)
        assert not is_valid_activation_floor(ACTIVATION_FLOOR_MIN - 0.001)
        assert not is_valid_activation_floor(ACTIVATION_FLOOR_MAX + 0.001)

    def test_resolver_unit_precedence(self):
        assert resolve_activation_floor(
            _persona({RETRIEVAL_ACTIVATION_FLOOR_KEY: 0.62}), 0.50
        ) == 0.62
        assert resolve_activation_floor(_persona(), 0.50) == 0.50
        assert resolve_activation_floor(
            _persona({RETRIEVAL_ACTIVATION_FLOOR_KEY: 0.96}), 0.50
        ) == 0.50


# ---------------------------------------------------------------------------
# Settings surface + create_app wiring
# ---------------------------------------------------------------------------


class TestSettingsAndWiring:
    def test_settings_default_is_derived_class_a_floor(self):
        assert Settings.model_fields["retrieval_activation_floor"].default == (
            DERIVED_CLASS_A_FLOOR
        )

    def test_settings_accepts_env_style_value(self):
        assert Settings(retrieval_activation_floor="0.62").retrieval_activation_floor == (
            pytest.approx(0.62)
        )

    @pytest.mark.parametrize("bad", [0.04, 0.96, -1.0, 2.0])
    def test_settings_rejects_out_of_band(self, bad: float):
        with pytest.raises(ValidationError):
            Settings(retrieval_activation_floor=bad)

    def test_create_app_threads_settings_floor_into_default_builder(self):
        settings = Settings(retrieval_activation_floor="0.7")
        app = create_app(settings=settings)
        builder = app.state.context_builder
        assert isinstance(builder, ContextBuilder)
        assert builder._default_retrieval_config is not None
        assert (
            builder._default_retrieval_config.activation_threshold == pytest.approx(0.7)
        )

    def test_create_app_default_uses_derived_floor(self):
        app = create_app(settings=Settings())
        config = app.state.context_builder._default_retrieval_config
        assert config is not None
        assert config.activation_threshold == pytest.approx(DERIVED_CLASS_A_FLOOR)

    def test_injected_builder_is_not_reconfigured(self):
        injected = ContextBuilder()
        app = create_app(settings=Settings(), context_builder=injected)
        assert app.state.context_builder is injected
        # The dataclass fallback (0.3) stays untouched for non-server callers;
        # the settings value must not leak into an injected builder.
        assert injected._default_retrieval_config is None
