"""Unit tests for the post-generation capability-leak guard (HU-2675).

Covers the W3-residual guard (``huible.safety.capability``):

* Structural assistant-register markers (code fluency, teaching register,
  capability boast, AI register) fire only on wall-fired turns and replace
  the reply with the in-voice deflection fallback.
* The bare-answer family catches the recorded OOD2 residual ("Canberra! …")
  — a short factual sentence whose salient token has no trace in the
  turn's grounding corpus — while memory-traceable short answers pass.
* Exemptions: deflection hedges rescue bare-answer replies (never the
  structural family); verbatim exemplar imitation is canon, not a leak.
* The fallback set is self-clean: every variant carries a deflection
  marker, stays claim-free (alignment filter), and survives the G3 affect
  guard's sarcasm patterns.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from huible.memory.protocol import (
    ContentType,
    DisclosureScope,
    MemoryNode,
    MemoryTier,
    SourceType,
)
from huible.safety import (
    CAPABILITY_DEFLECTION_FALLBACK_VARIANTS,
    IDENTITY_INTRO_FALLBACK_VARIANTS,
    apply_affect_guard,
    apply_capability_guard,
    detect_assistant_register,
    extract_claims,
    identity_intro_violation,
    select_capability_fallback,
    select_identity_intro_fallback,
)
from huible.safety.affect import UserAffect, detect_sarcastic_dismissive
from huible.safety.capability import DEFLECTION_MARKERS

PERSONA_NAME = "Chandler Bing"


@dataclass
class _Persona:
    name: str = PERSONA_NAME
    voice_instructions: str = ""
    era_knowledge_boundary: str = "2004-05-06"


def _node(content: str) -> MemoryNode:
    return MemoryNode(
        id=uuid4(),
        persona_id=uuid4(),
        tier=MemoryTier.ACCRUED,
        content=content,
        content_type=ContentType.FACT,
        embedding_content=[0.5],
        memory_date=None,
        source_type=SourceType.EXTRACTION,
        disclosure_scope=DisclosureScope.FAMILY,
        metadata={},
    )


def _persona() -> _Persona:
    return _Persona()


def _guard(
    response: str,
    *,
    wall_fired: bool = True,
    refs: list[MemoryNode] | None = None,
    exemplars: list[MemoryNode] | None = None,
    current_message: str = "What's the capital of Australia?",
    seed: str | None = None,
    identity_exchange: bool = False,
):
    return apply_capability_guard(
        response,
        wall_fired=wall_fired,
        refs=refs or [],
        persona=_persona(),
        deflection_exemplars=exemplars or [],
        current_message=current_message,
        fallback_seed=seed,
        identity_exchange=identity_exchange,
    )


class TestNoopOffWall:
    def test_guard_never_runs_off_wall(self):
        """Assistant-register text on a non-wall turn passes verbatim."""
        leak = "Sure — here's how a for loop works: ```for i in range(10):```"
        report = _guard(leak, wall_fired=False)
        assert report.text == leak
        assert report.disposition == "passed"
        assert report.fired_markers == []

    def test_empty_response_passes(self):
        report = _guard("", wall_fired=True)
        assert report.disposition == "passed"


class TestStructuralMarkers:
    def test_code_block_replaced_on_wall(self):
        report = _guard("Here's how: ```print(range(3))```")
        assert report.disposition == "replaced"
        assert "code_block" in report.fired_markers
        assert report.text in CAPABILITY_DEFLECTION_FALLBACK_VARIANTS

    def test_code_fluency_replaced_on_wall(self):
        report = _guard("Just do: for i in range(10): print(i)")
        assert report.disposition == "replaced"
        assert "code_fluency" in report.fired_markers

    def test_teaching_register_replaced_on_wall(self):
        report = _guard("Let's start with the basics. First, plants need sunlight.")
        assert report.disposition == "replaced"
        assert "teaching_register" in report.fired_markers

    def test_capability_boast_replaced_on_wall(self):
        report = _guard(
            "Canberra! And yes, I'm as surprised as you are that I knew that."
        )
        assert report.disposition == "replaced"
        assert "capability_boast" in report.fired_markers
        assert "Canberra" not in report.text

    def test_ai_self_reference_replaced_on_wall(self):
        report = _guard("As an AI, I can look that up for you.")
        assert report.disposition == "replaced"
        assert "assistant_register" in report.fired_markers

    def test_hedge_does_not_excuse_structural_marker(self):
        report = _guard("No clue about much, but: ```print('hi')```")
        assert report.disposition == "replaced"
        assert "code_block" in report.fired_markers


class TestBareAnswer:
    def test_encyclopedia_one_word_quip_replaced(self):
        """The exact W3 residual shape, minus the boast: a bare fact answer."""
        report = _guard("Canberra!")
        assert report.disposition == "replaced"
        assert report.fired_markers == ["bare_answer"]
        assert "Canberra" not in report.text

    def test_memory_traceable_short_answer_passes(self):
        """An in-corpus proper noun on a wall turn is a memory answer, not a leak."""
        refs = [_node("Janice — is: the girl with the voice from the office.")]
        report = _guard("Janice. God, the voice.", refs=refs)
        assert report.disposition == "passed"

    def test_current_message_tokens_ground_the_reply(self):
        """Echoing the user's own words is first-party, not a base-model fact."""
        report = _guard("Australia. Right?", current_message="What's the capital of Australia?")
        assert report.disposition == "passed"

    def test_short_conversational_quip_passes(self):
        report = _guard("Sure. Moving on.")
        assert report.disposition == "passed"

    def test_question_echo_passes(self):
        report = _guard("The WENUS? Really?")
        assert report.disposition == "passed"

    def test_longer_sentences_not_bare_answer_shape(self):
        report = _guard("I heard about that place once. Weird name for a city, honestly.")
        assert report.disposition == "passed"


