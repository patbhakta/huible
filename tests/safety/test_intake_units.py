"""Unit tests for the Stage 0.5 risk-profile intake (HU-1448 / §7.4.4).

Covers the canary-cohort intake path end-to-end at the unit level:

* **derive_persona_flags** — objective persona-record derivation of
  ``minor_decedent`` (age threshold) and ``recent_loss`` (acute window).
* **RiskIntakeService.record_intake** — merges derived + assessment flags,
  writes the right scope (persona vs. session), and returns the audit view.
* **Consent-awareness** — record_intake raises ConsentNotRecordedError when
  the (session, persona) has not acknowledged the G6 reality-framing card;
  it succeeds after a recorded consent (the "no bypass" acceptance item).
* **All five flags populated** — one canary user with the full surface set.
* **intake → profile → enforcement-action** — the Stage 0.5 exit criterion:
  a seeded profile produces a non-CONTINUE binding action the chat path
  would take, proving G8 enforcement is live (not inert).
* **Durability** — intake-written flags survive a "container restart"
  (second PostgresRiskProfile object over the same SQLite file-engine),
  mirroring tests/safety/test_durability_store.py.
* **Admin endpoint** — POST /api/v1/admin/risk-intake records consent-gated
  intake and returns the audit view; 409 before consent, 403/404 on bad
  persona scoping.
"""

from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from huible.api.app import create_app
from huible.api.auth import InMemoryApiKeyStore, InMemoryPersonaRegistry
from huible.persona.context import PersonaConfig
from huible.safety import (
    CONSENT_CARD_VERSION,
    ConsentNotRecordedError,
    EnforcementAction,
    InMemoryConsentGate,
    InMemoryRiskProfile,
    RiskFlag,
    RiskIntakeAssessment,
    RiskIntakeService,
    derive_persona_flags,
    enforce_risk_flags,
)
from huible.safety.store import PostgresRiskProfile, SafetyBase

# --- constants + fixtures ---------------------------------------------------


CANARY_PERSONA_ID = UUID("11111111-1111-1111-1111-111111111111")


def _persona(
    *,
    persona_id: UUID = CANARY_PERSONA_ID,
    name: str = "Chandler",
    age_at_death: int | None = None,
    death_date: str | None = None,
) -> PersonaConfig:
    return PersonaConfig(
        id=persona_id,
        name=name,
        age_at_death=age_at_death,
        death_date=death_date,
    )


def _consent_gate_with_ack(session_id: str, persona_id: UUID) -> InMemoryConsentGate:
    gate = InMemoryConsentGate()
    gate.record_acknowledgement(
        session_id, persona_id=persona_id, card_version=CONSENT_CARD_VERSION
    )
    return gate


# --- derive_persona_flags ---------------------------------------------------


class TestDerivePersonaFlags:
    """Objective persona-record derivation (consent-independent)."""

    def test_minor_decedent_when_age_below_threshold(self):
        persona = _persona(age_at_death=12)
        flags = derive_persona_flags(persona, now=date(2026, 8, 11))
        assert RiskFlag.MINOR_DECEDENT in flags

    def test_adult_decedent_not_flagged(self):
        persona = _persona(age_at_death=72)
        assert RiskFlag.MINOR_DECEDENT not in derive_persona_flags(
            persona, now=date(2026, 8, 11)
        )

    def test_minor_threshold_is_exclusive(self):
        # exactly 18 -> adult (the deceased lived to 18, an adult).
        persona = _persona(age_at_death=18)
        assert RiskFlag.MINOR_DECEDENT not in derive_persona_flags(
            persona, now=date(2026, 8, 11)
        )
        persona = _persona(age_at_death=17)
        assert RiskFlag.MINOR_DECEDENT in derive_persona_flags(
            persona, now=date(2026, 8, 11)
        )

    def test_missing_age_does_not_flag(self):
        persona = _persona(age_at_death=None)
        assert RiskFlag.MINOR_DECEDENT not in derive_persona_flags(
            persona, now=date(2026, 8, 11)
        )

    def test_recent_loss_within_acute_window(self):
        today = date(2026, 8, 11)
        persona = _persona(death_date=(today - timedelta(days=60)).isoformat())
        assert RiskFlag.RECENT_LOSS in derive_persona_flags(persona, now=today)

    def test_recent_loss_at_window_boundary_inclusive(self):
        today = date(2026, 8, 11)
        # exactly 180 days ago -> still recent (the conservative ceiling).
        persona = _persona(death_date=(today - timedelta(days=180)).isoformat())
        assert RiskFlag.RECENT_LOSS in derive_persona_flags(persona, now=today)

    def test_recent_loss_outside_window(self):
        today = date(2026, 8, 11)
        persona = _persona(death_date=(today - timedelta(days=400)).isoformat())
        assert RiskFlag.RECENT_LOSS not in derive_persona_flags(persona, now=today)

    def test_unparseable_death_date_is_ignored(self):
        persona = _persona(death_date="not-a-date")
        assert RiskFlag.RECENT_LOSS not in derive_persona_flags(
            persona, now=date(2026, 8, 11)
        )

    def test_both_derived_flags_when_minor_and_recent(self):
        today = date(2026, 8, 11)
        persona = _persona(
            age_at_death=8,
            death_date=(today - timedelta(days=30)).isoformat(),
        )
        flags = derive_persona_flags(persona, now=today)
        assert set(flags) == {RiskFlag.MINOR_DECEDENT, RiskFlag.RECENT_LOSS}

    def test_no_flags_for_adult_long_ago_loss(self):
        today = date(2026, 8, 11)
        persona = _persona(age_at_death=80, death_date="2010-01-01")
        assert derive_persona_flags(persona, now=today) == []


