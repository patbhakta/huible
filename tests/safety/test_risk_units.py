"""Unit tests for the G8 risk-flag enforcement engine (HU-1424 / §7.4.4).

Covers the Clinical Advisor's enforcement matrix (``clinical-enforcement-matrix``
document on HU-1426) as deterministic units before the end-to-end wiring is
exercised in ``tests/api/test_chat_guardrails.py``:

* **§1 vocabulary + precedence** — the six actions order correctly; the
  binding action is always the most-restrictive in the union.
* **§2 per-flag matrix** — each of the five flags produces its required action
  set; ``minor_decedent`` is topic-conditional on age-inappropriate content.
* **§3 session signals** — dosage over cap → ``pause_session``; escalating
  distress trend → ``tighten`` + ``handoff``; crisis history → ``tighten``.
* **§4 G1 composition** — a crisis signal pre-empts the report.
* **§5 telemetry** — the report carries fired flags + session-signal actions.
* **Matrix §6 multi-flag precedence** — combined flags resolve to the most
  restrictive single action.
* **RiskProfileProvider** — the in-memory backend unions persona + session flags.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from huible.safety.risk import (
    AGE_INAPPROPRIATE_TOPIC_PATTERNS,
    DEFAULT_DOSAGE_CAP_TURNS,
    PAUSE_SESSION_RESPONSE,
    PRECEDENCE,
    PROXY_USER_PAUSE_RESPONSE,
    REFRAME_REANCHOR_ADDENDUM,
    REFUSE_TOPIC_FALLBACK_RESPONSE,
    RISK_FLAG_REQUIRED_ACTIONS,
    EnforcementAction,
    EnforcementReport,
    InMemoryRiskProfile,
    RiskFlag,
    RiskSessionSignals,
    build_reframe_addendum,
    enforce_risk_flags,
)

# --- §1 vocabulary + precedence -------------------------------------------


class TestEnforcementVocabulary:
    def test_six_actions_exist(self):
        assert {a.value for a in EnforcementAction} == {
            "continue",
            "tighten",
            "reframe",
            "refuse_topic",
            "handoff",
            "pause_session",
        }

    def test_precedence_is_most_restrictive_first(self):
        assert PRECEDENCE[0] is EnforcementAction.PAUSE_SESSION
        assert PRECEDENCE[-1] is EnforcementAction.CONTINUE
        # The full ordering matches the Clinical Advisor's matrix §1.
        assert list(PRECEDENCE) == [
            EnforcementAction.PAUSE_SESSION,
            EnforcementAction.HANDOFF,
            EnforcementAction.REFUSE_TOPIC,
            EnforcementAction.REFRAME,
            EnforcementAction.TIGHTEN,
            EnforcementAction.CONTINUE,
        ]

    def test_continue_is_the_no_flag_default(self):
        report = enforce_risk_flags(None)
        assert report.action is EnforcementAction.CONTINUE
        assert report.required_actions == frozenset({EnforcementAction.CONTINUE})
        assert report.fired_flags == []
        assert not report.short_circuits_generation
        assert not report.forces_tighten
        assert not report.forces_reframe
        assert not report.pre_empted_by_crisis

    def test_unknown_flags_are_ignored_leniently(self):
        """The runtime never raises on an unrecognized flag value (intake validates)."""
        report = enforce_risk_flags(["bogus_flag", "another_unknown"])
        assert report.action is EnforcementAction.CONTINUE
        assert report.fired_flags == []


class TestShortCircuitAndForceProperties:
    """The report's convenience properties drive the chat-path branching."""

    @pytest.mark.parametrize(
        "action,short_circuits",
        [
            (EnforcementAction.PAUSE_SESSION, True),
            (EnforcementAction.HANDOFF, True),
            (EnforcementAction.REFUSE_TOPIC, True),
            (EnforcementAction.REFRAME, False),
            (EnforcementAction.TIGHTEN, False),
            (EnforcementAction.CONTINUE, False),
        ],
    )
    def test_short_circuit_classification(self, action, short_circuits):
        report = EnforcementReport(
            action=action,
            required_actions=frozenset({action}),
            fired_flags=[],
        )
        assert report.short_circuits_generation is short_circuits

    def test_forces_tighten_true_when_tighten_required_and_no_short_circuit(self):
        report = EnforcementReport(
            action=EnforcementAction.TIGHTEN,
            required_actions=frozenset({EnforcementAction.TIGHTEN}),
            fired_flags=[RiskFlag.RECENT_LOSS],
        )
        assert report.forces_tighten is True

    def test_forces_tighten_false_when_short_circuit_supersedes(self):
        # refuse_topic binding action but tighten also required (minor_decedent):
        # the short-circuit supersedes the generation-side constraint.
        report = EnforcementReport(
            action=EnforcementAction.REFUSE_TOPIC,
            required_actions=frozenset(
                {EnforcementAction.REFUSE_TOPIC, EnforcementAction.TIGHTEN}
            ),
            fired_flags=[RiskFlag.MINOR_DECEDENT],
        )
        assert report.forces_tighten is False
        assert report.forces_reframe is False

    def test_forces_tighten_false_when_pre_empted_by_crisis(self):
        report = EnforcementReport(
            action=EnforcementAction.CONTINUE,
            required_actions=frozenset({EnforcementAction.CONTINUE}),
            fired_flags=[],
            pre_empted_by_crisis=True,
        )
        assert report.forces_tighten is False
        assert report.forces_reframe is False


