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
from huible.llm.client import FakeLLMClient
from huible.persona.context import ConversationTurn, PersonaConfig
from huible.safety.handoff import (
    DEFAULT_HANDOFF_SLA_SECONDS,
    CoverageWindow,
    HandoffOutcome,
    HandoffTicket,
)
from huible.safety.risk import RiskFlag, RiskProfileProvider
from huible.safety.store import (
    PostgresConsentGate,
    PostgresConversationStore,
    PostgresHandoffQueue,
    PostgresRiskProfile,
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


def _risk_profile(eng) -> PostgresRiskProfile:
    """Build a PostgresRiskProfile wired to a shared (already-created) engine.

    Mirrors the other ``_<backend>(eng)`` helpers so a "restarted" profile is
    just a second object over the same DB — the durability contract under test.
    """
    p = PostgresRiskProfile.__new__(PostgresRiskProfile)
    p._engine = eng
    p._session_factory = sessionmaker(eng, class_=Session, expire_on_commit=False)
    return p


def _ticket(
    *,
    ticket_id: str = "hh-1",
    persona_id: str = "p1",
    conversation_id: str = "c1",
    outcome: HandoffOutcome = HandoffOutcome.ENQUEUED,
    age_seconds: int = 60,
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
            first.acknowledgment_id,
            second.acknowledgment_id,
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
            ("user", "hi"),
            ("persona", "hello"),
            ("user", "tell me more"),
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


# --- AC #4: risk profile durability (HU-1445) --------------------------------


class TestRiskProfileDurability:
    """The §7.4.4 G8 risk profile must survive a container restart.

    This is the HU-1445 consequence: a populated risk profile wiped on restart
    silently disables G8 enforcement — every per-flag required action in
    ``RISK_FLAG_REQUIRED_ACTIONS`` reverts to the no-flag (``CONTINUE``) path,
    so e.g. a ``loss_of_child`` session stops tightening/reframing and a
    ``proxy_user`` session stops pausing. The intake-derived flags must persist.
    """

    def test_persona_flags_survive_restart(self, safety_engine):
        """Intake-derived persona flags apply to every session and survive."""
        pid = uuid4()
        p1 = _risk_profile(safety_engine)
        p1.set_persona_flags(pid, {RiskFlag.LOSS_OF_CHILD, RiskFlag.RECENT_LOSS})

        # "Restart": fresh backend, same DB.
        p2 = _risk_profile(safety_engine)
        flags = p2.get_flags("any-session", pid)
        assert flags == [RiskFlag.LOSS_OF_CHILD.value, RiskFlag.RECENT_LOSS.value]

    def test_session_flags_survive_restart(self, safety_engine):
        """Session-scoped flags (proxy_user, non_acceptance) survive restart."""
        pid = uuid4()
        p1 = _risk_profile(safety_engine)
        p1.set_session_flags("sess-1", pid, {RiskFlag.PROXY_USER})

        p2 = _risk_profile(safety_engine)
        flags = p2.get_flags("sess-1", pid)
        assert flags == [RiskFlag.PROXY_USER.value]

    def test_persona_and_session_union_survives_restart(self, safety_engine):
        """get_flags returns the union of persona + session scopes (the
        InMemoryRiskProfile contract), and that union survives restart."""
        pid = uuid4()
        p1 = _risk_profile(safety_engine)
        p1.set_persona_flags(pid, {RiskFlag.LOSS_OF_CHILD})
        p1.set_session_flags("sess-u", pid, {RiskFlag.NON_ACCEPTANCE})

        p2 = _risk_profile(safety_engine)
        union = p2.get_flags("sess-u", pid)
        assert union == [RiskFlag.LOSS_OF_CHILD.value, RiskFlag.NON_ACCEPTANCE.value]
        # A different session on the same persona only sees the persona flags.
        other = p2.get_flags("sess-other", pid)
        assert other == [RiskFlag.LOSS_OF_CHILD.value]

    def test_set_persona_flags_upserts(self, safety_engine):
        """Re-setting persona flags replaces (not appends to) the prior set,
        mirroring InMemoryRiskProfile.set_persona_flags."""
        pid = uuid4()
        p1 = _risk_profile(safety_engine)
        p1.set_persona_flags(pid, {RiskFlag.LOSS_OF_CHILD, RiskFlag.RECENT_LOSS})
        p1.set_persona_flags(pid, {RiskFlag.MINOR_DECEDENT})

        p2 = _risk_profile(safety_engine)
        flags = p2.get_flags("sess-1", pid)
        # The second set replaced the first — no stale loss_of_child/recent_loss.
        assert flags == [RiskFlag.MINOR_DECEDENT.value]

    def test_set_session_flags_upserts(self, safety_engine):
        pid = uuid4()
        p1 = _risk_profile(safety_engine)
        p1.set_session_flags("sess-1", pid, {RiskFlag.PROXY_USER})
        p1.set_session_flags("sess-1", pid, {RiskFlag.NON_ACCEPTANCE})

        p2 = _risk_profile(safety_engine)
        flags = p2.get_flags("sess-1", pid)
        assert flags == [RiskFlag.NON_ACCEPTANCE.value]

    def test_empty_profile_stays_empty_after_restart(self, safety_engine):
        """An un-populated persona must read empty (the pre-real-user default)
        after a restart, so the default persona-chat turn stays unaffected."""
        pid = uuid4()
        p1 = _risk_profile(safety_engine)

        p2 = _risk_profile(safety_engine)
        assert p2.get_flags("sess-1", pid) == []
        # Sanity: the first object also read empty before the "restart".
        assert p1.get_flags("sess-1", pid) == []

    def test_is_a_risk_profile_provider(self, safety_engine):
        """PostgresRiskProfile satisfies the RiskProfileProvider Protocol."""
        p = _risk_profile(safety_engine)
        assert isinstance(p, RiskProfileProvider)

    def test_persona_flags_isolated_per_persona(self, safety_engine):
        """Flags for one persona must not leak into another's profile."""
        pid_a = uuid4()
        pid_b = uuid4()
        p1 = _risk_profile(safety_engine)
        p1.set_persona_flags(pid_a, {RiskFlag.LOSS_OF_CHILD})

        p2 = _risk_profile(safety_engine)
        assert p2.get_flags("sess-1", pid_a) == [RiskFlag.LOSS_OF_CHILD.value]
        assert p2.get_flags("sess-1", pid_b) == []


# --- ORM ↔ production DDL type alignment (HU-1459 regression guard) ---------


class TestOrmTimestampTypesMatchProdDdl:
    """The durable timestamp columns must be ``TIMESTAMPTZ`` on Postgres.

    Regression guard for the HU-1459 deployment gap: the sqlite test path
    stores everything as text, so an ORM column declared ``Text`` round-trips
    cleanly in the key-free suite even when the production schema (init-db +
    Alembic 002) declares the column ``TIMESTAMPTZ``. On real Postgres that
    mismatch raises ``DatatypeMismatch`` on the first insert, silently
    breaking the "conversation history persists across restart" acceptance.
    These assertions pin the ORM timestamp columns to ``DateTime`` so the
    type-alignment with the production DDL cannot regress.
    """

    def _col(self, model, name):
        from sqlalchemy import DateTime

        col = model.__table__.columns[name]
        assert isinstance(col.type, DateTime), (
            f"{model.__name__}.{name} must be DateTime (TIMESTAMPTZ on "
            f"Postgres), got {type(col.type).__name__}"
        )
        assert col.type.timezone is True, (
            f"{model.__name__}.{name} must be timezone-aware (TIMESTAMPTZ)"
        )

    def test_handoff_ticket_timestamps_are_timestamptz(self):
        from huible.safety.store import HandoffTicketRow

        self._col(HandoffTicketRow, "created_at")
        self._col(HandoffTicketRow, "resolved_at")

    def test_consent_record_timestamp_is_timestamptz(self):
        from huible.safety.store import ConsentRecordRow

        self._col(ConsentRecordRow, "acknowledged_at")


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
            postgres_user="u",
            postgres_password="p",
            postgres_host="h",
            postgres_port=5432,
            postgres_db="d",
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


# --- HU-1460 Stage-0.5: persistent crisis-session history (e2e via chat path) -
#
# The store-level tests above prove the durable backend persists the crisis
# marker. HU-1460's acceptance is that the *production chat path* writes through
# that backend (not the old in-process ``app.state.crisis_sessions`` set), so a
# fresh app instance — a container restart or a second replica sharing the DB —
# reads the crisis history AND the handoff audit for the crisis turn. These two
# tests exercise the full ``POST /api/v1/chat/{persona_id}`` G1 path against a
# durable store/queue and assert cross-instance visibility.

_PERSONA_ID_E2E = uuid4()
_API_KEY_E2E = "key-crisis-durability-e2e"


class _NullBackend:
    """No-op memory backend.

    The G1 crisis path bypasses the ContextBuilder entirely (no memory
    retrieval on a crisis turn). The distress (non-crisis) turn used by the
    regression guard does go through the persona path, so the read methods
    return empty results rather than raise. Either way no real memory is
    needed for the crisis-durability contract under test.
    """

    async def store_memory(self, node):  # pragma: no cover - never called
        return getattr(node, "id", None)

    async def get_memory(self, memory_id):  # pragma: no cover - never called
        return None

    async def search_by_content(self, *a, **k):
        return []

    async def search_by_sensory(self, *a, **k):
        return []

    async def search_by_affect(self, *a, **k):
        return []

    async def get_edges(self, memory_id):
        return []

    async def add_edge(self, edge):  # pragma: no cover - never called
        return getattr(edge, "id", None)

    async def supersede_memory(self, *a, **k):  # pragma: no cover - never called
        return None

    async def get_active_memories(self, *a, **k):
        return []

    async def quarantine_candidate(self, *a, **k):  # pragma: no cover - never called
        return None

    async def get_all_versions(self, memory_id):
        return []


def _crisis_chat_app(queue, store) -> TestClient:
    """Build a chat-capable app wired to durable ``queue`` + ``store``.

    Mirrors the ``_make_app`` fixture in ``tests/api/test_chat_guardrails.py``
    but injects the durable §7.4 backends so the G1 crisis turn writes through
    the real persistence layer (the HU-1460 contract under test).
    """
    persona = PersonaConfig(
        id=_PERSONA_ID_E2E,
        name="Chandler",
        voice_instructions="Warm Texas storyteller.",
        era_knowledge_boundary="2024-12-01",
        age_at_death=72,
        death_date="2024-12-01",
    )
    registry = InMemoryPersonaRegistry({persona.id: (persona, _NullBackend())})
    keys = InMemoryApiKeyStore({_API_KEY_E2E: _PERSONA_ID_E2E}, read_env=False)
    application = create_app(
        api_key_store=keys,
        persona_registry=registry,
        llm_client=FakeLLMClient(),
        handoff_queue=queue,
        conversation_store=store,
        start_time=0.0,
    )
    return TestClient(application)


def _post_crisis(client: TestClient, message: str, conversation_id: str) -> dict:
    """Pre-consent then POST a message; return the parsed chat body.

    Sends ``X-Huible-Traffic-Class: internal`` so the Stage-0.1 real-user ramp
    gate (HU-1444) never refuses the turn — this is synthetic test traffic,
    which is exactly what the ``internal`` class denotes (see
    ``huible.api.real_user_gate``). Keeps the test independent of the runtime
    ``persona_chat_real_user_mode`` setting.
    """
    headers = {
        "Authorization": f"Bearer {_API_KEY_E2E}",
        "X-Huible-Traffic-Class": "internal",
    }
    client.post(
        f"/api/v1/chat/{_PERSONA_ID_E2E}/consent",
        json={"conversation_id": conversation_id},
        headers=headers,
    )
    resp = client.post(
        f"/api/v1/chat/{_PERSONA_ID_E2E}",
        json={"message": message, "conversation_id": conversation_id},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


class TestCrisisSessionHistoryDurabilityE2E:
    """HU-1460 Stage-0.5 acceptance through the real chat endpoint.

    AC #1 — crisis-session history persists across a restart and is shared
    across instances (a fresh app over the same DB reads the marker).
    AC #2 — the handoff audit still records the crisis-session escalation,
    visible to a fresh instance.
    """

    def test_crisis_history_persists_across_restart_via_chat_path(self, safety_engine):
        """AC #1: a G1 crisis turn served by instance 1 is visible as
        ``has_crisis_history=True`` to a fresh instance 2 over the same DB."""
        conv = "sess-crisis-e2e"
        # Instance 1 serves the crisis turn.
        q1 = _handoff_queue(
            safety_engine, available_responders=1, responder_id_pool=("pat",)
        )
        store1 = _conversation_store(safety_engine)
        client1 = _crisis_chat_app(q1, store1)
        body = _post_crisis(client1, "I want to die, I have the pills", conv)
        # The turn really was a crisis escalation (not the persona voice).
        assert body["trace"]["safety_event"]["kind"] == "crisis_escalation"
        assert body["trace"]["handoff"]["trigger_signal"] == "crisis"
        # Sanity: instance 1 sees its own in-flight marker.
        assert store1.has_crisis_history(conv) is True

        # "Restart": a fresh app + fresh store/queue over the SAME durable DB.
        q2 = _handoff_queue(safety_engine)
        store2 = _conversation_store(safety_engine)
        _crisis_chat_app(q2, store2)
        # AC #1 — the marker survived the restart and is visible cross-instance.
        assert store2.has_crisis_history(conv) is True
        # A session with no prior crisis turn is still unmarked.
        assert store2.has_crisis_history("sess-never-crisis") is False

    def test_handoff_audit_records_crisis_session_across_restart(self, safety_engine):
        """AC #2: the handoff audit log retains the crisis escalation and a
        fresh instance reads it (the §10.1 invariant 5 surface)."""
        conv = "sess-audit-e2e"
        q1 = _handoff_queue(
            safety_engine, available_responders=1, responder_id_pool=("pat",)
        )
        store1 = _conversation_store(safety_engine)
        client1 = _crisis_chat_app(q1, store1)
        _post_crisis(client1, "I am going to kill myself", conv)

        # Fresh instance over the same durable DB reads the audit trail.
        q2 = _handoff_queue(safety_engine)
        log = q2.audit_log()
        assert len(log) == 1
        ticket = log[0]
        assert ticket.id.startswith("hh-")
        assert ticket.conversation_id == conv
        assert ticket.trigger_signal == "crisis"
        assert ticket.outcome is HandoffOutcome.ENQUEUED

    def test_non_crisis_turn_does_not_mark_session(self, safety_engine):
        """Regression guard: only a G1 crisis turn marks the session. A
        distress (sub-acute) turn through the same durable path must not."""
        conv = "sess-distress-e2e"
        q = _handoff_queue(
            safety_engine, available_responders=1, responder_id_pool=("pat",)
        )
        store = _conversation_store(safety_engine)
        client = _crisis_chat_app(q, store)
        body = _post_crisis(client, "I miss him so much, my heart is broken", conv)
        # Distress, not crisis → persona path, no handoff, no crisis marker.
        assert body["trace"]["safety_event"] is None
        assert body["trace"]["handoff"] is None
        assert store.has_crisis_history(conv) is False
        assert q.audit_log() == []