# --- RiskIntakeService.record_intake ---------------------------------------


class TestRecordIntake:
    """Merging derived + assessment flags and writing the right scope."""

    def test_writes_derived_flags_to_persona_scope(self):
        profile = InMemoryRiskProfile()
        service = RiskIntakeService(profile)
        persona = _persona(age_at_death=10)

        result = service.record_intake(
            session_id="sess-1",
            persona_id=CANARY_PERSONA_ID,
            persona=persona,
            assessment=RiskIntakeAssessment(),
        )

        assert RiskFlag.MINOR_DECEDENT.value in result.persona_flags
        assert result.session_flags == []
        # Read back through the provider the chat path uses.
        assert "minor_decedent" in profile.get_flags("sess-1", CANARY_PERSONA_ID)
        # Applies to every session for this persona.
        assert "minor_decedent" in profile.get_flags("sess-2", CANARY_PERSONA_ID)

    def test_writes_assessment_persona_flags(self):
        profile = InMemoryRiskProfile()
        service = RiskIntakeService(profile)

        result = service.record_intake(
            session_id="sess-1",
            persona_id=CANARY_PERSONA_ID,
            persona=_persona(),
            assessment=RiskIntakeAssessment(loss_of_child=True, non_acceptance=True),
        )

        assert "loss_of_child" in result.persona_flags
        assert "non_acceptance" in result.persona_flags
        assert "loss_of_child" in profile.get_flags("sess-1", CANARY_PERSONA_ID)

    def test_proxy_user_written_to_session_scope_only(self):
        """proxy_user is intrinsically per-session (matrix §2)."""
        profile = InMemoryRiskProfile()
        service = RiskIntakeService(profile)

        result = service.record_intake(
            session_id="sess-1",
            persona_id=CANARY_PERSONA_ID,
            persona=_persona(),
            assessment=RiskIntakeAssessment(proxy_user=True),
        )

        assert "proxy_user" in result.session_flags
        assert "proxy_user" not in result.persona_flags
        # Present for this session.
        assert "proxy_user" in profile.get_flags("sess-1", CANARY_PERSONA_ID)
        # NOT present for a different session on the same persona.
        assert "proxy_user" not in profile.get_flags("sess-2", CANARY_PERSONA_ID)

    def test_all_five_flags_populated_for_canary_user(self):
        """AC: intake path populates the five named flags for a canary user."""
        today = date(2026, 8, 11)
        profile = InMemoryRiskProfile()
        service = RiskIntakeService(profile)
        persona = _persona(
            age_at_death=10,
            death_date=(today - timedelta(days=30)).isoformat(),
        )

        result = service.record_intake(
            session_id="sess-canary",
            persona_id=CANARY_PERSONA_ID,
            persona=persona,
            assessment=RiskIntakeAssessment(
                loss_of_child=True, non_acceptance=True, proxy_user=True
            ),
        )

        # All five flags are present in the union.
        assert set(result.all_flags) == {
            "loss_of_child",
            "minor_decedent",
            "recent_loss",
            "non_acceptance",
            "proxy_user",
        }
        # The chat-path read returns the full set.
        flags_read = profile.get_flags("sess-canary", CANARY_PERSONA_ID)
        assert set(flags_read) == {
            "loss_of_child",
            "minor_decedent",
            "recent_loss",
            "non_acceptance",
            "proxy_user",
        }

    def test_result_breaks_down_by_source(self):
        profile = InMemoryRiskProfile()
        service = RiskIntakeService(profile)
        today = date(2026, 8, 11)
        persona = _persona(age_at_death=10, death_date=(today - timedelta(days=10)).isoformat())

        result = service.record_intake(
            session_id="sess-1",
            persona_id=CANARY_PERSONA_ID,
            persona=persona,
            assessment=RiskIntakeAssessment(loss_of_child=True, proxy_user=True),
        )

        assert set(result.derived_flags) == {"minor_decedent", "recent_loss"}
        assert set(result.assessed_flags) == {"loss_of_child", "proxy_user"}

    def test_requires_session_id(self):
        service = RiskIntakeService(InMemoryRiskProfile())
        with pytest.raises(ValueError, match="session_id"):
            service.record_intake(
                session_id="",
                persona_id=CANARY_PERSONA_ID,
                persona=_persona(),
                assessment=RiskIntakeAssessment(),
            )