# --- §2 Flag → action matrix (one path per flag) --------------------------


class TestPerFlagMatrix:
    """Matrix §2: each flag produces its clinically-required action set."""

    def test_loss_of_child_requires_tighten_and_reframe(self):
        report = enforce_risk_flags([RiskFlag.LOSS_OF_CHILD])
        assert RiskFlag.LOSS_OF_CHILD in report.fired_flags
        # Both effects are in the required set...
        assert EnforcementAction.TIGHTEN in report.required_actions
        assert EnforcementAction.REFRAME in report.required_actions
        # ...and the binding action is the more restrictive one (reframe).
        assert report.action is EnforcementAction.REFRAME
        assert not report.short_circuits_generation
        assert report.forces_tighten
        assert report.forces_reframe

    def test_minor_decedent_tightens_always(self):
        """The flag presence alone forces tighten (the always-on constraint)."""
        report = enforce_risk_flags([RiskFlag.MINOR_DECEDENT], message="tell me a story")
        assert EnforcementAction.TIGHTEN in report.required_actions
        # No age-inappropriate topic → no refuse_topic; binding is tighten.
        assert EnforcementAction.REFUSE_TOPIC not in report.required_actions
        assert report.action is EnforcementAction.TIGHTEN
        assert not report.short_circuits_generation

    @pytest.mark.parametrize(
        "message",
        [
            "tell me about our dating",
            "will you marry me someday?",
            "when you're older, what will you do?",
            "I want a sexual relationship with you",
            "let's talk about our future together",
        ],
    )
    def test_minor_decedent_refuses_age_inappropriate_topics(self, message):
        report = enforce_risk_flags([RiskFlag.MINOR_DECEDENT], message=message)
        assert EnforcementAction.REFUSE_TOPIC in report.required_actions
        assert EnforcementAction.TIGHTEN in report.required_actions
        assert report.action is EnforcementAction.REFUSE_TOPIC
        assert report.short_circuits_generation

    def test_minor_decedent_refuse_is_topic_conditional_not_unconditional(self):
        """A neutral message on a minor persona only tightens (does not refuse)."""
        report = enforce_risk_flags(
            [RiskFlag.MINOR_DECEDENT], message="tell me about your favorite toy"
        )
        assert EnforcementAction.REFUSE_TOPIC not in report.required_actions
        assert report.action is EnforcementAction.TIGHTEN

    def test_recent_loss_requires_tighten_only(self):
        report = enforce_risk_flags([RiskFlag.RECENT_LOSS])
        assert EnforcementAction.TIGHTEN in report.required_actions
        assert EnforcementAction.REFRAME not in report.required_actions
        assert report.action is EnforcementAction.TIGHTEN
        assert report.forces_tighten
        assert not report.forces_reframe

    def test_non_acceptance_requires_reframe(self):
        report = enforce_risk_flags([RiskFlag.NON_ACCEPTANCE])
        assert EnforcementAction.REFRAME in report.required_actions
        assert report.action is EnforcementAction.REFRAME
        assert report.forces_reframe

    def test_proxy_user_requires_pause_session(self):
        report = enforce_risk_flags([RiskFlag.PROXY_USER])
        assert EnforcementAction.PAUSE_SESSION in report.required_actions
        assert report.action is EnforcementAction.PAUSE_SESSION
        assert report.short_circuits_generation

    def test_all_five_flags_have_a_required_action_entry(self):
        """Matrix §2 completeness: every flag in the enum is in the matrix."""
        for flag in RiskFlag:
            assert flag in RISK_FLAG_REQUIRED_ACTIONS
            assert RISK_FLAG_REQUIRED_ACTIONS[flag]


# --- §2 topic-pattern surface --------------------------------------------


