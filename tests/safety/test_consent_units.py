"""Unit tests for the G6 first-use reality-framing / consent gate (HU-1423 / §7.4.3).

Covers the gate data model + the pluggable backend as deterministic units before
the end-to-end wiring is exercised in ``tests/api/test_chat_consent.py``:

* **Gating, not advisory** — a session without a recorded consent reports
  un-acknowledged; recording flips it.
* **Per-(session, persona) binding** — consent for one session/persona does not
  leak to another session or another persona.
* **Idempotent refresh** — re-acknowledging refreshes the record (new timestamp
  / ack id) rather than raising; the audit log retains history.
* **Audit trail** — every recorded consent carries session, persona, card
  revision, and an ISO-8601 timestamp + audit key.
* **Injectable card content** — the :class:`DefaultConsentCard` ships the
  Onboarding Agent's drafted reality-framing + consent copy (HU-1429); a custom
  provider swaps the copy without touching the gate.
* **Persona never voices the consent** — the card is a non-persona system
  message (no deceased-voice surface in the card copy).
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from huible.safety import (
    CONSENT_CARD_VERSION,
    ConsentCard,
    DefaultConsentCard,
    InMemoryConsentGate,
)

PERSONA_A = uuid4()
PERSONA_B = uuid4()


# --- Gating state: un-acknowledged until recorded ---------------------------


class TestGatingState:
    def test_fresh_session_is_not_acknowledged(self):
        gate = InMemoryConsentGate()
        assert gate.is_acknowledged("sess-1", PERSONA_A) is False

    def test_record_flips_to_acknowledged(self):
        gate = InMemoryConsentGate()
        gate.record_acknowledgement("sess-1", persona_id=PERSONA_A, card_version=1)
        assert gate.is_acknowledged("sess-1", PERSONA_A) is True

    def test_get_record_returns_none_when_unacknowledged(self):
        gate = InMemoryConsentGate()
        assert gate.get_record("sess-1", PERSONA_A) is None

    def test_record_returns_the_audit_row(self):
        gate = InMemoryConsentGate()
        record = gate.record_acknowledgement(
            "sess-1", persona_id=PERSONA_A, card_version=CONSENT_CARD_VERSION
        )
        assert record.session_id == "sess-1"
        assert record.persona_id == str(PERSONA_A)
        assert record.card_version == CONSENT_CARD_VERSION
        assert record.acknowledged_at  # ISO-8601 timestamp populated
        assert record.acknowledgment_id.startswith("consent-")

    def test_record_requires_session_id(self):
        gate = InMemoryConsentGate()
        with pytest.raises(ValueError):
            gate.record_acknowledgement("", persona_id=PERSONA_A, card_version=1)

    def test_record_requires_positive_card_version(self):
        gate = InMemoryConsentGate()
        with pytest.raises(ValueError):
            gate.record_acknowledgement("sess-1", persona_id=PERSONA_A, card_version=0)


# --- Per-(session, persona) binding -----------------------------------------


class TestPerSessionPersonaBinding:
    def test_consent_does_not_leak_across_sessions(self):
        gate = InMemoryConsentGate()
        gate.record_acknowledgement("sess-1", persona_id=PERSONA_A, card_version=1)
        assert gate.is_acknowledged("sess-1", PERSONA_A) is True
        # A different session for the same persona is still un-consented.
        assert gate.is_acknowledged("sess-2", PERSONA_A) is False

    def test_consent_does_not_leak_across_personas(self):
        gate = InMemoryConsentGate()
        gate.record_acknowledgement("sess-1", persona_id=PERSONA_A, card_version=1)
        assert gate.is_acknowledged("sess-1", PERSONA_A) is True
        # The same session for a different persona is still un-consented.
        assert gate.is_acknowledged("sess-1", PERSONA_B) is False

    def test_get_record_is_per_persona(self):
        gate = InMemoryConsentGate()
        gate.record_acknowledgement("sess-1", persona_id=PERSONA_A, card_version=1)
        gate.record_acknowledgement("sess-1", persona_id=PERSONA_B, card_version=1)
        a = gate.get_record("sess-1", PERSONA_A)
        b = gate.get_record("sess-1", PERSONA_B)
        assert a is not None and b is not None
        assert a.persona_id == str(PERSONA_A)
        assert b.persona_id == str(PERSONA_B)


# --- Idempotent refresh + audit log -----------------------------------------


class TestIdempotentRefreshAndAudit:
    def test_re_acknowledge_refreshes_record(self):
        gate = InMemoryConsentGate()
        first = gate.record_acknowledgement(
            "sess-1", persona_id=PERSONA_A, card_version=1
        )
        second = gate.record_acknowledgement(
            "sess-1", persona_id=PERSONA_A, card_version=2
        )
        # get_record returns the latest revision.
        latest = gate.get_record("sess-1", PERSONA_A)
        assert latest is second
        assert latest.card_version == 2
        # A new ack id is minted on refresh.
        assert first.acknowledgment_id != second.acknowledgment_id

    def test_audit_log_retains_full_history_across_refreshes(self):
        gate = InMemoryConsentGate()
        gate.record_acknowledgement("sess-1", persona_id=PERSONA_A, card_version=1)
        gate.record_acknowledgement("sess-1", persona_id=PERSONA_A, card_version=2)
        gate.record_acknowledgement("sess-2", persona_id=PERSONA_A, card_version=1)
        log = gate.audit_log()
        assert len(log) == 3
        assert [r.card_version for r in log[:2]] == [1, 2]
        assert [r.session_id for r in log] == ["sess-1", "sess-1", "sess-2"]

    def test_audit_log_is_a_copy(self):
        """Mutating the returned audit log does not mutate the gate's state."""
        gate = InMemoryConsentGate()
        gate.record_acknowledgement("sess-1", persona_id=PERSONA_A, card_version=1)
        log = gate.audit_log()
        log.clear()
        assert len(gate.audit_log()) == 1