# --- consent-awareness ------------------------------------------------------


class TestConsentGate:
    """AC: intake respects G6 consent (no bypass to gather flags)."""

    def test_raises_when_consent_not_recorded(self):
        profile = InMemoryRiskProfile()
        gate = InMemoryConsentGate()  # no acknowledgment recorded
        service = RiskIntakeService(profile, consent_gate=gate)

        with pytest.raises(ConsentNotRecordedError):
            service.record_intake(
                session_id="sess-1",
                persona_id=CANARY_PERSONA_ID,
                persona=_persona(),
                assessment=RiskIntakeAssessment(loss_of_child=True),
            )

    def test_succeeds_after_consent_recorded(self):
        profile = InMemoryRiskProfile()
        gate = _consent_gate_with_ack("sess-1", CANARY_PERSONA_ID)
        service = RiskIntakeService(profile, consent_gate=gate)

        result = service.record_intake(
            session_id="sess-1",
            persona_id=CANARY_PERSONA_ID,
            persona=_persona(),
            assessment=RiskIntakeAssessment(loss_of_child=True),
        )

        assert "loss_of_child" in result.persona_flags
        assert result.consent_acknowledgment_id is not None

    def test_no_bypass_when_one_session_consents_and_another_does_not(self):
        """Consent is per (session, persona); a second un-consented session
        cannot have its assessment gathered even after a first session consented."""
        profile = InMemoryRiskProfile()
        gate = _consent_gate_with_ack("sess-1", CANARY_PERSONA_ID)
        service = RiskIntakeService(profile, consent_gate=gate)

        # sess-1 consented -> intake succeeds.
        service.record_intake(
            session_id="sess-1",
            persona_id=CANARY_PERSONA_ID,
            persona=_persona(),
            assessment=RiskIntakeAssessment(loss_of_child=True),
        )
        # sess-2 has NOT consented -> intake refuses.
        with pytest.raises(ConsentNotRecordedError):
            service.record_intake(
                session_id="sess-2",
                persona_id=CANARY_PERSONA_ID,
                persona=_persona(),
                assessment=RiskIntakeAssessment(loss_of_child=True),
            )

    def test_no_consent_gate_is_permissive(self):
        """The raw write surface stays available when no gate is wired
        (admin/test seeder pre-populating objective flags outside the user flow)."""
        profile = InMemoryRiskProfile()
        service = RiskIntakeService(profile)  # no consent_gate

        result = service.record_intake(
            session_id="sess-1",
            persona_id=CANARY_PERSONA_ID,
            persona=_persona(),
            assessment=RiskIntakeAssessment(loss_of_child=True),
        )
        assert "loss_of_child" in result.persona_flags

    def test_consent_acknowledgment_id_recorded_on_success(self):
        profile = InMemoryRiskProfile()
        gate = _consent_gate_with_ack("sess-1", CANARY_PERSONA_ID)
        record = gate.get_record("sess-1", CANARY_PERSONA_ID)
        service = RiskIntakeService(profile, consent_gate=gate)

        result = service.record_intake(
            session_id="sess-1",
            persona_id=CANARY_PERSONA_ID,
            persona=_persona(),
            assessment=RiskIntakeAssessment(),
        )
        assert result.consent_acknowledgment_id == record.acknowledgment_id


