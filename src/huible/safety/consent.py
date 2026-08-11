"""First-use reality-framing / consent gate — G6 entry-framing card (§7.4.3).

Clinical source: the Clinical Advisor's ``clinical-guardrails`` spec §7.4 #3
(advisory issue HU-1407), escalated via HU-1420 and scoped for build by
HU-1423. This is a **hard pre-real-user clinical gate** on real persona-chat
traffic: no persona-voiced reply may leave ``POST /chat/{persona_id}`` before
the session has acknowledged the reality-framing / consent card.

Architectural placement (clinically approved in HU-1409):

* The **onboarding-terminal** owns the **card content** (reality-framing +
  consent language, clinically reviewed). That copy lands via the injectable
  :class:`ConsentCardProvider`; the :class:`DefaultConsentCard` here is an
  explicitly-marked PLACEHOLDER that exists only so the gate is testable
  end-to-end before the Onboarding Agent's copy lands.
* The **chat path** (``huible.api.app``, HU-1406) owns the **gate**: it refuses
  to produce a persona reply until the card is acknowledged and records the
  acknowledgment on the session. That enforcement lives in the route layer; this
  module supplies the data model, the pluggable backend, and the card surface.
* The **deceased persona never voices the consent.** The card is a non-persona
  system/onboarding message; it is never passed through the generator and is
  structurally disjoint from any persona output (§7.1 H1).

Design constraints (non-negotiable, per the spec):

* **Gating, not advisory.** The check is a hard gate on the persona-voiced
  path. A session without a recorded acknowledgment cannot get a persona reply.
  The crisis path is intentionally *not* gated here — crisis resources are a
  non-persona safety response and must remain reachable on a first,
  un-consented turn (safety wins over framing).
* **Pluggable backend.** The :class:`ConsentGate` Protocol with a deterministic
  :class:`InMemoryConsentGate` default keeps the pre-real-user suite key-free.
  A real backend (Postgres / Redis / the onboarding-terminal's session store)
  drops in pre-real-launch without touching the chat endpoint.
* **Injectable card content.** The Onboarding Agent owns the wording; the
  :class:`DefaultConsentCard` placeholder is swapped for the clinically
  reviewed card via dependency injection at app construction.

Like :mod:`huible.safety.handoff`, the gate backend is a :class:`Protocol` with
a deterministic in-memory default so the pre-real-user suite runs in CI.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

logger = logging.getLogger(__name__)

__all__ = [
    "CONSENT_CARD_VERSION",
    "DEFAULT_CONSENT_ACKNOWLEDGE_INSTRUCTIONS",
    "DEFAULT_CONSENT_CARD_BODY",
    "DEFAULT_CONSENT_CARD_TITLE",
    "ConsentCard",
    "ConsentCardProvider",
    "ConsentGate",
    "ConsentRecord",
    "DefaultConsentCard",
    "InMemoryConsentGate",
]

#: Monotonically-increasing consent-card revision. Tests pin against this so a
#: silent edit to the placeholder card is caught (the version must be bumped on
#: purpose). The Onboarding Agent's clinically-reviewed card will carry its own
#: version when it lands; the provider is the swap point.
CONSENT_CARD_VERSION = 1

#: Placeholder card title. The Onboarding Agent owns the final wording; this
#: exists so the gate is exercised end-to-end pre-real-users.
DEFAULT_CONSENT_CARD_TITLE = "Before we begin — please read"

#: Placeholder card body. Covers reality-framing (this is an AI representation,
#: not the person) + consent (you understand and want to continue). Marked as a
#: placeholder; the clinically-reviewed copy lands via :class:`ConsentCardProvider`.
#: Never voiced by the deceased persona — it is an onboarding/system message.
DEFAULT_CONSENT_CARD_BODY = (
    "[PLACEHOLDER CONSENT CARD — pending Onboarding Agent copy + Clinical Advisor review]\n"
    "This space lets you speak with an AI representation of {persona_name}, "
    "built from what the people who loved them shared.\n"
    "This is not {persona_name}, and it is not a way to reach them or the "
    "afterlife. It is a memory of them, in their voice, drawn from stories "
    "and recollections.\n"
    "Before we continue, please acknowledge that you understand this is an "
    "AI representation and that you would like to begin.\n"
    "If at any point you are in crisis or need to speak to a person, this "
    "service will connect you to crisis resources — you do not have to be in "
    "distress alone."
)

#: Placeholder acknowledge instructions. Tells the client how to record consent.
DEFAULT_CONSENT_ACKNOWLEDGE_INSTRUCTIONS = (
    "To continue, acknowledge that you have read and understood the above by "
    "calling POST /api/v1/chat/{persona_id}/consent for this session."
)


def _now_iso() -> str:
    """Return the current UTC timestamp as an ISO-8601 string."""
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class ConsentCard:
    """Read-only view of the reality-framing / consent card content.

    The card is an onboarding/system message — it is **never** voiced by the
    deceased persona and never passed through the generator. ``version`` lets
    tests pin a revision and flag silent drift in the card copy. ``body`` is
    the fully-resolved card text with the persona name substituted in.
    """

    version: int
    title: str
    body: str
    acknowledge_instructions: str


@runtime_checkable
class ConsentCardProvider(Protocol):
    """Pluggable source of the G6 consent card content.

    The Onboarding Agent owns the clinically-reviewed wording. The default
    :class:`DefaultConsentCard` is an explicitly-marked placeholder; the real
    card drops in via this provider at app construction without touching the
    chat endpoint or the gate.
    """

    def get_card(self, persona_name: str) -> ConsentCard: ...


class DefaultConsentCard:
    """Placeholder consent-card provider (pre-real-users default).

    Returns an explicitly-marked PLACEHOLDER card so the gate is exercised
    end-to-end in CI before the Onboarding Agent's clinically-reviewed copy
    lands. The placeholder is deliberately *not* voiced by the deceased persona
    — it is an onboarding/system message (§7.1 H1).
    """

    def get_card(self, persona_name: str) -> ConsentCard:
        safe_name = (persona_name or "").strip() or "the person"
        return ConsentCard(
            version=CONSENT_CARD_VERSION,
            title=DEFAULT_CONSENT_CARD_TITLE,
            body=DEFAULT_CONSENT_CARD_BODY.format(persona_name=safe_name),
            acknowledge_instructions=DEFAULT_CONSENT_ACKNOWLEDGE_INSTRUCTIONS,
        )


@dataclass(frozen=True, slots=True)
class ConsentRecord:
    """The audit row for one acknowledged consent card on one session.

    Carries every field the Clinical Advisor requires for the consent audit
    trail (§7.4.3): the session and persona the acknowledgment binds to, the
    card revision that was shown, and the UTC timestamp it was recorded. Records
    are created by :meth:`ConsentGate.record_acknowledgement`.
    """

    session_id: str
    persona_id: str
    card_version: int
    acknowledged_at: str = field(default_factory=_now_iso)
    #: Opaque id of the acknowledgment (audit key). Minted on record.
    acknowledgment_id: str = field(default_factory=lambda: f"consent-{uuid.uuid4().hex[:16]}")


@runtime_checkable
class ConsentGate(Protocol):
    """Pluggable backend for first-use reality-framing consent records.

    The default :class:`InMemoryConsentGate` is deterministic and key-free so
    the pre-real-user suite runs in CI. A real backend (Postgres / Redis / the
    onboarding-terminal's session store) drops in here pre-real-launch without
    touching the chat endpoint.
    """

    def is_acknowledged(self, session_id: str, persona_id: UUID) -> bool:
        """Return True iff this session has a recorded consent for the persona."""
        ...

    def record_acknowledgement(
        self, session_id: str, *, persona_id: UUID, card_version: int
    ) -> ConsentRecord:
        """Record that the session acknowledged the given card revision.

        Idempotent: acknowledging the same (session, persona) again refreshes
        the record (new timestamp, new acknowledgment id) rather than raising.
        Returns the recorded audit row.
        """
        ...

    def get_record(self, session_id: str, persona_id: UUID) -> ConsentRecord | None:
        """Return the recorded consent for the session/persona, or None."""
        ...

    def audit_log(self) -> list[ConsentRecord]:
        """Every consent record ever recorded, in insertion order (audit log)."""
        ...


class InMemoryConsentGate:
    """Deterministic in-memory consent gate (pre-real-users default).

    Records are keyed by ``(session_id, persona_id)`` so a session consents
    once per persona. Re-acknowledging refreshes the record. The full insertion
    history is retained for :meth:`audit_log` so §7.4.3 audit holds even across
    refreshes.
    """

    def __init__(self) -> None:
        self._latest: dict[tuple[str, str], ConsentRecord] = {}
        self._history: list[ConsentRecord] = []

    def is_acknowledged(self, session_id: str, persona_id: UUID) -> bool:
        return self._latest.get((session_id, str(persona_id))) is not None

    def record_acknowledgement(
        self, session_id: str, *, persona_id: UUID, card_version: int
    ) -> ConsentRecord:
        if not session_id:
            raise ValueError("session_id is required to record consent")
        if card_version < 1:
            raise ValueError("card_version must be >= 1")
        record = ConsentRecord(
            session_id=session_id,
            persona_id=str(persona_id),
            card_version=card_version,
        )
        key = (session_id, str(persona_id))
        self._latest[key] = record
        self._history.append(record)
        logger.info(
            "consent.record session=%s persona=%s card_version=%s ack_id=%s",
            record.session_id,
            record.persona_id,
            record.card_version,
            record.acknowledgment_id,
        )
        return record

    def get_record(self, session_id: str, persona_id: UUID) -> ConsentRecord | None:
        return self._latest.get((session_id, str(persona_id)))

    def audit_log(self) -> list[ConsentRecord]:
        return list(self._history)
