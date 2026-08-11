"""Durability tests for the §7.4 safety backends (HU-1440).

Verifies the core fix for the pre-real-launch persistence gap: the
handoff audit log, consent records, conversation turn history, and
crisis-session markers all survive a "container restart" (simulated by
constructing a fresh backend object against the same underlying database).

The Postgres backends are portable enough to run against an in-memory SQLite
engine (the ``_PortableJSON`` and ``BigInteger().with_variant(Integer, "sqlite")``
declarations keep the schema portable), so these tests run key-free in CI
without a Postgres container — the same strategy the memory-store suite uses
(``tests/test_store.py``). A shared file-engine keeps the rows visible across
"restart" (a second backend object).

Acceptance criteria covered (HU-1440):

* AC #1 — Postgres-backed HandoffQueue; ``audit_log`` survives restart.
* AC #2 — Postgres-backed ConsentGate; a consenting user is not re-gated.
* AC #3 — Conversation/session state (turn counts, crisis markers) survives.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from huible.api.app import create_app
from huible.api.auth import InMemoryApiKeyStore, InMemoryPersonaRegistry
from huible.api.settings import Settings
from huible.persona.context import ConversationTurn
from huible.safety.handoff import (
    DEFAULT_HANDOFF_SLA_SECONDS,
    CoverageWindow,
    HandoffOutcome,
    HandoffTicket,
)
from huible.safety.store import (
    PostgresConsentGate,
    PostgresConversationStore,
    PostgresHandoffQueue,
    SafetyBase,
)

# --- shared engine fixture --------------------------------------------------


@pytest.fixture
def safety_engine():
    """A fresh in-memory SQLite engine with the §7.4 tables created.

    A file-backed ``:memory:`` via ``StaticPool`` keeps the single in-memory
    database visible across separate connections (so a "restarted" backend
    object sees the rows the previous one wrote). Mirrors the
    ``tests/test_store.py`` pattern.
    """
    from sqlalchemy.pool import StaticPool

    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    SafetyBase.metadata.create_all(eng)
    yield eng
    eng.dispose()


def _handoff_queue(eng, **kwargs) -> PostgresHandoffQueue:
    q = PostgresHandoffQueue.__new__(PostgresHandoffQueue)
    q._available_responders = kwargs.get("available_responders", 2)
    q._responder_id_pool = kwargs.get("responder_id_pool", ("pat", "lee"))
    q._sla_target_seconds = kwargs.get("sla_target_seconds", DEFAULT_HANDOFF_SLA_SECONDS)
    q._coverage = kwargs.get("coverage", CoverageWindow())
    q._engine = eng
    q._session_factory = sessionmaker(eng, class_=Session, expire_on_commit=False)
    q._robin = 0
    return q


def _consent_gate(eng) -> PostgresConsentGate:
    g = PostgresConsentGate.__new__(PostgresConsentGate)
    g._engine = eng
    g._session_factory = sessionmaker(eng, class_=Session, expire_on_commit=False)
    return g


def _conversation_store(eng) -> PostgresConversationStore:
    s = PostgresConversationStore.__new__(PostgresConversationStore)
    s._engine = eng
    s._session_factory = sessionmaker(eng, class_=Session, expire_on_commit=False)
    return s


def _ticket(
    *, ticket_id: str = "hh-1", persona_id: str = "p1", conversation_id: str = "c1",
    outcome: HandoffOutcome = HandoffOutcome.ENQUEUED, age_seconds: int = 60,
) -> HandoffTicket:
    t = HandoffTicket(
        id=ticket_id,
        persona_id=persona_id,
        conversation_id=conversation_id,
        trigger_signal="crisis",
        affect="crisis",
        risk_flags=["recent_loss"],
        sla_target_seconds=DEFAULT_HANDOFF_SLA_SECONDS,
    )
    t.created_at = (datetime.now(UTC) - timedelta(seconds=age_seconds)).isoformat()
    t.outcome = outcome
    return t


# --- AC #1: handoff queue durability ---------------------------------------


class TestHandoffQueueDurability:
    def test_audit_log_survives_restart(self, safety_engine):
        q1 = _handoff_queue(safety_engine)
        q1.enqueue(_ticket(ticket_id="hh-a"))
        q1.enqueue(_ticket(ticket_id="hh-b"))

        # "Restart": a fresh backend object against the same DB.
        q2 = _handoff_queue(safety_engine)
        log = q2.audit_log()
        assert [t.id for t in log] == ["hh-a", "hh-b"]
        assert all(t.outcome is HandoffOutcome.ENQUEUED for t in log)
        assert all(t.sla_target_seconds == DEFAULT_HANDOFF_SLA_SECONDS for t in log)

    def test_get_after_restart(self, safety_engine):
        q1 = _handoff_queue(safety_engine)
        q1.enqueue(_ticket(ticket_id="hh-x"))
        q2 = _handoff_queue(safety_engine)
        got = q2.get("hh-x")
        assert got is not None
        assert got.trigger_signal == "crisis"
        assert got.risk_flags == ["recent_loss"]

    def test_pending_work_queue_survives_restart(self, safety_engine):
        """The responder work queue must not lose tickets across a restart.

        This is the life-safety consequence called out in HU-1440: an ENQUEUED
        ticket carries the user-facing 'a person will join you right now'
        promise; losing it on restart silently breaks that promise.
        """
        q1 = _handoff_queue(safety_engine)
        q1.enqueue(_ticket(ticket_id="hh-open-1"))
        q1.enqueue(_ticket(ticket_id="hh-open-2"))

        q2 = _handoff_queue(safety_engine)
        pending = q2.list_pending()
        assert {t.id for t in pending} == {"hh-open-1", "hh-open-2"}

    def test_resolve_persists_after_restart(self, safety_engine):
        q1 = _handoff_queue(safety_engine)
        q1.enqueue(_ticket(ticket_id="hh-r"))
        q1.resolve(
            "hh-r",
            outcome=HandoffOutcome.ANSWERED,
            responder_id="pat",
            clinical_review_note="connected user with crisis line",
        )

        q2 = _handoff_queue(safety_engine)
        got = q2.get("hh-r")
        assert got is not None
        assert got.outcome is HandoffOutcome.ANSWERED
        assert got.responder_id == "pat"
        assert got.clinical_review_note == "connected user with crisis line"
        assert got.resolved_at is not None
        # An answered ticket is no longer in the pending work queue.
        assert all(t.id != "hh-r" for t in q2.list_pending())

    def test_degraded_ticket_is_audited(self, safety_engine):
        """The §10.1 invariant 5 audit log must record every escalation,
        including DEGRADED ones. Durability means even the degrade record
        survives restart (clinical daily-review surface)."""
        q1 = _handoff_queue(safety_engine, available_responders=0)
        result = q1.enqueue(_ticket(ticket_id="hh-deg"))
        assert result.outcome is HandoffOutcome.DEGRADED
        assert result.degrade_reason == "no_responder_available"

        q2 = _handoff_queue(safety_engine)
        log = q2.audit_log()
        assert len(log) == 1
        assert log[0].outcome is HandoffOutcome.DEGRADED
        assert log[0].degrade_reason == "no_responder_available"


# --- AC #2: consent gate durability ----------------------------------------


class TestConsentGateDurability:
    def test_consenting_user_not_regated_after_restart(self, safety_engine):
        """The HU-1440 fix for §7.4.3: a user who acknowledged the card must
        not get a 409 CONSENT_REQUIRED again after the app restarts."""
        pid = uuid4()
        g1 = _consent_gate(safety_engine)
        g1.record_acknowledgement("sess-1", persona_id=pid, card_version=2)

        # "Restart": fresh backend, same DB.
        g2 = _consent_gate(safety_engine)
        assert g2.is_acknowledged("sess-1", pid) is True
        got = g2.get_record("sess-1", pid)
        assert got is not None
        assert got.card_version == 2

    def test_audit_log_retains_full_history(self, safety_engine):
        """Re-acknowledgment creates a new audit row (the full history is the
        §7.4.3 audit trail) and survives restart."""
        pid = uuid4()
        g1 = _consent_gate(safety_engine)
        first = g1.record_acknowledgement("sess-2", persona_id=pid, card_version=2)
        second = g1.record_acknowledgement("sess-2", persona_id=pid, card_version=2)

        g2 = _consent_gate(safety_engine)
        log = g2.audit_log()
        assert len(log) == 2
        assert {r.acknowledgment_id for r in log} == {
            first.acknowledgment_id, second.acknowledgment_id,
        }
        # Latest-of-record is the most recent acknowledgment.
        latest = g2.get_record("sess-2", pid)
        assert latest is not None
        assert latest.acknowledgment_id == second.acknowledgment_id

    def test_unconsented_session_still_gated_after_restart(self, safety_engine):
        g1 = _consent_gate(safety_engine)
        pid = uuid4()
        g1.record_acknowledgement("sess-x", persona_id=pid, card_version=2)

        g2 = _consent_gate(safety_engine)
        # A different session on the same persona is still gated.
        assert g2.is_acknowledged("sess-other", pid) is False
        # A different persona on the same session is still gated.
        assert g2.is_acknowledged("sess-x", uuid4()) is False

    def test_record_acknowledgement_validation(self, safety_engine):
        g = _consent_gate(safety_engine)
        with pytest.raises(ValueError):
            g.record_acknowledgement("", persona_id=uuid4(), card_version=2)
        with pytest.raises(ValueError):
            g.record_acknowledgement("s", persona_id=uuid4(), card_version=0)


# --- AC #3: conversation / crisis-session durability -----------------------


class TestConversationStoreDurability:
    def test_turn_history_survives_restart(self, safety_engine):
        s1 = _conversation_store(safety_engine)
        s1.append_turn("c1", ConversationTurn(speaker="user", content="hi"))
        s1.append_turn("c1", ConversationTurn(speaker="persona", content="hello"))
        s1.append_turn("c1", ConversationTurn(speaker="user", content="tell me more"))

        s2 = _conversation_store(safety_engine)
        history = s2.get_history("c1")
        assert [(t.speaker, t.content) for t in history] == [
            ("user", "hi"), ("persona", "hello"), ("user", "tell me more"),
        ]

    def test_dosage_turn_count_survives_restart(self, safety_engine):
        """§7.4.4 dosage-cap enforcement reads the turn count from history.
        A restart must not reset it to 0 (else a session near the cap gets a
        fresh quota — the HU-1440 consequence #4)."""
        s1 = _conversation_store(safety_engine)
        for _ in range(10):
            s1.append_turn("c-dose", ConversationTurn(speaker="user", content="x"))
            s1.append_turn("c-dose", ConversationTurn(speaker="persona", content="y"))

        s2 = _conversation_store(safety_engine)
        history = s2.get_history("c-dose")
        # 20 stored turns = 10 user+persona pairs.
        assert len(history) == 20
        # Chat endpoint derives turn_count = ceil(len(history) / 2).
        assert (len(history) + 1) // 2 == 10

    def test_crisis_marker_survives_restart(self, safety_engine):
        """The §7.4.4 matrix-§3 'crisis_history' signal must survive a
        restart so a repeat-crisis session keeps its tightened threshold."""
        s1 = _conversation_store(safety_engine)
        assert s1.has_crisis_history("c-crisis") is False
        s1.mark_crisis("c-crisis")
        assert s1.has_crisis_history("c-crisis") is True
        # Idempotent: marking twice does not flip it back.
        s1.mark_crisis("c-crisis")
        assert s1.has_crisis_history("c-crisis") is True

        s2 = _conversation_store(safety_engine)
        assert s2.has_crisis_history("c-crisis") is True
        # A session without a prior crisis turn is still unaffected.
        assert s2.has_crisis_history("c-calm") is False

    def test_empty_conversation_returns_empty(self, safety_engine):
        s = _conversation_store(safety_engine)
        assert s.get_history("never-seen") == []
        assert s.get_history(None) == []

    def test_null_conversation_id_is_noop(self, safety_engine):
        s = _conversation_store(safety_engine)
        s.append_turn(None, ConversationTurn(speaker="user", content="x"))
        s.mark_crisis(None)
        assert s.get_history(None) == []
        assert s.has_crisis_history(None) is False


# --- Settings: sync URL derivation -----------------------------------------


class TestSafetyDatabaseUrlDerivation:
    def test_async_url_swapped_to_psycopg(self):
        s = Settings(database_url="postgresql+asyncpg://u:p@host/db")
        assert s.effective_database_url.startswith("postgresql+asyncpg://")
        assert s.effective_safety_database_url.startswith("postgresql+psycopg://")
        # Same host/db, only the driver changed.
        assert "@host/db" in s.effective_safety_database_url

    def test_postgres_async_scheme_also_swapped(self):
        s = Settings(database_url="postgres+asyncpg://u:p@host/db")
        assert s.effective_safety_database_url.startswith("postgres+psycopg://")

    def test_no_db_configured_yields_empty_sync_url(self):
        s = Settings()
        assert s.effective_safety_database_url == ""

    def test_foreign_scheme_ignored(self):
        # A plain postgres:// control-plane URL must not be picked up.
        s = Settings(database_url="postgres://u:p@host/control")
        assert s.effective_database_url == ""
        assert s.effective_safety_database_url == ""

    def test_postgres_fields_assembled_into_sync_url(self):
        s = Settings(
            database_url="",
            postgres_user="u", postgres_password="p",
            postgres_host="h", postgres_port=5432, postgres_db="d",
        )
        sync = s.effective_safety_database_url
        assert sync == "postgresql+psycopg://u:p@h:5432/d"


# --- AC #1 integration: audit endpoint durability across "restart" ---------


class TestHandoffAuditEndpointDurability:
    """The staffed-responder audit surface (``GET /api/v1/handoff/audit``)
    must return durable records across a restart — the §10.1 invariant 5
    daily-review surface."""

    API_KEY = "key-durability"
    PERSONA_ID = uuid4()

    def _client_with_queue(self, queue) -> TestClient:
        keys = InMemoryApiKeyStore({self.API_KEY: self.PERSONA_ID}, read_env=False)
        registry = InMemoryPersonaRegistry()
        app = create_app(
            api_key_store=keys,
            persona_registry=registry,
            handoff_queue=queue,
            start_time=0.0,
        )
        return TestClient(app)

    def test_audit_endpoint_serves_durable_records(self, safety_engine):
        # Phase 1: one app instance handles a crisis escalation.
        q1 = _handoff_queue(safety_engine)
        client1 = self._client_with_queue(q1)
        # Seed a ticket directly through the queue (the chat path would route
        # the same enqueue via escalate_to_human; the audit endpoint reads
        # the same store either way).
        q1.enqueue(_ticket(ticket_id="hh-api-1"))

        resp1 = client1.get(
            "/api/v1/handoff/audit",
            headers={"Authorization": f"Bearer {self.API_KEY}"},
        )
        assert resp1.status_code == 200
        body1 = resp1.json()["data"]
        ids1 = [t["ticket_id"] for t in body1["tickets"]]
        assert "hh-api-1" in ids1

        # Phase 2: a "restarted" app instance with a fresh queue object
        # backed by the SAME durable store. The audit endpoint must still
        # surface the ticket.
        q2 = _handoff_queue(safety_engine)
        client2 = self._client_with_queue(q2)
        resp2 = client2.get(
            "/api/v1/handoff/audit",
            headers={"Authorization": f"Bearer {self.API_KEY}"},
        )
        assert resp2.status_code == 200
        body2 = resp2.json()["data"]
        ids2 = [t["ticket_id"] for t in body2["tickets"]]
        assert ids2 == ["hh-api-1"]
        # Telemetry is computed from the same durable audit log.
        assert body2["telemetry"]["total"] == 1
