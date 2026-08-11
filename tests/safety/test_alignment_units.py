"""Unit tests for the §7.4.2 generation-time claim->ref alignment filter.

Covers :mod:`huible.safety.alignment` in isolation: the claim taxonomy
(identity / advice policy claims + biographical / relationship entity-anchored
claims), the grounding corpus + content-overlap alignment method, and the
fail-the-turn-safely disposition policy. Mirrors the G3 affect-guard unit
suite so the deterministic clinical baseline holds before the e2e wiring.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import uuid4

from huible.memory.protocol import (
    ContentType,
    DisclosureScope,
    MemoryNode,
    MemoryTier,
    SourceType,
)
from huible.persona.context import CONFIDENCE_LEVEL_METADATA_KEY, PersonaConfig
from huible.safety import (
    ALIGNMENT_FALLBACK_RESPONSE,
    AlignmentReport,
    ClaimCategory,
    align_response,
    apply_alignment_guard,
    build_grounding_corpus,
    extract_claims,
    is_grounded,
)
from huible.safety.alignment import Claim

PERSONA = PersonaConfig(
    id=uuid4(),
    name="Chandler",
    voice_instructions="Warm Texas storyteller.",
    era_knowledge_boundary="2024-12-01",
    age_at_death=72,
    death_date="2024-12-01",
)


def _node(content: str, *, confidence_level: str = "high") -> MemoryNode:
    metadata: dict[str, Any] = {CONFIDENCE_LEVEL_METADATA_KEY: confidence_level}
    return MemoryNode(
        id=uuid4(),
        persona_id=PERSONA.id,
        tier=MemoryTier.CANONICAL,
        content=content,
        content_type=ContentType.NARRATIVE,
        memory_date=date(2015, 7, 15),
        source_type=SourceType.EXTRACTION,
        disclosure_scope=DisclosureScope.FAMILY,
        metadata=metadata,
    )


LAKE = _node("Chandler loved fishing on Lake Travis.")
RODS = _node("He kept his rods in the garage.")
VAULT_REFS = [LAKE, RODS]


# --- Claim taxonomy: identity / advice (policy claims) ---------------------


class TestPolicyClaimExtraction:
    def test_identity_remember_dying_is_a_claim(self):
        claims = extract_claims("I remember dying and seeing a light.", persona_name="Chandler")
        assert any(c.category == ClaimCategory.IDENTITY for c in claims)

    def test_identity_really_here_is_a_claim(self):
        claims = extract_claims("I am really here with you now.", persona_name="Chandler")
        assert any(c.category == ClaimCategory.IDENTITY for c in claims)

    def test_identity_afterlife_is_a_claim(self):
        claims = extract_claims("I am in a better place now.", persona_name="Chandler")
        assert any(c.category == ClaimCategory.IDENTITY for c in claims)

    def test_identity_came_back_is_a_claim(self):
        claims = extract_claims("I've come back to be with you.", persona_name="Chandler")
        assert any(c.category == ClaimCategory.IDENTITY for c in claims)

    def test_advice_you_should_is_a_claim(self):
        claims = extract_claims("You should move on and be happy.", persona_name="Chandler")
        assert any(c.category == ClaimCategory.ADVICE for c in claims)

    def test_advice_what_id_want_is_a_claim(self):
        claims = extract_claims(
            "What I'd want you to do is forgive yourself.", persona_name="Chandler"
        )
        assert any(c.category == ClaimCategory.ADVICE for c in claims)


# --- Claim taxonomy: biographical / relationship (entity-anchored) ---------


class TestEntityAnchoredExtraction:
    def test_grounded_biographical_claim_is_extracted(self):
        claims = extract_claims(
            "I loved fishing on Lake Travis as a boy.", persona_name="Chandler"
        )
        bio = [c for c in claims if c.category == ClaimCategory.BIOGRAPHICAL]
        assert len(bio) == 1
        assert "Lake Travis" in bio[0].salient_entities or "Lake" in bio[0].salient_entities

    def test_relationship_claim_uses_kinship_term(self):
        claims = extract_claims(
            "We went to Rome together, your mother and I.", persona_name="Chandler"
        )
        rel = [c for c in claims if c.category == ClaimCategory.RELATIONSHIP]
        assert len(rel) == 1

    def test_pure_reflection_is_not_a_claim(self):
        # No named entity → not a factual claim (reflection passes).
        claims = extract_claims(
            "I remember those days fondly, and I'm here with you.",
            persona_name="Chandler",
        )
        assert claims == []

    def test_persona_self_reference_is_not_an_entity(self):
        # A reply that only names the persona is self-reference, not a claim.
        claims = extract_claims("Chandler reflects on what you shared.", persona_name="Chandler")
        assert claims == []

    def test_fake_llm_digest_is_not_a_claim(self):
        claims = extract_claims(
            "[fake-llm:abcd1234] Deterministic response.", persona_name="Chandler"
        )
        assert claims == []

    def test_warm_distress_fallback_is_not_a_claim(self):
        # The G3 affect-guard fallback must not register as a claim (the
        # alignment filter runs after the affect guard).
        claims = extract_claims(
            "I hear you, and I'm right here with you. That's a heavy thing to carry.",
            persona_name="Chandler",
        )
        assert claims == []


# --- Grounding corpus + alignment ------------------------------------------


class TestGroundingCorpus:
    def test_corpus_includes_ref_content_tokens(self):
        corpus = build_grounding_corpus(VAULT_REFS, PERSONA)
        assert "fishing" in corpus
        assert "lake" in corpus
        assert "travis" in corpus

    def test_corpus_includes_persona_vault(self):
        corpus = build_grounding_corpus([], PERSONA)
        # Name + voice-instruction tokens + era boundary.
        assert "chandler" in corpus
        assert "warm" in corpus
        assert "texas" in corpus
        assert "2024-12-01" in corpus

    def test_is_grounded_identity_is_never_grounded(self):
        claim = Claim(text="I remember dying.", category=ClaimCategory.IDENTITY)
        assert is_grounded(claim, build_grounding_corpus(VAULT_REFS, PERSONA)) is False

    def test_is_grounded_advice_is_never_grounded(self):
        claim = Claim(text="You should move on.", category=ClaimCategory.ADVICE)
        assert is_grounded(claim, build_grounding_corpus(VAULT_REFS, PERSONA)) is False

    def test_is_grounded_biographical_known_entity(self):
        claim = Claim(
            text="I loved fishing on Lake Travis.",
            category=ClaimCategory.BIOGRAPHICAL,
            salient_entities=("Lake Travis",),
        )
        assert is_grounded(claim, build_grounding_corpus(VAULT_REFS, PERSONA)) is True

    def test_is_grounded_biographical_unknown_entity(self):
        claim = Claim(
            text="I lived in Marfa for years.",
            category=ClaimCategory.BIOGRAPHICAL,
            salient_entities=("Marfa",),
        )
        assert is_grounded(claim, build_grounding_corpus(VAULT_REFS, PERSONA)) is False


# --- align_response (no mutation) ------------------------------------------


class TestAlignResponse:
    def test_clean_reflection_passes(self):
        report = align_response(
            "I'm glad you're here. Tell me more.", refs=VAULT_REFS, persona=PERSONA
        )
        assert report.disposition == "passed"
        assert report.ungrounded == []
        assert report.claim_count == 0

    def test_grounded_biographical_passes(self):
        report = align_response(
            "I loved fishing on Lake Travis.", refs=VAULT_REFS, persona=PERSONA
        )
        assert report.disposition == "passed"
        assert report.claim_count == 1
        assert report.ungrounded_count == 0

    def test_ungrounded_biographical_flagged_not_mutated(self):
        original = "I lived in Marfa for twenty years."
        report = align_response(original, refs=VAULT_REFS, persona=PERSONA)
        assert report.disposition == "suppressed"
        assert report.ungrounded_count == 1
        assert report.text == original  # align_response does not mutate

    def test_identity_claim_flagged(self):
        report = align_response(
            "I remember dying, it was peaceful.", refs=VAULT_REFS, persona=PERSONA
        )
        assert report.disposition == "suppressed"
        assert report.ungrounded[0].category == ClaimCategory.IDENTITY

    def test_advice_claim_flagged(self):
        report = align_response(
            "You should see a therapist about this.", refs=VAULT_REFS, persona=PERSONA
        )
        assert report.disposition == "suppressed"
        assert report.ungrounded[0].category == ClaimCategory.ADVICE

    def test_relationship_ungrounded_flagged(self):
        report = align_response(
            "We went to Rome together, your mother and I.",
            refs=VAULT_REFS,
            persona=PERSONA,
        )
        assert report.disposition == "suppressed"
        assert report.ungrounded[0].category == ClaimCategory.RELATIONSHIP

    def test_category_counts_drive_telemetry(self):
        report = align_response(
            "I lived in Marfa. You should move on. I am really here.",
            refs=VAULT_REFS,
            persona=PERSONA,
        )
        counts = report.category_counts()
        assert counts.get(ClaimCategory.BIOGRAPHICAL) == 1
        assert counts.get(ClaimCategory.ADVICE) == 1
        assert counts.get(ClaimCategory.IDENTITY) == 1


# --- apply_alignment_guard (disposition policy) ----------------------------


class TestApplyAlignmentGuard:
    def test_clean_response_returned_verbatim(self):
        clean = "I remember those mornings on Lake Travis."
        report = apply_alignment_guard(clean, refs=VAULT_REFS, persona=PERSONA)
        assert report.disposition == "passed"
        assert report.text == clean

    def test_ungrounded_replaced_with_fallback(self):
        report = apply_alignment_guard(
            "I lived in Marfa for twenty years.", refs=VAULT_REFS, persona=PERSONA
        )
        assert report.disposition == "suppressed"
        assert report.text == ALIGNMENT_FALLBACK_RESPONSE

    def test_fallback_is_itself_claim_free(self):
        # The safe fallback must pass its own filter (no new claim introduced).
        report = apply_alignment_guard(
            ALIGNMENT_FALLBACK_RESPONSE, refs=[], persona=PERSONA
        )
        assert report.disposition == "passed"
        assert report.text == ALIGNMENT_FALLBACK_RESPONSE

    def test_report_type_is_alignment_report(self):
        report = apply_alignment_guard("hello", refs=[], persona=PERSONA)
        assert isinstance(report, AlignmentReport)


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