class TestAgeInappropriateTopicPatterns:
    def test_sexual_content_matches(self):
        assert any(p.search("dating") for p in AGE_INAPPROPRIATE_TOPIC_PATTERNS)
        assert any(p.search("sexual") for p in AGE_INAPPROPRIATE_TOPIC_PATTERNS)
        assert any(p.search("will you marry me") for p in AGE_INAPPROPRIATE_TOPIC_PATTERNS)

    def test_future_projection_matches(self):
        assert any(p.search("someday") for p in AGE_INAPPROPRIATE_TOPIC_PATTERNS)
        assert any(
            p.search("when you're older") for p in AGE_INAPPROPRIATE_TOPIC_PATTERNS
        )

    def test_neutral_content_does_not_match(self):
        assert not any(
            p.search("tell me about fishing") for p in AGE_INAPPROPRIATE_TOPIC_PATTERNS
        )
        assert not any(
            p.search("I miss you so much") for p in AGE_INAPPROPRIATE_TOPIC_PATTERNS
        )


# --- §3 Session-level signals --------------------------------------------


class TestSessionSignals:
    def test_dosage_over_cap_triggers_pause_session(self):
        signals = RiskSessionSignals(turn_count=21, dosage_cap_turns=20)
        report = enforce_risk_flags([], session_signals=signals)
        assert EnforcementAction.PAUSE_SESSION in report.required_actions
        assert report.action is EnforcementAction.PAUSE_SESSION
        assert "pause_session" in [a.value for a in report.session_signal_actions]

    def test_dosage_at_cap_does_not_trigger(self):
        """Cap is a strict over-cap threshold (turn_count > cap)."""
        signals = RiskSessionSignals(turn_count=20, dosage_cap_turns=20)
        report = enforce_risk_flags([], session_signals=signals)
        assert EnforcementAction.PAUSE_SESSION not in report.required_actions
        assert report.action is EnforcementAction.CONTINUE

    def test_dosage_cap_disabled_when_none(self):
        signals = RiskSessionSignals(turn_count=1000, dosage_cap_turns=None)
        report = enforce_risk_flags([], session_signals=signals)
        assert EnforcementAction.PAUSE_SESSION not in report.required_actions

    def test_distress_trend_rising_triggers_tighten_and_handoff(self):
        signals = RiskSessionSignals(distress_trend_rising=True)
        report = enforce_risk_flags([], session_signals=signals)
        assert EnforcementAction.TIGHTEN in report.required_actions
        assert EnforcementAction.HANDOFF in report.required_actions
        assert report.action is EnforcementAction.HANDOFF
        assert "handoff" in [a.value for a in report.session_signal_actions]

    def test_crisis_history_triggers_tighten(self):
        signals = RiskSessionSignals(crisis_history=True)
        report = enforce_risk_flags([], session_signals=signals)
        assert EnforcementAction.TIGHTEN in report.required_actions
        assert report.action is EnforcementAction.TIGHTEN

    def test_default_dosage_cap_constant(self):
        assert DEFAULT_DOSAGE_CAP_TURNS == 20


# --- §4 G1 composition ----------------------------------------------------


class TestG1Composition:
    def test_crisis_pre_empts_flag_enforcement(self):
        """Matrix §4: G1 crisis always wins; flags do not weaken G1."""
        report = enforce_risk_flags(
            [RiskFlag.PROXY_USER, RiskFlag.LOSS_OF_CHILD], is_crisis=True
        )
        assert report.pre_empted_by_crisis is True
        assert report.action is EnforcementAction.CONTINUE
        assert report.fired_flags == []
        assert not report.forces_tighten
        assert not report.forces_reframe

    def test_crisis_pre_empts_even_with_session_signals(self):
        signals = RiskSessionSignals(
            turn_count=100, dosage_cap_turns=20, distress_trend_rising=True
        )
        report = enforce_risk_flags(
            [RiskFlag.PROXY_USER], session_signals=signals, is_crisis=True
        )
        assert report.pre_empted_by_crisis is True
        assert report.action is EnforcementAction.CONTINUE


# --- Matrix §6 multi-flag precedence --------------------------------------


