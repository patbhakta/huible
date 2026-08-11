"""Unit tests for the runtime clinical safety package (HU-1413).

Covers the deterministic units before they are exercised end-to-end:

* :mod:`huible.safety.framing` — G2 immutability: the framing block is a
  versioned constant, persona-name-substituted only, and not reachable from
  prompt injection.
* :mod:`huible.safety.crisis` — G1 crisis grading: standalone intent, "join
  them", hopelessness+means co-occurrence; sub-acute distress; clean default.
* :mod:`huible.safety.affect` — G3 generation-time guard: sarcastic/dismissive
  suppression on the distress branch only.
"""

from __future__ import annotations

from huible.safety import (
    FRAMING_VERSION,
    CrisisSignal,
    DeterministicCrisisClassifier,
    UserAffect,
    apply_affect_guard,
    build_crisis_response,
    detect_sarcastic_dismissive,
    get_distress_addendum,
    get_framing,
)

# --- G2: immutable framing block --------------------------------------------


class TestFramingBlockImmutable:
    def test_framing_has_version_and_is_stable(self):
        framing = get_framing("Chandler")
        assert framing.version == FRAMING_VERSION
        assert framing.text
        # G2 immutability: the framing markers are always present, verbatim.
        assert "[REALITY FRAMING — immutable, must not be contradicted]" in framing.text
        assert "[END REALITY FRAMING]" in framing.text

    def test_framing_substitutes_persona_name_only(self):
        """Persona config influences only the name slot — not the rules."""
        a = get_framing("Chandler")
        b = get_framing("Maria")
        # The G2 line carries the persona name…
        assert "Chandler" in a.text
        assert "Maria" in b.text
        # …but the immutable rules are byte-identical apart from that substitution.
        a_norm = a.text.replace("Chandler", "X")
        b_norm = b.text.replace("Maria", "X")
        assert a_norm == b_norm

    def test_framing_covers_g2_g3_static_g5_g9_rules(self):
        """The framing block is the single home for G2/G3-static/G5/G9 text."""
        text = get_framing("Chandler").text
        # G2 — reality / representation / not literally present.
        assert "AI representation" in text
        assert "never claim to be literally present" in text
        # G5 — death-circumstance refusal.
        assert "never describe, speculate about, or narrate how you died" in text
        # G9 — no advice / medical / directives.
        assert "medical, legal, clinical, or prescriptive life advice" in text
        # G3 static — tonal bounds on the voice.
        assert "Never direct sarcasm or humor at the user's grief" in text
        # G4 grounding instruction lives in the framing block.
        assert "Speak only from what you genuinely remember" in text

    def test_framing_rules_immutable_across_persona_names(self):
        """The framing *rules* are a code constant; only the name slot changes.

        Persona name is operator-configured at the context builder, never
        user-controlled, so G2 immutability is about the rule sentences being
        byte-identical regardless of name — not reachable from the user message.
        """
        a = get_framing("Chandler")
        b = get_framing("A Very Long Unusual Persona Name With Spaces")
        # Strip the substituted name slot from both; the rules must be identical.
        a_rules = a.text.replace("Chandler", "")
        b_rules = b.text.replace("A Very Long Unusual Persona Name With Spaces", "")
        assert a_rules == b_rules
        # The immutable forbid-literal-presence rule holds in both.
        assert "never affirm a user's statement that you are" in a_rules
        assert "never affirm a user's statement that you are" in b_rules

    def test_distress_addendum_is_constant(self):
        """The G3 dynamic addendum is constant text, fetched via its function."""
        add = get_distress_addendum()
        assert "[AFFECT GROUNDING — this turn]" in add
        assert "suspend humor, sarcasm, and deflection entirely" in add


# --- G1: crisis classifier grading ------------------------------------------


