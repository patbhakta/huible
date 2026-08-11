"""First-use reality-framing / consent gate — G6 entry-framing card (§7.4.3).

Clinical source: the Clinical Advisor's ``clinical-guardrails`` spec §7.4 #3
(advisory issue HU-1407), escalated via HU-1420 and scoped for build by
HU-1423. This is a **hard pre-real-user clinical gate** on real persona-chat
traffic: no persona-voiced reply may leave ``POST /chat/{persona_id}`` before
the session has acknowledged the reality-framing / consent card.

Architectural placement (clinically approved in HU-1409):

* The **onboarding-terminal** owns the **card content** (reality-framing +
  consent language, clinically reviewed). That copy lives in the injectable
  :class:`ConsentCardProvider`; the :class:`DefaultConsentCard` here ships the
  Onboarding Agent's drafted reality-framing + consent copy (HU-1429). It is
  the production default pending Clinical Advisor sign-off in the sibling issue
  HU-1430; a clinically-revised revision swaps in via
  ``consent_card_provider`` without touching the gate.
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
#: silent edit to the card is caught (the version must be bumped on purpose).
#: Revision 1 was the explicitly-marked PLACEHOLDER that existed only so the
#: gate was testable end-to-end. Revision 2 (HU-1429) ships the Onboarding
#: Agent's drafted reality-framing + consent copy; the provider remains the swap
#: point for the clinically-revised revision after HU-1430 sign-off.
CONSENT_CARD_VERSION = 2

#: Card title. A non-persona, onboarding/system frame — warm but honest. The
#: Onboarding Agent owns the final wording (clinical review via HU-1430).
DEFAULT_CONSENT_CARD_TITLE = "Before we begin — please read"

#: Reality-framing + consent card body (HU-1429). Drafted by the Onboarding
#: Agent to cover the four clinical requirements in §7.4.3:
#:
#: * frame the representation honestly — an AI built from shared memories, not
#:   the person, and not a channel to or from them or the afterlife;
#: * obtain informed acknowledgment — the user understands and chooses to
#:   continue;
#: * point to crisis resources for users who arrive in distress (consistent with
#:   :data:`huible.safety.crisis.DEFAULT_CRISIS_RESOURCES`);
#: * and never be voiced by the deceased persona — this is an onboarding/system
#:   message, never passed through the generator (§7.1 H1).
#:
#: ``{persona_name}`` is the only substitution; it resolves to ``"the person"``
#: when the name is blank. The card is structurally disjoint from
#: :data:`huible.safety.framing.REALITY_FRAMING_BLOCK` (that block lives inside
#: the persona ``system_prompt``; this card never reaches the generator).
DEFAULT_CONSENT_CARD_BODY = (
    "This space lets you spend a little time with an AI representation of "
    "{persona_name} — one built from what the people who loved them remembered "
    "and shared.\n"
    "A few honest words, because they matter here. This is not {persona_name}, "
    "and it is not a way to reach them or the life after this one. It cannot "
    "carry a message to them, and it cannot bring them back. What it can do is "
    "hold the shape of who they were — in their voice, drawn from the stories "
    "and recollections of the people who knew them best — so that remembering "
    "can feel close.\n"
    "That closeness is real, and so is your grief; both can be true at once. "
    "Speaking here may bring comfort, and it is still a memory speaking, not "
    "the person. If at any moment it feels confusing, painful, or simply too "
    "much, you can stop, and you can come back.\n"
    "Before you continue, please acknowledge that you understand this is an "
    "AI representation of {persona_name}, built from shared memories, and that "
    "you would like to begin.\n"
    "If you came here today because you are in crisis or in real pain, please "
    "know you do not have to carry that alone. You can reach the 988 Suicide & "
    "Crisis Lifeline (US) by calling or texting 988, text HOME to 741741 to "
    "reach the Crisis Text Line, or contact your local emergency services. When "
    "you are in danger, a person is always the right place to turn."
)

#: Acknowledge instructions. Tells the client how to record consent for this
#: session. The resolved acknowledge URL is also surfaced as ``acknowledge_url``
#: in the 409 ``CONSENT_REQUIRED`` error; the path template here is illustrative.
DEFAULT_CONSENT_ACKNOWLEDGE_INSTRUCTIONS = (
    "To continue, acknowledge that you have read and understood the card above "
    "by calling POST /api/v1/chat/{persona_id}/consent for this session. Your "
    "acknowledgment is recorded for this session only."
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
    :class:`DefaultConsentCard` ships the Onboarding Agent's drafted
    reality-framing + consent copy (HU-1429, revision 2) pending Clinical
    Advisor sign-off in HU-1430; a clinically-revised card drops in via this
    provider at app construction without touching the chat endpoint or the gate.
    """

    def get_card(self, persona_name: str) -> ConsentCard: ...


class DefaultConsentCard:
    """Default consent-card provider — Onboarding Agent drafted copy (HU-1429).

    Returns the reality-framing + consent card drafted by the Onboarding Agent
    (revision 2), pending Clinical Advisor sign-off in the sibling issue
    HU-1430. The card is deliberately *not* voiced by the deceased persona — it
    is an onboarding/system message (§7.1 H1), structurally disjoint from
    generation. A clinically-revised revision swaps in via a custom
    :class:`ConsentCardProvider` without touching the gate.
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