# --- intake -> profile -> enforcement-action (Stage 0.5 exit criterion) -----


class TestIntakeToEnforcement:
    """AC: G8 enforcement fires on a seeded profile and takes the correct action.

    The Stage 0.5 exit criterion: a populated profile changes runtime behavior.
    Each path exercises intake → get_flags → enforce_risk_flags to prove the
    engine is live (not inert) and the correct matrix action fires.
    """

    @staticmethod
    def _seed_then_enforce(
        *,
        persona: PersonaConfig,
        assessment: RiskIntakeAssessment,
        session_id: str = "sess-enforce",
        message: str = "tell me about your day",
        session_signals=None,
    ) -> tuple[list[str], EnforcementAction]:
        profile = InMemoryRiskProfile()
        service = RiskIntakeService(profile)
        service.record_intake(
            session_id=session_id,
            persona_id=CANARY_PERSONA_ID,
            persona=persona,
            assessment=assessment,
        )
        flags_read = profile.get_flags(session_id, CANARY_PERSONA_ID)
        report = enforce_risk_flags(
            flags_read, session_signals=session_signals, message=message
        )
        return flags_read, report.action

    def test_proxy_user_profile_binds_pause_session(self):
        _, action = self._seed_then_enforce(
            persona=_persona(),
            assessment=RiskIntakeAssessment(proxy_user=True),
        )
        assert action is EnforcementAction.PAUSE_SESSION

    def test_minor_decedent_profile_tightens_neutral_message(self):
        _, action = self._seed_then_enforce(
            persona=_persona(age_at_death=12),
            assessment=RiskIntakeAssessment(),
            message="tell me about your favorite toy",
        )
        assert action is EnforcementAction.TIGHTEN

    def test_minor_decedent_profile_refuses_age_inappropriate_topic(self):
        _, action = self._seed_then_enforce(
            persona=_persona(age_at_death=12),
            assessment=RiskIntakeAssessment(),
            message="tell me about our dating future together",
        )
        assert action is EnforcementAction.REFUSE_TOPIC

    def test_non_acceptance_profile_binds_reframe(self):
        _, action = self._seed_then_enforce(
            persona=_persona(),
            assessment=RiskIntakeAssessment(non_acceptance=True),
        )
        assert action is EnforcementAction.REFRAME

    def test_loss_of_child_profile_binds_reframe(self):
        # loss_of_child -> {tighten, reframe}; reframe dominates.
        _, action = self._seed_then_enforce(
            persona=_persona(),
            assessment=RiskIntakeAssessment(loss_of_child=True),
        )
        assert action is EnforcementAction.REFRAME

    def test_empty_profile_continues_inert(self):
        """The default (empty) profile produces CONTINUE — the inert baseline
        this Stage 0.5 path exists to replace for the canary cohort."""
        profile = InMemoryRiskProfile()
        flags_read = profile.get_flags("sess", CANARY_PERSONA_ID)
        report = enforce_risk_flags(flags_read)
        assert report.action is EnforcementAction.CONTINUE

    def test_seeded_profile_unions_multiple_flags_to_most_restrictive(self):
        # proxy_user (pause) + loss_of_child (reframe) -> pause_session wins.
        _, action = self._seed_then_enforce(
            persona=_persona(),
            assessment=RiskIntakeAssessment(proxy_user=True, loss_of_child=True),
        )
        assert action is EnforcementAction.PAUSE_SESSION


# --- durability: flags survive a "restart" ----------------------------------


@pytest.fixture
def risk_engine():
    """A fresh in-memory SQLite engine with the risk_profiles table created.

    A StaticPool file-less engine keeps the single in-memory DB visible across
    separate connections (so a "restarted" backend object sees the rows the
    previous one wrote). Mirrors tests/safety/test_durability_store.py.
    """
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    SafetyBase.metadata.create_all(eng)
    yield eng
    eng.dispose()


def _durable_profile(eng) -> PostgresRiskProfile:
    """Build a PostgresRiskProfile wired to a shared (already-created) engine."""
    p = PostgresRiskProfile.__new__(PostgresRiskProfile)
    p._engine = eng
    p._session_factory = sessionmaker(eng, class_=Session, expire_on_commit=False)
    return p