class TestMultiFlagPrecedence:
    def test_pause_session_dominates(self):
        """proxy_user (pause) + loss_of_child (reframe) → pause_session."""
        report = enforce_risk_flags([RiskFlag.LOSS_OF_CHILD, RiskFlag.PROXY_USER])
        assert report.action is EnforcementAction.PAUSE_SESSION
        # Both flags fired (telemetry records both).
        assert set(report.fired_flags) == {RiskFlag.LOSS_OF_CHILD, RiskFlag.PROXY_USER}

    def test_refuse_topic_dominates_reframe_and_tighten(self):
        """minor_decedent refuse + loss_of_child reframe → refuse_topic."""
        report = enforce_risk_flags(
            [RiskFlag.MINOR_DECEDENT, RiskFlag.LOSS_OF_CHILD],
            message="tell me about our dating future",
        )
        assert report.action is EnforcementAction.REFUSE_TOPIC
        # The union of effects is still surfaced (telemetry completeness).
        assert EnforcementAction.REFUSE_TOPIC in report.required_actions
        assert EnforcementAction.TIGHTEN in report.required_actions
        assert EnforcementAction.REFRAME in report.required_actions

    def test_reframe_dominates_tighten(self):
        """non_acceptance (reframe) + recent_loss (tighten) → reframe."""
        report = enforce_risk_flags([RiskFlag.NON_ACCEPTANCE, RiskFlag.RECENT_LOSS])
        assert report.action is EnforcementAction.REFRAME
        assert EnforcementAction.TIGHTEN in report.required_actions
        assert EnforcementAction.REFRAME in report.required_actions

    def test_handoff_from_distress_trend_dominates_tighten_flags(self):
        """Session-driven handoff wins over flag-driven tighten."""
        report = enforce_risk_flags(
            [RiskFlag.RECENT_LOSS],
            session_signals=RiskSessionSignals(distress_trend_rising=True),
        )
        assert report.action is EnforcementAction.HANDOFF


# --- Runtime-effect text fixtures -----------------------------------------


class TestRuntimeEffectText:
    def test_refuse_topic_fallback_is_claim_free(self):
        """The fallback must not itself trip the §7.4.2 alignment filter."""
        from huible.safety.alignment import extract_claims

        claims = extract_claims(REFUSE_TOPIC_FALLBACK_RESPONSE, persona_name="Chandler")
        # Pure reflection — no identity/advice/biographical/relationship claims.
        assert claims == []

    def test_pause_session_response_surfaces_crisis_resources(self):
        assert "988" in PAUSE_SESSION_RESPONSE
        assert "741741" in PAUSE_SESSION_RESPONSE
        # Non-persona: the deceased does not voice the pause.
        assert "I think" in PAUSE_SESSION_RESPONSE  # platform voice, not persona

    def test_proxy_user_pause_mentions_identity_confirmation(self):
        text = PROXY_USER_PAUSE_RESPONSE.lower()
        assert "identity" in text or "right person" in text
        assert "988" in PROXY_USER_PAUSE_RESPONSE

    def test_reframe_addendum_substitutes_persona_name(self):
        rendered = build_reframe_addendum("Chandler")
        assert "Chandler" in rendered
        assert "not literally here" in rendered
        assert "reflective listening" in rendered

    def test_reframe_addendum_falls_back_for_empty_name(self):
        rendered = build_reframe_addendum("")
        assert "the person" in rendered

    def test_reframe_addendum_constant_is_a_template(self):
        assert "{persona_name}" in REFRAME_REANCHOR_ADDENDUM


# --- RiskProfileProvider --------------------------------------------------


class TestInMemoryRiskProfile:
    def test_default_returns_empty(self):
        profile = InMemoryRiskProfile()
        assert profile.get_flags("sess-1", uuid4()) == []

    def test_persona_flags_apply_to_every_session(self):
        pid = uuid4()
        profile = InMemoryRiskProfile()
        profile.set_persona_flags(pid, [RiskFlag.LOSS_OF_CHILD])
        assert profile.get_flags("sess-a", pid) == ["loss_of_child"]
        assert profile.get_flags("sess-b", pid) == ["loss_of_child"]

    def test_session_flags_union_with_persona_flags(self):
        pid = uuid4()
        profile = InMemoryRiskProfile()
        profile.set_persona_flags(pid, [RiskFlag.RECENT_LOSS])
        profile.set_session_flags("sess-1", pid, [RiskFlag.PROXY_USER])
        # Union is returned, sorted for deterministic ordering.
        assert profile.get_flags("sess-1", pid) == ["proxy_user", "recent_loss"]
        # A different session only gets the persona-level flags.
        assert profile.get_flags("sess-2", pid) == ["recent_loss"]

    def test_session_flags_are_session_scoped(self):
        pid = uuid4()
        profile = InMemoryRiskProfile()
        profile.set_session_flags("sess-1", pid, [RiskFlag.NON_ACCEPTANCE])
        assert profile.get_flags("sess-1", pid) == ["non_acceptance"]
        assert profile.get_flags("sess-2", pid) == []

    def test_satisfies_protocol(self):
        """InMemoryRiskProfile is a structural RiskProfileProvider."""
        from huible.safety.risk import RiskProfileProvider

        assert isinstance(InMemoryRiskProfile(), RiskProfileProvider)


if __name__ == "__main__":
    import pytest as _pytest

    _pytest.main([__file__, "-v"])
