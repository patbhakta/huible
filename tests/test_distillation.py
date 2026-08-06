from __future__ import annotations

from datetime import UTC, datetime

import pytest

from huible.distillation import (
    Distiller,
    L0Record,
    MarkdownMemoryStore,
    MarkdownPersonaResponder,
    MemoryType,
    Tier,
    parse_frontmatter,
    render_l1,
)
from huible.distillation.records import L1Fact
from huible.distillation.records import MemoryType as MT

UTC = UTC


def _raw(content: str, occurred: str = "2024-10-15T00:00:00+00:00") -> L0Record:
    return L0Record(
        content=content,
        occurred_at=datetime.fromisoformat(occurred),
    )


@pytest.mark.asyncio
async def test_distill_produces_pyramid():
    distiller = Distiller()
    result = await distiller.distill(
        [
            _raw("Pat loves drinking warm Earl Grey tea with oat milk on Sunday mornings."),
            _raw("Pat complained about Hermes losing conversation context during refactoring."),
        ]
    )
    assert len(result.facts) == 2
    assert len(result.scenarios) >= 1
    assert len(result.profiles) >= 1
    assert all(f.evidence for f in result.facts)
    assert all(p.evidence for p in result.profiles)


@pytest.mark.asyncio
async def test_memory_type_classification():
    distiller = Distiller()
    result = await distiller.distill([_raw("Pat always drinks unsweetened tea.")])
    assert result.facts[0].memory_type is MT.DURABLE_RULE
    assert any(p.memory_type is MT.DURABLE_RULE for p in result.profiles)

    result = await distiller.distill([_raw("Pat lives in Seattle now.")])
    assert result.facts[0].memory_type is MT.CURRENT_STATE

    result = await distiller.distill([_raw("Pat visited the garden on a Tuesday.")])
    assert result.facts[0].memory_type is MT.OBSERVATION


def test_markdown_frontmatter_roundtrip():
    fact = L1Fact(
        subject="Pat",
        predicate="prefers",
        object="Earl Grey with oat milk",
        memory_type=MT.DURABLE_RULE,
        valid_from=datetime(2024, 10, 15, tzinfo=UTC),
        content="Pat prefers Earl Grey with oat milk.",
    )
    text = render_l1(fact)
    fields, body = parse_frontmatter(text)
    assert fields["tier"] == Tier.L1.value
    assert fields["memory_type"] == MT.DURABLE_RULE.value
    assert fields["valid_from"] == "2024-10-15T00:00:00+00:00"
    assert body == "Pat prefers Earl Grey with oat milk."


@pytest.mark.asyncio
async def test_store_query_temporal_validity(tmp_path):
    distiller = Distiller()
    result = await distiller.distill([_raw("Pat always drinks unsweetened tea.")])
    store = MarkdownMemoryStore(tmp_path)
    store.write_result(result)

    now = datetime(2025, 1, 1, tzinfo=UTC)
    before = len(store.query(MemoryType.DURABLE_RULE, now=now))
    assert before >= 1

    # Expiring the L1 fact's validity window must drop it from the active set.
    result.facts[0].valid_to = datetime(2024, 1, 1, tzinfo=UTC)
    store.write_result(result)
    after = len(store.query(MemoryType.DURABLE_RULE, now=now))
    assert after == before - 1


@pytest.mark.asyncio
async def test_responder_uses_markdown_not_vectors(tmp_path):
    distiller = Distiller()
    result = await distiller.distill(
        [
            _raw("Pat loves drinking warm Earl Grey tea with oat milk."),
            _raw("Pat complained about Hermes losing conversation context."),
        ]
    )
    store = MarkdownMemoryStore(tmp_path)
    store.write_result(result)

    responder = MarkdownPersonaResponder(store, persona_name="Pat")
    response = responder.respond("What tea does Pat like?")
    assert "Earl Grey" in response.reply or "tea" in response.reply
    assert response.hits
    assert any("[evidence" in response.reply for _ in [0])
    assert all(h.source for h in response.hits)


def test_responder_no_match(tmp_path):
    store = MarkdownMemoryStore(tmp_path)
    responder = MarkdownPersonaResponder(store)
    response = responder.respond("quantum entanglement")
    assert response.reply
    assert response.hits == []
