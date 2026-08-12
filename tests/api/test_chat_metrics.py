"""Stage 0.3 metrics tests (HU-1446) + Stage 0.8 SLO gauge tests (HU-1463).

Verifies ``GET /metrics`` emits the §3 guardrail counters and that the
per-turn counters increment on each guardrail fire. Reuses the e2e fixtures so
the full chat path runs against the deterministic fake provider.

Stage 0.8 (HU-1463) extends this to the §3 SLO *gauges* — the handoff SLA
telemetry (degrade rate, pending breach, answered-within-SLA rate) and the
``/health`` status mirrored into Prometheus on every scrape.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from huible.api.app import create_app
from huible.api.auth import InMemoryApiKeyStore, InMemoryPersonaRegistry
from huible.api.real_user_gate import REAL_USER_TRAFFIC_CLASS_HEADER
from huible.api.settings import Settings
from huible.llm.client import FakeLLMClient
from huible.safety import HandoffOutcome, HandoffTicket, InMemoryHandoffQueue
from tests.api.test_chat_e2e import API_KEY, PERSONA_ID, _persona, _seeded_backend


def _metric_total(text: str, name: str) -> float:
    """Sum every sample value for ``name`` across all label sets.

    Skips ``# HELP`` / ``# TYPE`` comments and the ``_created`` gauge companion
    that prometheus_client emits for histograms/summaries.
    """
    total = 0.0
    pattern = re.compile(rf"^{re.escape(name)}(?:\{{[^}}]*\}})?\s+([0-9.eE+-]+)")
    for line in text.splitlines():
        if line.startswith("#"):
            continue
        m = pattern.match(line)
        if m:
            total += float(m.group(1))
    return total


def _make_client(*, mode: str = "open") -> TestClient:
    settings = Settings(persona_chat_real_user_mode=mode)
    backend, _ = _seeded_backend()
    persona = _persona()
    registry = InMemoryPersonaRegistry({persona.id: (persona, backend)})
    keys = InMemoryApiKeyStore({API_KEY: PERSONA_ID}, read_env=False)
    application = create_app(
        api_key_store=keys,
        persona_registry=registry,
        llm_client=FakeLLMClient(persona_name="Chandler"),
        settings=settings,
        start_time=0.0,
    )
    return TestClient(application)


def _auth_headers(*, traffic: str = "internal") -> dict:
    return {"Authorization": f"Bearer {API_KEY}", REAL_USER_TRAFFIC_CLASS_HEADER: traffic}


def _consent(client: TestClient, conv: str) -> None:
    r = client.post(
        f"/api/v1/chat/{PERSONA_ID}/consent",
        json={"conversation_id": conv},
        headers=_auth_headers(),
    )
    assert r.status_code == 200, r.text


def _make_client_with_queue(queue: InMemoryHandoffQueue, *, mode: str = "open") -> TestClient:
    """Build a client wired to an explicit handoff queue (SLO gauge tests)."""
    settings = Settings(persona_chat_real_user_mode=mode)
    backend, _ = _seeded_backend()
    persona = _persona()
    registry = InMemoryPersonaRegistry({persona.id: (persona, backend)})
    keys = InMemoryApiKeyStore({API_KEY: PERSONA_ID}, read_env=False)
    application = create_app(
        api_key_store=keys,
        persona_registry=registry,
        llm_client=FakeLLMClient(persona_name="Chandler"),
        handoff_queue=queue,
        settings=settings,
        start_time=0.0,
    )
    return TestClient(application)


def _seed_ticket(
    queue: InMemoryHandoffQueue,
    *,
    ticket_id: str,
    outcome: HandoffOutcome = HandoffOutcome.ENQUEUED,
    seconds_old: int = 60,
    sla_target_seconds: int = 300,
) -> HandoffTicket:
    """Seed a handoff ticket with a controlled age + outcome (HU-1463 tests)."""
    created = datetime.now(UTC) - timedelta(seconds=seconds_old)
    ticket = HandoffTicket(
        id=ticket_id,
        persona_id=str(PERSONA_ID),
        conversation_id="sess-slo-gauge",
        trigger_signal="crisis",
        affect="crisis",
        risk_flags=[],
        sla_target_seconds=sla_target_seconds,
    )
    ticket.created_at = created.isoformat()
    ticket.outcome = outcome
    return queue.enqueue(ticket)


class TestMetricsEndpoint:
    def test_metrics_emits_guardrail_counters(self):
        client = _make_client()
        r = client.get("/metrics")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/plain")
        text = r.text
        # Every §3 instrument is declared + named in the exposition.
        for name in (
            "huible_chat_turns_total",
            "huible_chat_turn_latency_seconds",
            "huible_chat_errors_total",
            "huible_crisis_fires_total",
            "huible_consent_required_total",
            "huible_handoff_outcomes_total",
            "huible_ungrounded_claims_total",
            "huible_alignment_dispositions_total",
            "huible_risk_enforcement_actions_total",
            "huible_risk_flag_fires_total",
            "huible_real_user_refused_total",
        ):
            assert name in text, f"missing metric {name}"

    def test_metrics_emits_stage_0_8_slo_gauges(self):
        """Stage 0.8 (HU-1463): /metrics exposes the §3 SLO gauges."""
        client = _make_client()
        text = client.get("/metrics").text
        # Guardrail-health (§3.1) gauges.
        for name in (
            "huible_handoff_degrade_rate",
            "huible_handoff_pending_breached",
            "huible_handoff_pending_breach_rate",
            "huible_handoff_answered_within_sla_rate",
            "huible_handoff_tickets_total",
            "huible_handoff_pending",
        ):
            assert name in text, f"missing SLO gauge {name}"
        # Service-health (§3.2) gauge.
        assert "huible_health_status" in text


class TestSloGauges:
    """Stage 0.8 (HU-1463): the §3 SLO gauges mirror the handoff telemetry."""

    def test_health_status_gauge_is_one_when_ok(self):
        # No DB wired → status ok (the key-free default).
        client = _make_client()
        text = client.get("/metrics").text
        assert _metric_total(text, "huible_health_status") == 1.0

    def test_handoff_gauges_reflect_empty_queue(self):
        queue = InMemoryHandoffQueue(available_responders=1)
        client = _make_client_with_queue(queue)
        text = client.get("/metrics").text
        assert _metric_total(text, "huible_handoff_tickets_total") == 0.0
        assert _metric_total(text, "huible_handoff_pending") == 0.0
        assert _metric_total(text, "huible_handoff_degrade_rate") == 0.0
        assert _metric_total(text, "huible_handoff_pending_breached") == 0.0
        # Empty queue → answered-within-SLA rate is 1.0 (no misses).
        assert _metric_total(text, "huible_handoff_answered_within_sla_rate") == 1.0

    def test_degrade_rate_gauge_rises_on_degraded_ticket(self):
        # available_responders=0 forces every escalation to degrade.
        queue = InMemoryHandoffQueue(available_responders=0)
        _seed_ticket(
            queue,
            ticket_id="t-degrade",
            outcome=HandoffOutcome.DEGRADED,
        )
        client = _make_client_with_queue(queue)
        text = client.get("/metrics").text
        assert _metric_total(text, "huible_handoff_degrade_rate") == 1.0
        assert _metric_total(text, "huible_handoff_tickets_total") == 1.0

    def test_pending_breached_gauge_rises_on_overdue_ticket(self):
        queue = InMemoryHandoffQueue(available_responders=1)
        # Created 10 minutes ago, SLA target 300s → past SLA now.
        _seed_ticket(
            queue,
            ticket_id="t-breach",
            outcome=HandoffOutcome.ENQUEUED,
            seconds_old=600,
            sla_target_seconds=300,
        )
        client = _make_client_with_queue(queue)
        text = client.get("/metrics").text
        assert _metric_total(text, "huible_handoff_pending_breached") == 1.0
        assert _metric_total(text, "huible_handoff_pending") == 1.0
        assert _metric_total(text, "huible_handoff_pending_breach_rate") == 1.0

    def test_answered_within_sla_rate_gauge_falls_on_breach(self):
        queue = InMemoryHandoffQueue(available_responders=1)
        # One answered ticket that breached SLA (resolved 10 min after create,
        # SLA target 300s). The in-memory queue.resolve stamps resolved_at.
        ticket = _seed_ticket(
            queue,
            ticket_id="t-answered-miss",
            outcome=HandoffOutcome.ENQUEUED,
            seconds_old=600,
            sla_target_seconds=300,
        )
        queue.resolve(
            ticket.id,
            outcome=HandoffOutcome.ANSWERED,
            responder_id="r-1",
        )
        # Force resolved_at well past the SLA window.
        resolved_ts = datetime.fromisoformat(ticket.created_at) + timedelta(seconds=600)
        ticket.resolved_at = resolved_ts.isoformat()
        client = _make_client_with_queue(queue)
        text = client.get("/metrics").text
        # answered_breach_rate = 1/1 → answered_within_sla_rate = 0.0.
        assert _metric_total(text, "huible_handoff_answered_within_sla_rate") == 0.0

    def test_scrape_updates_gauges_to_current_queue_state(self):
        """The gauges track the live queue, not a frozen snapshot."""
        queue = InMemoryHandoffQueue(available_responders=1)
        client = _make_client_with_queue(queue)
        # First scrape: empty queue.
        text1 = client.get("/metrics").text
        assert _metric_total(text1, "huible_handoff_tickets_total") == 0.0
        # Seed a pending ticket and scrape again — gauge follows.
        _seed_ticket(queue, ticket_id="t-live", outcome=HandoffOutcome.ENQUEUED)
        text2 = client.get("/metrics").text
        assert _metric_total(text2, "huible_handoff_tickets_total") == 1.0
        assert _metric_total(text2, "huible_handoff_pending") == 1.0


class TestCountersIncrement:
    def test_persona_turn_increments_turns_and_latency(self):
        client = _make_client()
        conv = "sess-metrics-persona"
        _consent(client, conv)
        before = _metric_total(client.get("/metrics").text, "huible_chat_turns_total")
        r = client.post(
            f"/api/v1/chat/{PERSONA_ID}",
            json={"message": "tell me about fishing on the lake", "conversation_id": conv},
            headers=_auth_headers(),
        )
        assert r.status_code == 200, r.text
        after = _metric_total(client.get("/metrics").text, "huible_chat_turns_total")
        assert after > before, (before, after)
        # Latency histogram observed at least one persona turn.
        metrics_text = client.get("/metrics").text
        assert _metric_total(metrics_text, "huible_chat_turn_latency_seconds_count") >= 1

    def test_crisis_turn_increments_crisis_counter(self):
        client = _make_client()
        conv = "sess-metrics-crisis"
        _consent(client, conv)
        before = _metric_total(client.get("/metrics").text, "huible_crisis_fires_total")
        client.post(
            f"/api/v1/chat/{PERSONA_ID}",
            json={"message": "I want to kill myself", "conversation_id": conv},
            headers=_auth_headers(),
        )
        after = _metric_total(client.get("/metrics").text, "huible_crisis_fires_total")
        assert after > before, (before, after)

    def test_unconsented_turn_increments_consent_counter(self):
        client = _make_client()
        conv = "sess-metrics-noconsent"  # deliberately not pre-consented
        before = _metric_total(client.get("/metrics").text, "huible_consent_required_total")
        client.post(
            f"/api/v1/chat/{PERSONA_ID}",
            json={"message": "hi there", "conversation_id": conv},
            headers=_auth_headers(),
        )
        after = _metric_total(client.get("/metrics").text, "huible_consent_required_total")
        assert after > before, (before, after)

    def test_kill_switch_refusal_increments_refused_counter(self):
        client = _make_client(mode="off")
        before = _metric_total(client.get("/metrics").text, "huible_real_user_refused_total")
        client.post(
            f"/api/v1/chat/{PERSONA_ID}",
            json={"message": "hi there", "conversation_id": "sess-metrics-refused"},
            headers={"Authorization": f"Bearer {API_KEY}"},  # no internal header → real
        )
        after = _metric_total(client.get("/metrics").text, "huible_real_user_refused_total")
        assert after > before, (before, after)
