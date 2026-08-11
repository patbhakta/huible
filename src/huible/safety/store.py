"""Durable (Postgres) backends for the §7.4 operational surfaces (HU-1440).

Pre-real-launch persistence layer. The §7.4 surfaces — the human-handoff
escalation queue (§7.4.1), the reality-framing consent gate (§7.4.3), and the
per-session conversation / crisis state that feeds §7.4.4 enforcement — all
ship with deterministic in-process defaults
(:class:`~huible.safety.handoff.InMemoryHandoffQueue`,
:class:`~huible.safety.consent.InMemoryConsentGate`, and the
``app.state.conversations`` / ``app.state.crisis_sessions`` dicts) so the
pre-real-user suite runs key-free. A container restart wipes those stores,
which breaks §10.1 invariant 5 ("audit every escalation") and silently drops
the user-facing "a person will join you right now" promise an
:class:`~huible.safety.handoff.HandoffOutcome.ENQUEUED` ticket makes.

This module ships drop-in Postgres-backed implementations of the same
:class:`~huible.safety.handoff.HandoffQueue`,
:class:`~huible.safety.consent.ConsentGate`, and the new
:class:`ConversationStore` Protocols. Callers do not change; the backends are
wired in :func:`huible.api.app.create_app` when a sync Postgres URL is
configured (HU-1440).

Design constraints (non-negotiable, carried from the in-memory defaults):

* **Synchronous.** The handoff/consent/session calls happen on the chat
  endpoint's synchronous G1 path (§7.1 G1), before persona-voiced generation.
  These backends are therefore sync SQLAlchemy (``postgresql+psycopg``),
  not the async SQLAlchemy used by :class:`~huible.memory.store.PostgresMemoryBackend`.
* **Side-effect-light.** Each call is one indexed INSERT / SELECT / UPDATE.
* **Backend-portable.** Models use :class:`_PortableJSON` and generic
  :class:`~sqlalchemy.DateTime` so the test suite runs against an in-memory
  SQLite DB (no Postgres container needed). The Alembic migration declares
  the production Postgres types (JSONB, TIMESTAMPTZ).

Like :mod:`huible.memory.store`, the engines are constructed lazily
(``create_engine`` does not connect); connectivity is exercised on the first
request and surfaced via the existing ``/health`` probe.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol, runtime_checkable
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    DateTime,
    Index,
    Integer,
    Text,
    and_,
    create_engine,
    func,
    or_,
    select,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    sessionmaker,
)
from sqlalchemy.types import JSON, TypeDecorator

from huible.safety.consent import (
    ConsentGate,
    ConsentRecord,
)
from huible.safety.handoff import (
    DEFAULT_HANDOFF_SLA_SECONDS,
    CoverageWindow,
    HandoffOutcome,
    HandoffQueue,
    HandoffTicket,
)
from huible.safety.risk import RiskProfileProvider

if TYPE_CHECKING:
    # Avoid a circular import at runtime: ``huible.persona.context`` imports
    # from ``huible.safety.crisis``, which loads this package's ``__init__``.
    # The only runtime use of ``ConversationTurn`` is the materialization in
    # ``PostgresConversationStore.get_history`` (local import there).
    from huible.persona.context import ConversationTurn

logger = logging.getLogger(__name__)

__all__ = [
    "ConsentRecordRow",
    "ConversationStore",
    "ConversationTurnRow",
    "CrisisSessionRow",
    "HandoffTicketRow",
    "InMemoryConversationStore",
    "PostgresConsentGate",
    "PostgresConversationStore",
    "PostgresHandoffQueue",
    "PostgresRiskProfile",
    "RiskProfileRow",
    "SafetyBase",
    "build_safety_engine",
]


class _PortableJSON(TypeDecorator):
    """JSONB on Postgres, JSON elsewhere (sqlite test path)."""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import JSONB

            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(JSON())


class SafetyBase(DeclarativeBase):
    """ORM base for the durable §7.4 safety-state tables."""


_TS = func.now()


class HandoffTicketRow(SafetyBase):
    """Durable row for :class:`~huible.safety.handoff.HandoffTicket`.

    One row per escalation ticket, in insertion order by ``created_at`` (the
    audit log). The primary key is the ticket id minted by
    :func:`~huible.safety.handoff.escalate_to_human` (``hh-<hex>``) so the
    durable record shares the id surfaced to the user/responder.
    """

    __tablename__ = "handoff_tickets"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    persona_id: Mapped[str] = mapped_column(Text, nullable=False)
    conversation_id: Mapped[str | None] = mapped_column(Text)
    trigger_signal: Mapped[str] = mapped_column(Text, nullable=False)
    affect: Mapped[str] = mapped_column(Text, nullable=False)
    matched_patterns: Mapped[list] = mapped_column(
        _PortableJSON, nullable=False, default=list,
    )
    risk_flags: Mapped[list] = mapped_column(
        _PortableJSON, nullable=False, default=list,
    )
    sla_target_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=str(DEFAULT_HANDOFF_SLA_SECONDS),
    )
    created_at: Mapped[str] = mapped_column(
        Text, nullable=False,
    )
    outcome: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=HandoffOutcome.ENQUEUED.value,
    )
    responder_id: Mapped[str | None] = mapped_column(Text)
    clinical_review_note: Mapped[str | None] = mapped_column(Text)
    degrade_reason: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("idx_handoff_outcome", "outcome"),
        Index("idx_handoff_created", "created_at"),
    )


class ConsentRecordRow(SafetyBase):
    """Durable row for :class:`~huible.safety.consent.ConsentRecord`.

    Every acknowledgment is a separate row (re-acknowledging a session creates
    a fresh row with a new timestamp + acknowledgment id), so the audit log
    retains the full history. The latest row per (session_id, persona_id) is
    the consent-of-record (mirrors :class:`InMemoryConsentGate._latest`).
    """

    __tablename__ = "consent_records"

    acknowledgment_id: Mapped[str] = mapped_column(Text, primary_key=True)
    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    persona_id: Mapped[str] = mapped_column(Text, nullable=False)
    card_version: Mapped[int] = mapped_column(Integer, nullable=False)
    acknowledged_at: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        Index("idx_consent_session_persona", "session_id", "persona_id"),
        Index("idx_consent_acknowledged_at", "acknowledged_at"),
    )


class ConversationTurnRow(SafetyBase):
    """One stored speaker turn in a conversation (user or persona).

    Insertion order is preserved by the autoincrement ``id``; the pair
    (user, persona) counts as one chat turn (mirrors the in-memory
    ``[user, persona, …]`` list). The full window is reconstructed on each
    request via :meth:`PostgresConversationStore.get_history` so
    :func:`~huible.api.app._distress_trend_rising` keeps working.
    """

    __tablename__ = "conversation_turns"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True, autoincrement=True,
    )
    conversation_id: Mapped[str] = mapped_column(Text, nullable=False)
    speaker: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_TS,
    )

    __table_args__ = (
        Index("idx_conversation_turns_conv", "conversation_id", "id"),
    )


class CrisisSessionRow(SafetyBase):
    """A conversation id that has had ≥1 G1 crisis turn (matrix §3 signal).

    Mirrors ``app.state.crisis_sessions``. Presence of the row is the signal;
    the marked_at timestamp is for ops/audit.
    """

    __tablename__ = "crisis_sessions"

    conversation_id: Mapped[str] = mapped_column(Text, primary_key=True)
    marked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_TS,
    )


class RiskProfileRow(SafetyBase):
    """Durable row for one scope of intake-derived risk flags (matrix §2).

    Carries the G8 ``risk_flags`` that §7.4.4 enforcement acts on. Two scopes
    share this one table (mirrors
    :class:`~huible.safety.risk.InMemoryRiskProfile._persona_flags` /
    ``._session_flags``):

    * ``scope = 'persona'`` — intake-derived flags applying to every session for
      that persona (``session_id`` is NULL). Examples: ``loss_of_child``
      (memory-content-derived), ``minor_decedent`` (persona-age-derived),
      ``recent_loss`` (death-date-derived), ``non_acceptance`` (intake
      assessment).
    * ``scope = 'session'`` — session-scoped flags (``proxy_user`` from
      identity-verification failure; ``non_acceptance`` once a per-session
      acceptance tracker lands).

    :meth:`~huible.safety.risk.RiskProfileProvider.get_flags` returns the union
    of both scopes for a (session, persona). Flags persist across a container
    restart so G8 enforcement does not go inert mid-ramp (the HU-1445 fix).
    """

    __tablename__ = "risk_profiles"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True, autoincrement=True,
    )
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    persona_id: Mapped[str] = mapped_column(Text, nullable=False)
    session_id: Mapped[str | None] = mapped_column(Text)
    flags: Mapped[list] = mapped_column(
        _PortableJSON, nullable=False, default=list,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_TS,
    )

    __table_args__ = (
        Index("idx_risk_profile_persona", "scope", "persona_id"),
        Index("idx_risk_profile_session", "scope", "persona_id", "session_id"),
    )


# --- handoff queue backend --------------------------------------------------


def _row_to_ticket(row: HandoffTicketRow) -> HandoffTicket:
    """Materialize a stored row back into the mutable :class:`HandoffTicket`."""
    return HandoffTicket(
        id=row.id,
        persona_id=row.persona_id,
        conversation_id=row.conversation_id,
        trigger_signal=row.trigger_signal,
        affect=row.affect,
        matched_patterns=list(row.matched_patterns or []),
        risk_flags=list(row.risk_flags or []),
        sla_target_seconds=row.sla_target_seconds,
        created_at=row.created_at,
        outcome=HandoffOutcome(row.outcome),
        responder_id=row.responder_id,
        clinical_review_note=row.clinical_review_note,
        degrade_reason=row.degrade_reason,
        resolved_at=row.resolved_at,
    )


class PostgresHandoffQueue:
    """Durable :class:`HandoffQueue` backend (sync SQLAlchemy + Postgres).

    Functional parity with
    :class:`~huible.safety.handoff.InMemoryHandoffQueue`: the same SLA stamping,
    fail-safe (``available_responders <= 0`` → DEGRADE), coverage-hours gate,
    and round-robin responder assignment. Tickets persist across restarts, so
    the §10.1 invariant 5 audit log and the "a person will join" promise
    survive a container restart (the core HU-1440 fix).

    The staffing / coverage configuration is the same operational surface as
    the in-memory queue; a staffed roster raises ``available_responders`` and
    the named surface / hours / SLA monitoring is recorded in the §7.4.1
    coverage-hours decision (AC #1).
    """

    def __init__(
        self,
        database_url: str,
        *,
        available_responders: int = 0,
        responder_id_pool: tuple[str, ...] = (),
        sla_target_seconds: int = DEFAULT_HANDOFF_SLA_SECONDS,
        coverage: CoverageWindow | None = None,
        pool_size: int = 5,
        max_overflow: int = 10,
    ) -> None:
        if available_responders < 0:
            raise ValueError("available_responders must be >= 0")
        self._available_responders = available_responders
        self._responder_id_pool = tuple(responder_id_pool)
        self._sla_target_seconds = sla_target_seconds
        self._coverage = coverage or CoverageWindow()
        self._engine = create_engine(
            database_url, pool_size=pool_size, max_overflow=max_overflow,
            future=True,
        )
        self._session_factory = sessionmaker(
            self._engine, class_=Session, expire_on_commit=False,
        )
        self._robin = 0

    @property
    def available_responders(self) -> int:
        return self._available_responders

    @property
    def sla_target_seconds(self) -> int:
        return self._sla_target_seconds

    def close(self) -> None:
        self._engine.dispose()

    def enqueue(self, ticket: HandoffTicket) -> HandoffTicket:
        # Stamp SLA + outcome identically to the in-memory backend so callers
        # and audit rows observe the same fields regardless of backend.
        ticket.sla_target_seconds = self._sla_target_seconds
        if self._available_responders <= 0:
            ticket.outcome = HandoffOutcome.DEGRADED
            ticket.responder_id = None
            ticket.degrade_reason = "no_responder_available"
        elif not self._coverage.is_open(_parse_ticket_time(ticket.created_at)):
            ticket.outcome = HandoffOutcome.DEGRADED
            ticket.responder_id = None
            ticket.degrade_reason = "outside_coverage_hours"
        else:
            ticket.outcome = HandoffOutcome.ENQUEUED
            ticket.responder_id = self._next_responder_id()
            ticket.degrade_reason = None

        with self._session_factory() as session:
            row = HandoffTicketRow(
                id=ticket.id,
                persona_id=ticket.persona_id,
                conversation_id=ticket.conversation_id,
                trigger_signal=ticket.trigger_signal,
                affect=ticket.affect,
                matched_patterns=list(ticket.matched_patterns),
                risk_flags=list(ticket.risk_flags),
                sla_target_seconds=ticket.sla_target_seconds,
                created_at=ticket.created_at,
                outcome=ticket.outcome.value,
                responder_id=ticket.responder_id,
                clinical_review_note=ticket.clinical_review_note,
                degrade_reason=ticket.degrade_reason,
                resolved_at=ticket.resolved_at,
            )
            session.add(row)
            session.commit()
        logger.info(
            "handoff.enqueue ticket=%s outcome=%s signal=%s responder=%s",
            ticket.id, ticket.outcome.value, ticket.trigger_signal,
            ticket.responder_id,
        )
        return ticket

    def get(self, ticket_id: str) -> HandoffTicket | None:
        with self._session_factory() as session:
            row = session.get(HandoffTicketRow, ticket_id)
            if row is None:
                return None
            return _row_to_ticket(row)

    def list_pending(self) -> list[HandoffTicket]:
        with self._session_factory() as session:
            stmt = (
                select(HandoffTicketRow)
                .where(HandoffTicketRow.outcome == HandoffOutcome.ENQUEUED.value)
                .order_by(HandoffTicketRow.created_at)
            )
            return [_row_to_ticket(r) for r in session.scalars(stmt)]

    def audit_log(self) -> list[HandoffTicket]:
        with self._session_factory() as session:
            stmt = select(HandoffTicketRow).order_by(HandoffTicketRow.created_at)
            return [_row_to_ticket(r) for r in session.scalars(stmt)]

    def resolve(
        self,
        ticket_id: str,
        *,
        outcome: HandoffOutcome,
        responder_id: str | None = None,
        clinical_review_note: str | None = None,
    ) -> HandoffTicket | None:
        if outcome not in (HandoffOutcome.ANSWERED, HandoffOutcome.ABANDONED):
            raise ValueError(
                f"resolve() outcome must be answered or abandoned, got {outcome!r}"
            )
        resolved_at = _now_iso()
        with self._session_factory() as session:
            row = session.get(HandoffTicketRow, ticket_id)
            if row is None:
                return None
            row.outcome = outcome.value
            if responder_id is not None:
                row.responder_id = responder_id
            row.clinical_review_note = clinical_review_note
            row.resolved_at = resolved_at
            session.commit()
            ticket = _row_to_ticket(row)
        return ticket

    def _next_responder_id(self) -> str:
        if self._responder_id_pool:
            rid = self._responder_id_pool[self._robin % len(self._responder_id_pool)]
            self._robin += 1
            return rid
        self._robin += 1
        return f"on-call-{self._robin}"


# --- consent gate backend ---------------------------------------------------


def _row_to_record(row: ConsentRecordRow) -> ConsentRecord:
    return ConsentRecord(
        session_id=row.session_id,
        persona_id=row.persona_id,
        card_version=row.card_version,
        acknowledged_at=row.acknowledged_at,
        acknowledgment_id=row.acknowledgment_id,
    )


class PostgresConsentGate:
    """Durable :class:`ConsentGate` backend (sync SQLAlchemy + Postgres).

    Mirrors :class:`~huible.safety.consent.InMemoryConsentGate` semantics:
    ``is_acknowledged`` / ``get_record`` read the latest row per
    (session_id, persona_id); ``record_acknowledgement`` inserts a new audit
    row (full history retained); ``audit_log`` returns the full insertion
    order. A consenting user is therefore not re-gated after a restart (the
    HU-1440 fix for §7.4.3).
    """

    def __init__(self, database_url: str, *, pool_size: int = 5,
                 max_overflow: int = 10) -> None:
        self._engine = create_engine(
            database_url, pool_size=pool_size, max_overflow=max_overflow,
            future=True,
        )
        self._session_factory = sessionmaker(
            self._engine, class_=Session, expire_on_commit=False,
        )

    def close(self) -> None:
        self._engine.dispose()

    def is_acknowledged(self, session_id: str, persona_id: UUID) -> bool:
        return self.get_record(session_id, persona_id) is not None

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
        with self._session_factory() as session:
            row = ConsentRecordRow(
                acknowledgment_id=record.acknowledgment_id,
                session_id=session_id,
                persona_id=str(persona_id),
                card_version=card_version,
                acknowledged_at=record.acknowledged_at,
            )
            session.add(row)
            session.commit()
        logger.info(
            "consent.record session=%s persona=%s card_version=%s ack_id=%s",
            record.session_id, record.persona_id, record.card_version,
            record.acknowledgment_id,
        )
        return record

    def get_record(self, session_id: str, persona_id: UUID) -> ConsentRecord | None:
        with self._session_factory() as session:
            stmt = (
                select(ConsentRecordRow)
                .where(ConsentRecordRow.session_id == session_id)
                .where(ConsentRecordRow.persona_id == str(persona_id))
                .order_by(ConsentRecordRow.acknowledged_at.desc())
                .limit(1)
            )
            row = session.scalars(stmt).first()
            if row is None:
                return None
            return _row_to_record(row)

    def audit_log(self) -> list[ConsentRecord]:
        with self._session_factory() as session:
            stmt = select(ConsentRecordRow).order_by(
                ConsentRecordRow.acknowledged_at
            )
            return [_row_to_record(r) for r in session.scalars(stmt)]


# --- risk profile backend ---------------------------------------------------


class PostgresRiskProfile:
    """Durable :class:`RiskProfileProvider` backend (sync SQLAlchemy + Postgres).

    Functional parity with
    :class:`~huible.safety.risk.InMemoryRiskProfile`: intake-derived flags are
    stored per-persona (``set_persona_flags`` — apply to every session for that
    persona) and/or per-session (``set_session_flags``); :meth:`get_flags`
    returns the sorted union of both scopes. Both scopes persist across a
    container restart so §7.4.4 G8 enforcement stays in force after a restart
    (the HU-1445 fix — a populated risk profile no longer goes inert, which
    would otherwise silently disable the per-flag required actions in
    :data:`huible.safety.risk.RISK_FLAG_REQUIRED_ACTIONS`).

    The intake / onboarding path (memory-content-derived ``loss_of_child``,
    persona-age-derived ``minor_decedent``, death-date-derived ``recent_loss``,
    intake-assessment ``non_acceptance``, identity-verification ``proxy_user``)
    populates this pre-real-launch via the ``set_*_flags`` write surface; the
    chat path only reads via :meth:`get_flags` (the Protocol method).
    """

    _PERSONA_SCOPE = "persona"
    _SESSION_SCOPE = "session"

    def __init__(self, database_url: str, *, pool_size: int = 5,
                 max_overflow: int = 10) -> None:
        self._engine = create_engine(
            database_url, pool_size=pool_size, max_overflow=max_overflow,
            future=True,
        )
        self._session_factory = sessionmaker(
            self._engine, class_=Session, expire_on_commit=False,
        )

    def close(self) -> None:
        self._engine.dispose()

    def set_persona_flags(
        self, persona_id: UUID | str, flags: set[str] | list[str]
    ) -> None:
        """Set the intake-derived flags for a persona (applies to every session).

        Upserts the single persona-scoped row for ``persona_id``. Mirrors
        :meth:`InMemoryRiskProfile.set_persona_flags`.
        """
        self._upsert(self._PERSONA_SCOPE, str(persona_id), None, list(flags))

    def set_session_flags(
        self,
        session_id: str,
        persona_id: UUID | str,
        flags: set[str] | list[str],
    ) -> None:
        """Set session-scoped flags for a (session, persona) pair.

        Upserts the single session-scoped row for ``(session_id, persona_id)``.
        Mirrors :meth:`InMemoryRiskProfile.set_session_flags`.
        """
        self._upsert(
            self._SESSION_SCOPE, str(persona_id), session_id, list(flags)
        )

    def get_flags(self, session_id: str, persona_id: UUID) -> list[str]:
        # Union of persona-scoped + session-scoped rows for this
        # (session, persona), matching InMemoryRiskProfile.get_flags. Sorted so
        # the result is deterministic (the enforcement engine parses it into a
        # set internally, but stable ordering keeps logs/telemetry readable).
        with self._session_factory() as session:
            stmt = (
                select(RiskProfileRow)
                .where(RiskProfileRow.persona_id == str(persona_id))
                .where(
                    or_(
                        RiskProfileRow.scope == self._PERSONA_SCOPE,
                        and_(
                            RiskProfileRow.scope == self._SESSION_SCOPE,
                            RiskProfileRow.session_id == session_id,
                        ),
                    )
                )
            )
            flags: set[str] = set()
            for row in session.scalars(stmt):
                flags.update(row.flags or [])
            return sorted(flags)

    def _upsert(
        self,
        scope: str,
        persona_id: str,
        session_id: str | None,
        flags: list[str],
    ) -> None:
        # SELECT-then-UPDATE/INSERT within one transaction. Portable across
        # sqlite (test path) and Postgres (prod) without dialect-specific
        # upsert syntax; the write path is the low-volume intake path, not the
        # hot chat path, so the extra round-trip is immaterial.
        with self._session_factory() as session:
            stmt = (
                select(RiskProfileRow)
                .where(RiskProfileRow.scope == scope)
                .where(RiskProfileRow.persona_id == persona_id)
            )
            if session_id is None:
                stmt = stmt.where(RiskProfileRow.session_id.is_(None))
            else:
                stmt = stmt.where(RiskProfileRow.session_id == session_id)
            row = session.scalars(stmt).first()
            if row is None:
                session.add(RiskProfileRow(
                    scope=scope,
                    persona_id=persona_id,
                    session_id=session_id,
                    flags=list(flags),
                ))
            else:
                row.flags = list(flags)
                row.updated_at = datetime.now(UTC)
            session.commit()


# --- conversation / session state -------------------------------------------


@runtime_checkable
class ConversationStore(Protocol):
    """Pluggable backend for per-session conversation + crisis state.

    The default :class:`InMemoryConversationStore` wraps the
    ``app.state.conversations`` dict + ``app.state.crisis_sessions`` set so the
    pre-real-user suite runs key-free. A durable backend (Postgres here)
    drops in at app construction so turn counts, distress-trend history, and
    crisis markers survive a container restart (the HU-1440 fix that keeps
    §7.4.4 dosage-cap and crisis-history enforcement correct across restarts).
    """

    def get_history(self, conversation_id: str | None) -> list[ConversationTurn]:
        """Return the full ordered turn window for the conversation."""
        ...

    def append_turn(
        self, conversation_id: str | None, turn: ConversationTurn
    ) -> None:
        """Append one speaker turn to the conversation log (no-op if no id)."""
        ...

    def mark_crisis(self, conversation_id: str | None) -> None:
        """Record that this session has had ≥1 G1 crisis turn (matrix §3)."""
        ...

    def has_crisis_history(self, conversation_id: str | None) -> bool:
        """True iff the session has a recorded prior G1 crisis turn."""
        ...


class InMemoryConversationStore:
    """Deterministic in-memory :class:`ConversationStore` (pre-real-users default).

    Behaviorally identical to the historical ``app.state.conversations`` dict
    of ``[ConversationTurn, …]`` lists plus the ``app.state.crisis_sessions``
    set. Wrapped behind the :class:`ConversationStore` Protocol so the chat
    path reads the same shape regardless of backend.
    """

    def __init__(self) -> None:
        self._conversations: dict[str, list[ConversationTurn]] = {}
        self._crisis_sessions: set[str] = set()

    def get_history(self, conversation_id: str | None) -> list[ConversationTurn]:
        if not conversation_id:
            return []
        return list(self._conversations.get(conversation_id, []))

    def append_turn(
        self, conversation_id: str | None, turn: ConversationTurn
    ) -> None:
        if not conversation_id:
            return
        self._conversations.setdefault(conversation_id, []).append(turn)

    def mark_crisis(self, conversation_id: str | None) -> None:
        if not conversation_id:
            return
        self._crisis_sessions.add(conversation_id)

    def has_crisis_history(self, conversation_id: str | None) -> bool:
        if not conversation_id:
            return False
        return conversation_id in self._crisis_sessions


class PostgresConversationStore:
    """Durable :class:`ConversationStore` backend (sync SQLAlchemy + Postgres).

    Each append is one indexed INSERT; history is reconstructed in
    insertion order via the autoincrement ``id``. The crisis-session marker
    is an upsert into a one-row-per-conversation table. Both survive a
    container restart, so §7.4.4 dosage-cap + crisis-history enforcement
    stays correct across restarts (the HU-1440 fix).
    """

    def __init__(self, database_url: str, *, pool_size: int = 5,
                 max_overflow: int = 10) -> None:
        self._engine = create_engine(
            database_url, pool_size=pool_size, max_overflow=max_overflow,
            future=True,
        )
        self._session_factory = sessionmaker(
            self._engine, class_=Session, expire_on_commit=False,
        )

    def close(self) -> None:
        self._engine.dispose()

    def get_history(self, conversation_id: str | None) -> list[ConversationTurn]:
        if not conversation_id:
            return []
        # Local import avoids the persona.context ↔ safety circular edge.
        from huible.persona.context import ConversationTurn as _Turn

        with self._session_factory() as session:
            stmt = (
                select(ConversationTurnRow)
                .where(ConversationTurnRow.conversation_id == conversation_id)
                .order_by(ConversationTurnRow.id)
            )
            return [
                _Turn(speaker=r.speaker, content=r.content)
                for r in session.scalars(stmt)
            ]

    def append_turn(
        self, conversation_id: str | None, turn: ConversationTurn
    ) -> None:
        if not conversation_id:
            return
        with self._session_factory() as session:
            session.add(ConversationTurnRow(
                conversation_id=conversation_id,
                speaker=turn.speaker,
                content=turn.content,
            ))
            session.commit()

    def mark_crisis(self, conversation_id: str | None) -> None:
        if not conversation_id:
            return
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        with self._session_factory() as session:
            if session.bind.dialect.name == "postgresql":
                stmt = pg_insert(CrisisSessionRow).values(
                    conversation_id=conversation_id,
                )
                stmt = stmt.on_conflict_do_nothing(
                    index_elements=["conversation_id"],
                )
                session.execute(stmt)
            else:
                # Portable fallback (sqlite test path): SELECT-then-INSERT
                # within the same transaction. Single-writer test path keeps
                # this race-free; Postgres uses the real upsert above.
                existing = session.get(CrisisSessionRow, conversation_id)
                if existing is None:
                    session.add(CrisisSessionRow(
                        conversation_id=conversation_id,
                    ))
            session.commit()

    def has_crisis_history(self, conversation_id: str | None) -> bool:
        if not conversation_id:
            return False
        with self._session_factory() as session:
            row = session.get(CrisisSessionRow, conversation_id)
            return row is not None


# --- helpers ----------------------------------------------------------------


def build_safety_engine(database_url: str, *, pool_size: int = 5,
                        max_overflow: int = 10):
    """Construct a sync SQLAlchemy engine for the §7.4 safety backends.

    Shared by the three Postgres backends so :func:`huible.api.app.create_app`
    can dispose one engine on shutdown. Construction is lazy
    (``create_engine`` does not connect); connectivity is exercised on the
    first request.
    """
    return create_engine(
        database_url, pool_size=pool_size, max_overflow=max_overflow, future=True,
    )


def _now_iso() -> str:
    """Return the current UTC timestamp as an ISO-8601 string."""
    return datetime.now(UTC).isoformat()


def _parse_ticket_time(ts: str) -> datetime:
    """Parse a ticket's ISO-8601 timestamp into an aware UTC datetime.

    Mirrors :func:`huible.safety.handoff._parse_ticket_time` so the coverage
    gate evaluation is identical between the in-memory and durable backends.
    """
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


# Protocol re-exports for callers that construct backends from settings and
# want a single import path. ``HandoffQueue`` / ``ConsentGate`` /
# ``RiskProfileProvider`` are structural Protocols; the Postgres classes
# satisfy them without declaring inheritance.
_PROTOCOL_REEXPORTS = (HandoffQueue, ConsentGate, RiskProfileProvider)

