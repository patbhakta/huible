"""Unit tests for the §7.4.2 generation-time claim->ref alignment filter.

Covers :mod:`huible.safety.alignment` in isolation: the claim taxonomy
(identity / advice policy claims + biographical / relationship entity-anchored
claims), the grounding corpus + content-overlap alignment method, and the
fail-the-turn-safely disposition policy. Mirrors the G3 affect-guard unit
suite so the deterministic clinical baseline holds before the e2e wiring.
"""

from __future__ import annotations

from datetime import date
from typing import Any, ClassVar
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
    ALIGNMENT_FALLBACK_VARIANTS,
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

    def test_advice_grain_of_salt_idiom_is_not_a_claim(self):
        """HU-2161 re-probe: the anti-advice disclaimer must not fire G9."""
        claims = extract_claims(
            "Take my advice with a grain of salt the size of my student loans.",
            persona_name="Chandler",
        )
        assert not any(c.category == ClaimCategory.ADVICE for c in claims)

    def test_explicit_my_advice_is_to_is_a_claim(self):
        claims = extract_claims(
            "My advice is to take it one day at a time.", persona_name="Chandler"
        )
        assert any(c.category == ClaimCategory.ADVICE for c in claims)

    def test_empathic_keep_talking_to_someone_is_not_a_claim(self):
        """HU-2297: the exact live HU-1911 control false positive (epoch
        40dda40cfcca, 14:38Z) — an empathic-support closer continuing an
        existing support conversation — must not fire G9 (it paged sev-1
        un-adjudicated as an advice-class policy claim)."""
        claims = extract_claims("Please keep talking to someone.", persona_name="Chandler")
        assert not any(c.category == ClaimCategory.ADVICE for c in claims)

    def test_continuation_register_variants_are_not_advice(self):
        """HU-2297: keep/keep-on/kept/continue + progressive referral verb is
        support register in every variant, not a referral directive."""
        for text in (
            "Keep talking with a counselor about that.",
            "She keeps talking to someone at the VA, and it helps.",
            "I kept seeing a therapist after the funeral.",
            "Keep on talking to someone who knew him.",
            "Continue talking with someone from your group.",
        ):
            claims = extract_claims(text, persona_name="Chandler")
            assert not any(c.category == ClaimCategory.ADVICE for c in claims), text

    def test_affirmative_recognition_register_is_not_advice(self):
        """HU-2297 CA ruling 26fda368: second-person progressive statives
        ("I'm glad you're talking to someone" class) recognize the user's
        existing support — presupposition-of-continuation, not a referral
        directive — and must never fire G9."""
        for text in (
            "I'm glad you're talking to someone.",
            "It's good that you are talking with a counselor.",
            "I'm glad you've been talking to someone at the VA.",
            "That's great that you have been seeing a therapist.",
            "Sounds like you're talking with someone who understands.",
        ):
            claims = extract_claims(text, persona_name="Chandler")
            assert not any(c.category == ClaimCategory.ADVICE for c in claims), text

    def test_true_g9_referral_positives_still_fire(self):
        """HU-2297 acceptance: deterministic suppression of directive referral
        forms is unchanged, including inside continuation wording when a
        prescriptive pattern co-occurs in the sentence."""
        for text in (
            "Talk to a therapist about this.",
            "You should see someone about that.",
            "You should keep talking to someone.",  # prescriptive modal survives
            "You should be talking to someone about this.",  # modal + stative
            "You need to be talking with a counselor.",  # modal + stative
            "It might help to talk to a counselor.",
            "Have you considered seeing someone?",
            "My advice is to keep talking to someone.",  # explicit my-advice form
            "Keep talking to someone, and you should see a doctor.",  # mixed sentence
        ):
            claims = extract_claims(text, persona_name="Chandler")
            assert any(c.category == ClaimCategory.ADVICE for c in claims), text


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


# --- HU-2070: persona-scope grounding corpus widening -----------------------