class TestExemptions:
    def test_deflection_hedge_passes_bare_answer(self):
        report = _guard("No clue, buddy.")
        assert report.disposition == "passed"

    def test_canon_exemplar_imitation_passes(self):
        exemplars = [_node("general — is: could that BE any more boring? moving on.")]
        report = _guard(
            "could that BE any more boring? moving on.",
            exemplars=exemplars,
        )
        assert report.disposition == "passed"

    def test_canon_quoted_line_with_vanilla_markers_passes(self):
        exemplars = [
            _node(
                "general — is: can I interest you in a sarcastic comment "
                "instead? No clue, buddy."
            )
        ]
        report = _guard(
            "Can I interest you in a sarcastic comment instead? No clue, buddy.",
            exemplars=exemplars,
        )
        assert report.disposition == "passed"

    def test_exemplar_without_hits_still_graded_normally(self):
        exemplars = [_node("general — is: could that BE any more boring?")]
        report = _guard("Canberra!", exemplars=exemplars)
        assert report.disposition == "replaced"


class TestFallback:
    def test_seed_selects_deterministically(self):
        assert select_capability_fallback("conv-1") == select_capability_fallback("conv-1")
        assert select_capability_fallback(None) in CAPABILITY_DEFLECTION_FALLBACK_VARIANTS

    def test_variants_carry_deflection_marker(self):
        for variant in CAPABILITY_DEFLECTION_FALLBACK_VARIANTS:
            low = variant.lower()
            assert any(marker in low for marker in DEFLECTION_MARKERS), variant

    def test_variants_are_claim_free(self):
        for variant in CAPABILITY_DEFLECTION_FALLBACK_VARIANTS:
            assert extract_claims(variant, persona_name=PERSONA_NAME) == [], variant

    def test_variants_pass_own_guard(self):
        for variant in CAPABILITY_DEFLECTION_FALLBACK_VARIANTS:
            report = _guard(variant)
            assert report.disposition == "passed", variant

    def test_variants_survive_affect_guard(self):
        for variant in CAPABILITY_DEFLECTION_FALLBACK_VARIANTS:
            assert detect_sarcastic_dismissive(variant) == [], variant
            guarded, suppressed = apply_affect_guard(variant, affect=UserAffect.DISTRESS)
            assert suppressed is False, variant
            assert guarded == variant

    def test_variants_carry_no_named_entities(self):
        for variant in CAPABILITY_DEFLECTION_FALLBACK_VARIANTS:
            assert "Canberra" not in variant


class TestDetector:
    def test_detect_assistant_register_names(self):
        assert detect_assistant_register("```py") == ["code_block"]
        assert detect_assistant_register("totally normal, no idea") == []
        hits = detect_assistant_register("I knew that. In summary, yes.")
        assert "capability_boast" in hits
        assert "teaching_register" in hits

    def test_detect_clean_in_voice_text(self):
        assert detect_assistant_register("Could that BE any more boring?") == []


class TestCleanPassThrough:
    def test_clean_in_voice_reply_passes_with_no_markers(self):
        report = _guard("Could that BE any more boring? Moving on.")
        assert report.fired_markers == []
        assert report.disposition == "passed"


class TestRealWorldResidual:
    def test_recorded_residual_reply_is_replaced(self):
        """The verbatim c53814cb202e residual must never reach a user again."""
        residual = (
            "Canberra! See, I know things that aren't cheese-related."
        )
        report = _guard(residual)
        assert report.disposition == "replaced"
        assert "Canberra" not in report.text

    def test_ood1_canon_reply_untouched(self):
        """The OOD1 post-W3 PASS reply must keep passing untouched."""
        reply = "Can I interest you in a sarcastic comment instead? No clue, buddy."
        report = _guard(reply)
        assert report.disposition == "passed"
        assert report.text == reply


class TestProtocolShape:
    def test_persona_stub_satisfies_protocol(self):
        from huible.safety.alignment import PersonaVault

        assert isinstance(_persona(), PersonaVault)


class TestArgPassThrough:
    def test_history_and_scope_refs_accepted(self):
        report = apply_capability_guard(
            "Canberra!",
            wall_fired=True,
            refs=[],
            persona=_persona(),
            persona_scope_refs=[_node("work stuff about the WENUS reports")],
            conversation_history=[],
            current_message="What's the capital of Australia?",
        )
        assert report.disposition == "replaced"


