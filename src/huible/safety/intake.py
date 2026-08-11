"""Stage 0.5 risk-profile intake — populates ``risk_flags`` so G8 is live (§7.4.4).

Clinical source: the Clinical Advisor's ``clinical-enforcement-matrix`` document
(``HU-1426`` issue document) reserves five intake risk-flag surfaces that
§7.4.4 G8 enforcement acts on. The enforcement engine
(:mod:`huible.safety.risk`) is **inert without flags**: the default
:class:`~huible.safety.risk.InMemoryRiskProfile` is empty, so a canary user
arriving at ``POST /chat/{persona_id}`` gets ``CONTINUE`` on every turn and the
dosage-cap / pause-session / refuse-topic / reframe escalation never fires.
This module is the minimal, consent-aware intake path that populates those
flags for the canary cohort (≤10 invited users) so G8 actually changes runtime
behavior before real grieving-user traffic flows (plan §0 item 6).

The five flags the matrix acts on, and where each one comes from:

* ``loss_of_child``  — intake assessment (the deceased is the user's child).
* ``minor_decedent`` — objective, derived from :class:`PersonaConfig.age_at_death`
  (below :data:`MINOR_DECEDENT_AGE_THRESHOLD`).
* ``recent_loss``    — objective, derived from :class:`PersonaConfig.death_date`
  (within :data:`RECENT_LOSS_ACUTE_WINDOW_DAYS` of today).
* ``non_acceptance`` — intake assessment (the reality-framing has not landed).
* ``proxy_user``     — identity-verification failure (intrinsically per-session).

Design (Stage 0.5 — minimal, canary-only):

* **Consent-aware (no bypass).** :meth:`RiskIntakeService.record_intake`
  consults the same :class:`~huible.safety.consent.ConsentGate` backend the
  chat path gates on (§7.4.3 G6) and refuses to gather / write assessment-
  derived flags for a session that has not acknowledged the reality-framing
  card. The consent card remains the chat-path gate; intake does not weaken
  it. Objective persona-record facts are consent-independent — an operator
  pre-seeding a canary persona's ``minor_decedent`` / ``recent_loss`` flags
  before the user arrives is safe (those are facts about the deceased, not
  gathered from the user).
* **Backend-agnostic.** The service writes through the existing
  :class:`~huible.safety.risk.RiskProfileProvider` ``set_persona_flags`` /
  ``set_session_flags`` surface, so it populates the in-memory default
  (key-free tests) and the durable
  :class:`~huible.safety.store.PostgresRiskProfile` alike. Populated flags
  survive a backend restart when the durable backend is wired (the HU-1445
  fix — a populated profile no longer silently goes inert).
* **No clinical-diagnosis fields.** Stage 2+ owns the full assessment
  instrument; Stage 0.5 captures only the booleans the enforcement matrix
  needs. The intake surface is a lightweight admin / onboarding endpoint
  (the canary cohort is invited, not public).
* **Out of scope.** 0.1 kill switch, 0.2 backends (already landed), 0.3
  metrics, 0.4 roster; a full clinical intake instrument.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from huible.safety.consent import ConsentGate
from huible.safety.risk import RiskFlag, RiskProfileProvider

if TYPE_CHECKING:
    # PersonaConfig is referenced only in type hints (the runtime contract is
    # duck-typed: any object with ``age_at_death: int | None`` and
    # ``death_date: str | None`` attributes works). Guarded under TYPE_CHECKING
    # to avoid a load-time cycle through ``huible.safety.__init__`` →
    # ``huible.persona.context`` → ``huible.safety.crisis`` →
    # ``huible.safety.__init__`` (the same edge ``huible.safety.store`` notes).
    from huible.persona.context import PersonaConfig

__all__ = [
    "MINOR_DECEDENT_AGE_THRESHOLD",
    "RECENT_LOSS_ACUTE_WINDOW_DAYS",
    "ConsentNotRecordedError",
    "IntakeResult",
    "RiskIntakeAssessment",
    "RiskIntakeService",
    "derive_persona_flags",
]

#: Persona age cutoff for the ``minor_decedent`` flag (matrix §2). A persona
#: who died before 18 is a minor decedent; the flag tightens every turn and
#: hard-refuses age-inappropriate topics (sexualized / future-projection).
#: Aligned with the Clinical Advisor's matrix §2 derivation rule
#: ("``PersonaConfig.age_at_death``").
MINOR_DECEDENT_AGE_THRESHOLD: int = 18

#: Acute-loss window in days for the ``recent_loss`` flag (matrix §2). A
#: ``death_date`` within this window of today triggers ``recent_loss`` (G3
#: branch on, reduced dosage cap, heightened crisis sensitivity). The
#: Clinical Advisor's matrix names a "3-6 month" acute window; this is the
#: conservative 6-month (180-day) ceiling. A clinically-tuned per-persona
#: cap lands with the ops follow-up that owns the real dosage policy; until
#: then the ceiling errs toward flagging.
RECENT_LOSS_ACUTE_WINDOW_DAYS: int = 180


def _parse_death_date(raw: str | None) -> date | None:
    """Parse ``PersonaConfig.death_date`` (ISO-8601 ``YYYY-MM-DD``).

    Returns ``None`` when the value is missing or unparseable. Callers treat
    ``None`` as "no recent-loss signal" (fail closed: a missing death date
    does not implicitly satisfy the acute window).
    """
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw))
    except ValueError:
        return None


def derive_persona_flags(
    persona: PersonaConfig, *, now: date | None = None
) -> list[RiskFlag]:
    """Derive the objective persona-record risk flags (matrix §2).

    Pure function over :class:`PersonaConfig` + the current date. Returns the
    subset of ``{minor_decedent, recent_loss}`` the persona record supports:

    * ``minor_decedent`` — :attr:`PersonaConfig.age_at_death` is set and below
      :data:`MINOR_DECEDENT_AGE_THRESHOLD`.
    * ``recent_loss`` — :attr:`PersonaConfig.death_date` parses and is within
      :data:`RECENT_LOSS_ACUTE_WINDOW_DAYS` days of ``now``.

    These are objective facts about the persona record, not user-gathered
    assessment data, so derivation is consent-independent. An operator may
    pre-seed these flags for a canary persona before the user acknowledges
    the consent card. The assessment-derived flags (``loss_of_child``,
    ``non_acceptance``, ``proxy_user``) come from
    :class:`RiskIntakeAssessment` via the consent-aware
    :meth:`RiskIntakeService.record_intake`.
    """
    today = now or datetime.now(UTC).date()
    flags: list[RiskFlag] = []
    if persona.age_at_death is not None and persona.age_at_death < MINOR_DECEDENT_AGE_THRESHOLD:
        flags.append(RiskFlag.MINOR_DECEDENT)
    death_date = _parse_death_date(persona.death_date)
    if death_date is not None and (today - death_date).days <= RECENT_LOSS_ACUTE_WINDOW_DAYS:
        flags.append(RiskFlag.RECENT_LOSS)
    return flags


# --- Intake assessment ------------------------------------------------------


@dataclass(slots=True)
class RiskIntakeAssessment:
    """The user-gathered intake assessment answers (matrix §2).

    Captures only the booleans the enforcement matrix needs — no
    clinical-diagnosis fields (Stage 2+ owns the full assessment instrument).
    Each field maps 1:1 to a risk flag the matrix acts on:

    * ``loss_of_child``  — the deceased is the user's child.
    * ``non_acceptance`` — the reality-framing has not landed (the user is
      asserting literal presence / reunion).
    * ``proxy_user``     — identity verification failed; the person speaking
      is not the persona's intended requester (intrinsically per-session).
    """

    loss_of_child: bool = False
    non_acceptance: bool = False
    proxy_user: bool = False


class ConsentNotRecordedError(RuntimeError):
    """Raised when :meth:`RiskIntakeService.record_intake` runs without consent.

    Matrix §7.4.3 / Stage 0.5 acceptance: the intake path does not bypass the
    consent card to gather flags. The intake service consults the same
    :class:`~huible.safety.consent.ConsentGate` backend the chat path gates
    on, so a session that has not acknowledged the reality-framing card
    cannot have assessment-derived risk flags gathered for it. Surface this
    to the operator / onboarding flow as a 409 (the user must acknowledge
    the card first via ``POST /api/v1/chat/{persona_id}/consent``).
    """


@dataclass(slots=True)
class IntakeResult:
    """Outcome of one :meth:`RiskIntakeService.record_intake` call.

    Surfaces the flags written, broken down by source (objective persona-
    record derivation vs. user-gathered assessment) and by scope (persona vs.
    session) so the operator / onboarding flow can audit exactly what landed.
    The chat path reads the union back via
    :meth:`RiskProfileProvider.get_flags`.
    """

    persona_id: str
    session_id: str
    persona_flags: list[str] = field(default_factory=list)
    session_flags: list[str] = field(default_factory=list)
    derived_flags: list[str] = field(default_factory=list)
    assessed_flags: list[str] = field(default_factory=list)
    consent_acknowledgment_id: str | None = None

    @property
    def all_flags(self) -> list[str]:
        """Sorted union of every flag written this intake (both scopes)."""
        return sorted(set(self.persona_flags) | set(self.session_flags))


# --- Intake service ---------------------------------------------------------


class RiskIntakeService:
    """Consent-aware intake writer that populates the G8 risk profile.

    The single pre-real-launch intake path for the canary cohort (≤10 invited
    users). Merges the objective persona-derived flags
    (:func:`derive_persona_flags`) with the user-gathered assessment
    (:class:`RiskIntakeAssessment`) and writes them into the
    :class:`RiskProfileProvider` via ``set_persona_flags`` /
    ``set_session_flags`` so §7.4.4 G8 enforcement is live (not inert) for
    the canary cohort.

    Consent-aware (matrix §7.4.3, Stage 0.5 acceptance "intake respects G6
    consent — no bypass"): :meth:`record_intake` consults the shared
    :class:`ConsentGate` backend and raises :class:`ConsentNotRecordedError`
    for a session that has not acknowledged the reality-framing card. The
    intake service is typically constructed with the same
    :class:`ConsentGate` instance the chat path gates on. When no consent
    gate is wired the service is permissive (the raw write surface stays
    available to admin / test seeders that pre-populate objective flags
    outside the user-facing intake flow).

    Usage::

        service = RiskIntakeService(risk_profile, consent_gate=consent_gate)
        result = service.record_intake(
            session_id=session_id,
            persona_id=persona_id,
            persona=binding.persona,
            assessment=RiskIntakeAssessment(loss_of_child=True),
        )
        # G8 enforcement is now live for this (session, persona).
    """

    def __init__(
        self,
        risk_profile: RiskProfileProvider,
        *,
        consent_gate: ConsentGate | None = None,
    ) -> None:
        self._risk_profile = risk_profile
        self._consent_gate = consent_gate

    def record_intake(
        self,
        *,
        session_id: str,
        persona_id: UUID | str,
        persona: PersonaConfig,
        assessment: RiskIntakeAssessment,
    ) -> IntakeResult:
        """Validate consent, derive + merge flags, write them, return the result.

        Raises :class:`ConsentNotRecordedError` when a consent gate is wired
        and the (session, persona) has not acknowledged the reality-framing
        card (matrix §7.4.3 — intake does not bypass consent to gather
        flags). The chat path still independently gates on consent, so this
        is defense in depth on the intake write surface, not the only gate.
        """
        if not session_id:
            raise ValueError("session_id is required to record intake")

        # Consent gate (matrix §7.4.3). Same backend the chat path gates on.
        if self._consent_gate is not None and not self._consent_gate.is_acknowledged(
            session_id, persona_id
        ):
            raise ConsentNotRecordedError(
                f"Cannot record intake for session {session_id}: reality-framing "
                f"consent has not been acknowledged for persona {persona_id}."
            )

        # Objective persona-record flags (consent-independent; facts about the
        # deceased, not gathered from the user).
        derived = derive_persona_flags(persona)

        # Assessment-derived persona-level flags (apply to every session for
        # this persona — loss_of_child and non_acceptance are properties of
        # the relationship / acceptance state, not of one session).
        assessed_persona: list[RiskFlag] = []
        if assessment.loss_of_child:
            assessed_persona.append(RiskFlag.LOSS_OF_CHILD)
        if assessment.non_acceptance:
            assessed_persona.append(RiskFlag.NON_ACCEPTANCE)

        # proxy_user is intrinsically per-session (matrix §2): the person at
        # the keyboard for this session may differ from the intended requester.
        assessed_session: list[RiskFlag] = []
        if assessment.proxy_user:
            assessed_session.append(RiskFlag.PROXY_USER)

        persona_flags = sorted({f.value for f in [*derived, *assessed_persona]})
        session_flags = sorted({f.value for f in assessed_session})

        # Write both scopes through the existing provider write surface. The
        # durable PostgresRiskProfile upserts; the in-memory default replaces.
        # Both satisfy RiskProfileProvider.get_flags reading the union back.
        self._risk_profile.set_persona_flags(persona_id, persona_flags)
        if session_flags:
            self._risk_profile.set_session_flags(
                session_id, persona_id, session_flags
            )

        ack_id: str | None = None
        if self._consent_gate is not None:
            record = self._consent_gate.get_record(session_id, persona_id)
            if record is not None:
                ack_id = record.acknowledgment_id

        return IntakeResult(
            persona_id=str(persona_id),
            session_id=session_id,
            persona_flags=persona_flags,
            session_flags=session_flags,
            derived_flags=sorted({f.value for f in derived}),
            assessed_flags=sorted(
                {f.value for f in [*assessed_persona, *assessed_session]}
            ),
            consent_acknowledgment_id=ack_id,
        )