class TestPersonaScopeGroundingWidening:
    """HU-2070: truthful replies naming entities that live in the wider
    persona corpus (raw-dialogue corpora) must not be suppressed just because
    the turn's retrieval window missed them."""

    #: Wider persona corpus: a Thanksgiving memory the turn's refs miss.
    THANKSGIVING = _node("Every Thanksgiving, we hosted dinner in the apartment.")
    CHICK_DUCK = _node("The chick and the duck lived with us.")
    PERSONA_SCOPE: ClassVar[list[MemoryNode]] = [THANKSGIVING, CHICK_DUCK]

    def test_corpus_unions_persona_scope_tokens(self):
        base = build_grounding_corpus(VAULT_REFS, PERSONA)
        widened = build_grounding_corpus(
            VAULT_REFS, PERSONA, persona_scope_refs=self.PERSONA_SCOPE
        )
        assert "thanksgiving" not in base
        assert "thanksgiving" in widened
        assert base < widened  # widening is strictly additive

    def test_persona_true_reply_passes_with_widening(self):
        reply = "I loved our Thanksgiving dinner with the chick and the duck."
        strict = apply_alignment_guard(reply, refs=VAULT_REFS, persona=PERSONA)
        assert strict.disposition == "suppressed"  # the HU-2070 symptom
        widened = apply_alignment_guard(
            reply, refs=VAULT_REFS, persona=PERSONA, persona_scope_refs=self.PERSONA_SCOPE
        )
        assert widened.disposition == "passed"
        assert widened.text == reply

    def test_omitted_scope_keeps_pre_widening_behavior(self):
        """The clinical Stage-A oracle path (no persona_scope_refs) is unchanged."""
        reply = "I loved our Thanksgiving dinner with the chick and the duck."
        report = align_response(reply, refs=VAULT_REFS, persona=PERSONA)
        assert report.disposition == "suppressed"
        assert report.ungrounded_count == 1

    def test_fabricated_entity_still_suppressed_under_widening(self):
        reply = "I lived in Marfa for twenty years."
        report = apply_alignment_guard(
            reply,
            refs=VAULT_REFS,
            persona=PERSONA,
            persona_scope_refs=self.PERSONA_SCOPE,
        )
        assert report.disposition == "suppressed"
        assert report.text == ALIGNMENT_FALLBACK_RESPONSE

    def test_identity_claim_never_grounded_by_scope_widening(self):
        reply = "I am really here. I remember dying."
        report = apply_alignment_guard(
            reply,
            refs=VAULT_REFS,
            persona=PERSONA,
            persona_scope_refs=self.PERSONA_SCOPE,
        )
        assert report.disposition == "suppressed"
        assert report.text == ALIGNMENT_FALLBACK_RESPONSE

    def test_empty_scope_list_is_a_noop(self):
        reply = "I loved our Thanksgiving dinner with the chick and the duck."
        report = apply_alignment_guard(
            reply, refs=VAULT_REFS, persona=PERSONA, persona_scope_refs=[]
        )
        assert report.disposition == "suppressed"


# --- HU-2161: current-message widening + entity-precision -------------------


class TestCurrentMessageGrounding:
    """HU-2161: a truthful reply echoing the user's own phrasing this turn is
    first-party truth — the user's message grounds the reply's echo of it."""

    def test_echo_of_current_user_message_passes(self):
        """A reply echoing the user's own named entity this turn (the
        user's words are first-party truth for the exchange) is grounded by
        the current message, not suppressed."""
        reply = "Guilty — I famously fled the Yakima job that winter."
        current = "Any advice from a man who famously fled the Yakima job?"
        strict = apply_alignment_guard(reply, refs=VAULT_REFS, persona=PERSONA)
        assert strict.disposition == "suppressed"  # the HU-2161 symptom
        widened = apply_alignment_guard(
            reply, refs=VAULT_REFS, persona=PERSONA, current_message=current
        )
        assert widened.disposition == "passed"
        assert widened.text == reply

    def test_current_message_does_not_ground_other_entities(self):
        """Widening is scoped: a fabricated entity the user never said and no
        ref carries is still suppressed."""
        reply = "I lived in Marfa for twenty years."
        report = apply_alignment_guard(
            reply,
            refs=VAULT_REFS,
            persona=PERSONA,
            current_message="Tell me about your years in office.",
        )
        assert report.disposition == "suppressed"


class TestEntityDenylistPrecision:
    """HU-2161: capitalized discourse words (inside quotations) are not named
    entities and must never anchor a biographical claim."""

    def test_mid_quote_discourse_adverb_is_not_an_entity(self):
        claims = extract_claims(
            '"Famously fled his own job" — I\'d be offended if it weren\'t accurate.',
            persona_name="Chandler",
        )
        assert not any("Famously" in c.salient_entities for c in claims)

    def test_real_named_entity_still_anchors(self):
        claims = extract_claims(
            "I lived in Marfa with Walter for years.", persona_name="Eleanor"
        )
        assert any(c.salient_entities for c in claims)


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

    def test_empathic_support_closer_passes_alignment(self):
        """HU-2297 e2e: the empathic closer that paged sev-1 live now passes
        the guard untouched — no suppression, so no page and no voice break."""
        report = align_response(
            "Please keep talking to someone.", refs=VAULT_REFS, persona=PERSONA
        )
        assert report.disposition == "passed"
        assert report.ungrounded == []

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

    def test_every_fallback_variant_is_claim_free_and_texting_length(self):
        # HU-1911 human-touch gate: the variation set must (a) each pass the
        # module's own claim filter and (b) stay texting-length (rubric #3).
        from huible.safety.alignment import ALIGNMENT_FALLBACK_VARIANTS

        for variant in ALIGNMENT_FALLBACK_VARIANTS:
            report = apply_alignment_guard(variant, refs=[], persona=PERSONA)
            assert report.disposition == "passed", variant
            assert len(variant) <= 160, variant

    def test_fallback_seed_varies_and_is_stable(self):
        # Deterministic per-conversation selection: stable for a seed,
        # different across seeds (HU-1911 verbatim-duplication fix).
        from huible.safety.alignment import select_alignment_fallback

        assert select_alignment_fallback(None) == ALIGNMENT_FALLBACK_RESPONSE
        assert select_alignment_fallback("") == ALIGNMENT_FALLBACK_RESPONSE
        first = select_alignment_fallback("conv-a")
        assert first == select_alignment_fallback("conv-a")
        picks = {select_alignment_fallback(f"conv-{i}") for i in range(20)}
        assert picks <= set(ALIGNMENT_FALLBACK_VARIANTS)
        assert len(picks) > 1  # the set actually varies across conversations

    def test_report_type_is_alignment_report(self):
        report = apply_alignment_guard("hello", refs=[], persona=PERSONA)
        assert isinstance(report, AlignmentReport)


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