class TestDurability:
    """AC: populated flags survive a backend restart (PostgresRiskProfile)."""

    def test_persona_flags_survive_restart(self, risk_engine):
        today = date(2026, 8, 11)
        p1 = _durable_profile(risk_engine)
        service = RiskIntakeService(p1)
        service.record_intake(
            session_id="sess-1",
            persona_id=CANARY_PERSONA_ID,
            persona=_persona(
                age_at_death=10,
                death_date=(today - timedelta(days=30)).isoformat(),
            ),
            assessment=RiskIntakeAssessment(loss_of_child=True),
        )

        # Simulate a container restart: a fresh backend object over the same DB.
        p2 = _durable_profile(risk_engine)
        flags = p2.get_flags("sess-1", CANARY_PERSONA_ID)
        assert set(flags) == {"loss_of_child", "minor_decedent", "recent_loss"}
        # Persona-scoped flags apply to a brand-new session after restart too.
        flags_new_session = p2.get_flags("sess-after-restart", CANARY_PERSONA_ID)
        assert "minor_decedent" in flags_new_session
        assert "loss_of_child" in flags_new_session

    def test_session_flags_survive_restart(self, risk_engine):
        p1 = _durable_profile(risk_engine)
        service = RiskIntakeService(p1)
        service.record_intake(
            session_id="sess-1",
            persona_id=CANARY_PERSONA_ID,
            persona=_persona(),
            assessment=RiskIntakeAssessment(proxy_user=True),
        )

        p2 = _durable_profile(risk_engine)
        # proxy_user was session-scoped — survives on sess-1.
        assert "proxy_user" in p2.get_flags("sess-1", CANARY_PERSONA_ID)
        # And does NOT leak to a different session.
        assert "proxy_user" not in p2.get_flags("sess-2", CANARY_PERSONA_ID)

    def test_enforcement_live_after_restart(self, risk_engine):
        """The full chain persists: intake-written proxy_user still binds
        PAUSE_SESSION after a restart (G8 does not go inert mid-ramp)."""
        p1 = _durable_profile(risk_engine)
        RiskIntakeService(p1).record_intake(
            session_id="sess-1",
            persona_id=CANARY_PERSONA_ID,
            persona=_persona(),
            assessment=RiskIntakeAssessment(proxy_user=True),
        )

        p2 = _durable_profile(risk_engine)
        flags = p2.get_flags("sess-1", CANARY_PERSONA_ID)
        report = enforce_risk_flags(flags)
        assert report.action is EnforcementAction.PAUSE_SESSION


# --- admin endpoint: POST /api/v1/admin/risk-intake -------------------------


def _intake_app(
    *,
    persona: PersonaConfig,
    consented_session: str | None = None,
):
    """Build a TestClient app with a registered persona + optional consent recorded."""
    from huible.memory.protocol import MemoryBackend

    class _NullBackend(MemoryBackend):
        async def retrieve(self, *args, **kwargs):  # pragma: no cover - unused
            return []

        async def ingest(self, *args, **kwargs):  # pragma: no cover - unused
            return None

        async def health_check(self, *args, **kwargs):  # pragma: no cover
            return {"database": "skipped"}

    registry = InMemoryPersonaRegistry()
    registry.register(persona, _NullBackend())
    key_store = InMemoryApiKeyStore({f"key-{persona.id}": persona.id})
    consent_gate = InMemoryConsentGate()
    if consented_session is not None:
        consent_gate.record_acknowledgement(
            consented_session,
            persona_id=persona.id,
            card_version=CONSENT_CARD_VERSION,
        )
    risk_profile = InMemoryRiskProfile()
    app = create_app(
        api_key_store=key_store,
        persona_registry=registry,
        consent_gate=consent_gate,
        risk_profile=risk_profile,
    )
    return app, risk_profile