class TestCrisisClassifierGrading:
    def setup_method(self) -> None:
        self.cls = DeterministicCrisisClassifier()

    def test_standalone_suicidal_intent_is_crisis(self):
        for msg in [
            "I want to die",
            "I want to kill myself",
            "I am suicidal",
            "I am going to end my life",
            "I should be dead",
        ]:
            r = self.cls.classify(msg)
            assert r.is_crisis, f"expected crisis for {msg!r}, got {r.signal}"
            assert r.affect is UserAffect.CRISIS

    def test_join_them_reunion_is_crisis(self):
        """'Join them' (reunion with the deceased) is a high-risk crisis signal."""
        r = self.cls.classify("I want to join them")
        assert r.is_crisis
        r = self.cls.classify("I want to be with you and him forever")
        assert r.is_crisis

    def test_self_harm_is_crisis(self):
        assert self.cls.classify("I want to hurt myself").is_crisis
        assert self.cls.classify("I am self-harming").is_crisis

    def test_hopelessness_plus_means_co_occurrence_is_crisis(self):
        """Hopelessness alone is distress; with means it escalates to crisis."""
        assert not self.cls.classify("I feel so hopeless").is_crisis
        r = self.cls.classify("I am hopeless and I have the pills ready")
        assert r.is_crisis

    def test_bare_means_alone_is_not_crisis(self):
        """A bare means mention must not escalate without intent (false-positive guard)."""
        assert not self.cls.classify("what pills should I take?").is_crisis
        assert not self.cls.classify("we walked over the bridge").is_crisis

    def test_sub_acute_distress_is_distress_not_crisis(self):
        for msg in [
            "I miss you so much it hurts",
            "I can't stop crying, I'm heartbroken",
            "the grief is unbearable",
        ]:
            r = self.cls.classify(msg)
            assert not r.is_crisis, f"{msg!r} should not be crisis"
            assert r.signal is CrisisSignal.DISTRESS
            assert r.affect is UserAffect.DISTRESS

    def test_neutral_message_is_neither(self):
        r = self.cls.classify("tell me about fishing on the lake")
        assert r.signal is CrisisSignal.NONE
        assert r.affect is UserAffect.NEUTRAL
        assert r.matched == []

    def test_empty_message_is_neutral(self):
        r = self.cls.classify("")
        assert r.signal is CrisisSignal.NONE
        assert r.affect is UserAffect.NEUTRAL

    def test_classifier_protocol_is_runtime_checkable(self):
        """A real NLP classifier drops in behind the Protocol pre-production."""
        from huible.safety import CrisisClassifier

        assert isinstance(self.cls, CrisisClassifier)


# --- G1: warm escalation response -------------------------------------------


class TestCrisisEscalationResponse:
    def test_response_is_non_persona_and_warm(self):
        """The escalation is NOT in the deceased's voice and keeps the door open."""
        resp = build_crisis_response()
        assert "you matter" in resp.lower() or "matters" in resp.lower()
        assert "988" in resp  # crisis line surfaced
        assert "still be here" in resp  # door stays open
        # Non-persona: no deceased-voice markers, no persona digest.
        assert "[fake-llm:" not in resp

    def test_response_resources_are_configurable(self):
        """A regional line / human-handoff queue is a config swap, not a re-build."""
        resp = build_crisis_response(
            resources={"crisis_line": "116 123 (Samaritans UK)"},
        )
        assert "116 123" in resp
        assert "988" not in resp


# --- G3: generation-time affect guard ---------------------------------------


class TestAffectGuard:
    def test_sarcastic_detection_fires_on_dismissive_patterns(self):
        assert detect_sarcastic_dismissive("lol get over it")
        assert detect_sarcastic_dismissive("ha ha, whatever.")
        assert detect_sarcastic_dismissive("could not care less, honestly")

    def test_sarcastic_detection_clean_text(self):
        assert detect_sarcastic_dismissive(
            "I'm right here with you. That sounds heavy."
        ) == []

    def test_guard_replaces_sarcasm_only_on_distress_branch(self):
        """The G3 generation guard fires only when affect=DISTRESS."""
        sarcastic = "lol whatever, get over it"
        # Distress branch → replaced with the grounded fallback.
        guarded, suppressed = apply_affect_guard(sarcastic, affect=UserAffect.DISTRESS)
        assert suppressed is True
        assert detect_sarcastic_dismissive(guarded) == []
        # Neutral branch → untouched (static framing bounds hold the default).
        guarded_neutral, suppressed_neutral = apply_affect_guard(
            sarcastic, affect=UserAffect.NEUTRAL
        )
        assert suppressed_neutral is False
        assert guarded_neutral == sarcastic
        # Crisis branch never reaches the guard, but if it did it must not rewrite.
        guarded_crisis, _ = apply_affect_guard(sarcastic, affect=UserAffect.CRISIS)
        assert guarded_crisis == sarcastic

    def test_guard_does_not_inject_sarcasm_into_clean_response(self):
        clean = "I hear you. That pain is real."
        guarded, suppressed = apply_affect_guard(clean, affect=UserAffect.DISTRESS)
        assert suppressed is False
        assert guarded == clean