class TestIdentityIntroGuard:
    """M1.6 (HU-2732): the M-0 turn-1 full-name self-intro site.

    Identity-exchange turns are IN-domain (the vault's own "who are you?"
    atoms activate), so the wall is silent there — the guard must run off-wall
    when ``identity_exchange`` is set, and must not run on ordinary turns.
    """

    IDENTITY_MESSAGE = "hey who r u?"

    def test_full_name_intro_replaced_off_wall(self):
        leak = "Uh, Chandler. Chandler Bing. The hey-hey guy, apparently."
        report = _guard(
            leak,
            wall_fired=False,
            current_message=self.IDENTITY_MESSAGE,
            identity_exchange=True,
            seed="h1m0-ee7214d9fe",
        )
        assert report.disposition == "replaced"
        assert report.fired_markers == ["full_name_intro"]
        assert "Chandler Bing" not in report.text
        assert report.text in IDENTITY_INTRO_FALLBACK_VARIANTS

    def test_my_name_is_formula_replaced(self):
        report = _guard(
            "my name is Chandler, nice to meet you",
            wall_fired=False,
            current_message=self.IDENTITY_MESSAGE,
            identity_exchange=True,
        )
        assert report.disposition == "replaced"
        assert report.fired_markers == ["my_name_is"]

    def test_first_name_only_reply_passes(self):
        reply = "Chandler. And you are?"
        report = _guard(
            reply,
            wall_fired=False,
            current_message=self.IDENTITY_MESSAGE,
            identity_exchange=True,
        )
        assert report.disposition == "passed"
        assert report.text == reply

    def test_clean_recognition_reply_passes(self):
        reply = "Hey-hey, you know exactly who this is."
        report = _guard(
            reply,
            wall_fired=False,
            current_message=self.IDENTITY_MESSAGE,
            identity_exchange=True,
        )
        assert report.disposition == "passed"
        assert report.text == reply

    def test_guard_never_runs_on_ordinary_turn(self):
        leak = "Uh, Chandler. Chandler Bing. The hey-hey guy, apparently."
        report = _guard(leak, wall_fired=True, identity_exchange=False)
        assert report.disposition == "passed"
        assert report.text == leak

    def test_empty_response_passes(self):
        report = _guard(
            "",
            wall_fired=False,
            current_message=self.IDENTITY_MESSAGE,
            identity_exchange=True,
        )
        assert report.disposition == "passed"

    def test_h2_site_replaced(self):
        """The H2 class-b site from the same RED run."""
        leak = "Depends — are you a beautiful woman? Because then it's Chandler Bing."
        report = _guard(
            leak,
            wall_fired=False,
            current_message="who am i talking to?",
            identity_exchange=True,
            seed="conv-b",
        )
        assert report.disposition == "replaced"
        assert report.fired_markers == ["full_name_intro"]


class TestIdentityFallbackVariants:
    def test_seed_selects_deterministically(self):
        assert select_identity_intro_fallback("conv-1") == select_identity_intro_fallback(
            "conv-1"
        )
        assert select_identity_intro_fallback(None) in IDENTITY_INTRO_FALLBACK_VARIANTS

    def test_variants_carry_no_intro_violation(self):
        for variant in IDENTITY_INTRO_FALLBACK_VARIANTS:
            assert identity_intro_violation(variant, persona_name=PERSONA_NAME) is None, (
                variant
            )

    def test_variants_are_claim_free(self):
        for variant in IDENTITY_INTRO_FALLBACK_VARIANTS:
            assert extract_claims(variant, persona_name=PERSONA_NAME) == [], variant

    def test_variants_carry_no_assistant_register(self):
        for variant in IDENTITY_INTRO_FALLBACK_VARIANTS:
            assert detect_assistant_register(variant) == [], variant

    def test_variants_survive_affect_guard(self):
        for variant in IDENTITY_INTRO_FALLBACK_VARIANTS:
            assert detect_sarcastic_dismissive(variant) == [], variant
            guarded, suppressed = apply_affect_guard(variant, affect=UserAffect.DISTRESS)
            assert suppressed is False, variant
            assert guarded == variant

    def test_variants_pass_own_guard(self):
        for variant in IDENTITY_INTRO_FALLBACK_VARIANTS:
            report = _guard(
                variant,
                wall_fired=False,
                current_message="hey who r u?",
                identity_exchange=True,
            )
            assert report.disposition == "passed", variant


class TestImports:
    def test_public_exports(self):
        import huible.safety as safety

        for name in (
            "apply_capability_guard",
            "CapabilityGuardReport",
            "select_capability_fallback",
            "detect_assistant_register",
            "DEFLECTION_MARKERS",
            "ASSISTANT_REGISTER_PATTERNS",
            "identity_intro_violation",
            "select_identity_intro_fallback",
            "IDENTITY_INTRO_FALLBACK_VARIANTS",
        ):
            assert hasattr(safety, name), name