class TestAdminIntakeEndpoint:
    """POST /api/v1/admin/risk-intake — the canary-cohort intake surface."""

    def test_records_intake_after_consent(self):
        persona = _persona(age_at_death=10)
        app, profile = _intake_app(
            persona=persona, consented_session="sess-admin"
        )
        client = TestClient(app)

        resp = client.post(
            "/api/v1/admin/risk-intake",
            json={
                "conversation_id": "sess-admin",
                "persona_id": str(persona.id),
                "loss_of_child": True,
            },
            headers={"Authorization": f"Bearer key-{persona.id}"},
        )

        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert set(data["all_flags"]) == {"loss_of_child", "minor_decedent"}
        assert data["consent_acknowledgment_id"] is not None
        # The chat path can now read the seeded profile back.
        assert "minor_decedent" in profile.get_flags("sess-admin", persona.id)

    def test_409_before_consent(self):
        """AC: intake respects G6 consent — refuses before acknowledgment."""
        persona = _persona()
        app, _ = _intake_app(persona=persona)  # no consent recorded
        client = TestClient(app)

        resp = client.post(
            "/api/v1/admin/risk-intake",
            json={
                "conversation_id": "sess-no-consent",
                "persona_id": str(persona.id),
                "loss_of_child": True,
            },
            headers={"Authorization": f"Bearer key-{persona.id}"},
        )

        assert resp.status_code == 409
        body = resp.json()["detail"]["error"]
        assert body["code"] == "CONSENT_REQUIRED"
        assert body["acknowledge_url"].endswith("/consent")

    def test_403_when_persona_id_mismatches_key_scope(self):
        persona = _persona()
        other = _persona(persona_id=uuid4(), name="Other")
        app, _ = _intake_app(persona=persona)
        client = TestClient(app)

        resp = client.post(
            "/api/v1/admin/risk-intake",
            json={
                "conversation_id": "sess-x",
                "persona_id": str(other.id),  # not the key's scope
            },
            headers={"Authorization": f"Bearer key-{persona.id}"},
        )
        assert resp.status_code == 403

    def test_404_when_persona_not_registered(self):
        # persona IS the key scope, but the body persona_id is unknown to the
        # registry. We need a key bound to that unknown id to get past the
        # 403 scope check, so build a persona + key + an empty registry.
        persona = _persona()
        from huible.memory.protocol import MemoryBackend

        class _NullBackend(MemoryBackend):
            async def retrieve(self, *a, **k):  # pragma: no cover
                return []

            async def ingest(self, *a, **k):  # pragma: no cover
                return None

            async def health_check(self, *a, **k):  # pragma: no cover
                return {"database": "skipped"}

        # Empty registry: persona id is NOT registered.
        app = create_app(
            api_key_store=InMemoryApiKeyStore({f"key-{persona.id}": persona.id}),
            persona_registry=InMemoryPersonaRegistry(),
            consent_gate=_consent_gate_with_ack("sess-x", persona.id),
            risk_profile=InMemoryRiskProfile(),
        )
        client = TestClient(app)

        resp = client.post(
            "/api/v1/admin/risk-intake",
            json={
                "conversation_id": "sess-x",
                "persona_id": str(persona.id),
            },
            headers={"Authorization": f"Bearer key-{persona.id}"},
        )
        assert resp.status_code == 404

    def test_401_without_bearer_key(self):
        persona = _persona()
        app, _ = _intake_app(persona=persona)
        client = TestClient(app)

        resp = client.post(
            "/api/v1/admin/risk-intake",
            json={
                "conversation_id": "sess-x",
                "persona_id": str(persona.id),
            },
        )
        assert resp.status_code == 401

    def test_proxy_user_session_scoped_via_endpoint(self):
        persona = _persona()
        app, profile = _intake_app(
            persona=persona, consented_session="sess-admin"
        )
        client = TestClient(app)

        resp = client.post(
            "/api/v1/admin/risk-intake",
            json={
                "conversation_id": "sess-admin",
                "persona_id": str(persona.id),
                "proxy_user": True,
            },
            headers={"Authorization": f"Bearer key-{persona.id}"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["session_flags"] == ["proxy_user"]
        assert data["persona_flags"] == []
        # Session-scoped: present on sess-admin, not on sess-other.
        assert "proxy_user" in profile.get_flags("sess-admin", persona.id)
        assert "proxy_user" not in profile.get_flags("sess-other", persona.id)


# --- re-exports sanity ------------------------------------------------------


def test_intake_reexported_from_safety_package():
    """Consumers get a single import path (from huible.safety import ...)."""
    import huible.safety as safety

    for name in (
        "RiskIntakeService",
        "RiskIntakeAssessment",
        "IntakeResult",
        "ConsentNotRecordedError",
        "derive_persona_flags",
        "MINOR_DECEDENT_AGE_THRESHOLD",
        "RECENT_LOSS_ACUTE_WINDOW_DAYS",
    ):
        assert hasattr(safety, name), f"{name} missing from huible.safety"


if __name__ == "__main__":
    import pytest as _pytest

    _pytest.main([__file__, "-v"])