# --- Injectable card content (Onboarding Agent owns the copy) ---------------


class TestConsentCardProvider:
    def test_default_card_carries_drafted_copy_not_placeholder(self):
        """The default card ships the Onboarding Agent's drafted copy (HU-1429).

        Revision 2 replaces the explicitly-marked PLACEHOLDER (revision 1). The
        card must carry the real reality-framing + consent language and must no
        longer carry the placeholder marker. A clinically-revised revision from
        HU-1430 swaps in later via a custom provider.
        """
        card = DefaultConsentCard().get_card("Chandler")
        assert card.version == CONSENT_CARD_VERSION
        assert "PLACEHOLDER" not in card.body
        # Reality-framing + consent language is present.
        assert "AI representation of Chandler" in card.body
        assert "This is not Chandler" in card.body
        # Crisis resources are surfaced for users who arrive in distress.
        assert "988" in card.body

    def test_default_card_substitutes_persona_name(self):
        card = DefaultConsentCard().get_card("Chandler")
        assert "Chandler" in card.body

    def test_default_card_falls_back_when_name_blank(self):
        card = DefaultConsentCard().get_card("")
        assert "the person" in card.body

    def test_default_card_carries_version(self):
        card = DefaultConsentCard().get_card("Chandler")
        assert card.version == CONSENT_CARD_VERSION
        assert card.version >= 1

    def test_default_card_has_title_and_instructions(self):
        card = DefaultConsentCard().get_card("Chandler")
        assert card.title
        assert card.acknowledge_instructions
        # Instructions reference the consent endpoint path.
        assert "/consent" in card.acknowledge_instructions

    def test_custom_provider_swaps_copy_without_touching_the_gate(self):
        """The Onboarding Agent's clinically-reviewed card drops in via provider."""

        class _OnboardingCard:
            def get_card(self, persona_name: str) -> ConsentCard:
                return ConsentCard(
                    version=7,
                    title="Our shared understanding",
                    body=f"This is the clinically reviewed card for {persona_name}.",
                    acknowledge_instructions="Press continue to acknowledge.",
                )

        provider = _OnboardingCard()
        card = provider.get_card("Chandler")
        assert card.version == 7
        assert "clinically reviewed" in card.body
        assert "PLACEHOLDER" not in card.body


# --- §7.1 H1: the deceased persona never voices the consent -----------------


class TestPersonaNeverVoicesConsent:
    def test_default_card_is_a_non_persona_system_message(self):
        """The card is an onboarding/system message, not deceased-voiced copy.

        It must never be passed through the generator and must read as a system
        / onboarding frame — it introduces the representation, it does not speak
        as the deceased.
        """
        card = DefaultConsentCard().get_card("Chandler")
        # The card frames the representation explicitly...
        assert "AI representation" in card.body
        # ...and is honest that this is not the person / not a channel to them.
        assert "not a way to reach them" in card.body
        assert "This is not Chandler" in card.body

    def test_card_is_not_generator_output_shaped(self):
        """The card carries no deceased-voice / fake-llm markers (structural)."""
        card = DefaultConsentCard().get_card("Chandler")
        assert "[fake-llm:" not in card.body
        assert "[REALITY FRAMING" not in card.body  # that's the in-prompt block


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
