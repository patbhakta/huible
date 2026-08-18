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


def _labeled_value(text: str, name: str, **labels: str) -> float:
    """Return the sample value for ``name`` with an exact ``labels`` match.

    Returns 0.0 when the label set has not been initialized (no leak of that
    category yet) — the clinically correct value for an unseen category.
    """
    labelset = ",".join(f'{k}="{v}"' for k, v in labels.items())
    pattern = re.compile(rf"^{re.escape(name)}\{{{re.escape(labelset)}\}}\s+([0-9.eE+-]+)")
    for line in text.splitlines():
        if line.startswith("#"):
            continue
        m = pattern.match(line)
        if m:
            return float(m.group(1))
    return 0.0


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
            "huible_alignment_ungrounded_claims_total",
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

    def test_degrade_rate_gauge_excludes_degrades_outside_window(self):
        # HU-1865: the gauge aggregates the rolling telemetry window
        # (default 24h). A degrade older than the window must not pin it —
        # the 2026-08-18 incident had the all-time cumulative rate paging at
        # 100% indefinitely after one pre-staffing degrade.
        queue = InMemoryHandoffQueue(available_responders=0)
        _seed_ticket(
            queue,
            ticket_id="t-stale-degrade",
            outcome=HandoffOutcome.DEGRADED,
            seconds_old=25 * 3600,  # outside the default 24h window
        )
        client = _make_client_with_queue(queue)
        text = client.get("/metrics").text
        assert _metric_total(text, "huible_handoff_degrade_rate") == 0.0
        assert _metric_total(text, "huible_handoff_tickets_total") == 0.0

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


class TestAlignmentCategoryTelemetry:
    """HU-1461 — Clinical Advisor §7.4.2 monitoring ask.

    The per-category un-grounded claim signal must reach Prometheus (not just the
    per-turn ``trace.alignment`` JSON) so real-model drift in a single category
    (identity/advice/biographical/relationship) is visible on the SLO dashboard.
    """

    def test_per_category_counter_reflects_record_chat_turn_input(self):
        """A direct record_chat_turn call increments each category label exactly."""
        from huible.api.metrics import ChatTurnOutcome, record_chat_turn

        client = _make_client()
        name = "huible_alignment_ungrounded_claims_total"
        before_id = _labeled_value(client.get("/metrics").text, name, category="identity")
        before_rel = _labeled_value(client.get("/metrics").text, name, category="relationship")
        before_adv = _labeled_value(client.get("/metrics").text, name, category="advice")

        record_chat_turn(
            ChatTurnOutcome(
                outcome="persona",
                latency_s=0.01,
                ungrounded_claims=3,
                alignment_disposition="suppressed",
                ungrounded_by_category={"identity": 2, "relationship": 1},
            )
        )

        text = client.get("/metrics").text
        # Only the categories present in the mapping moved.
        assert _labeled_value(text, name, category="identity") - before_id == 2.0
        assert _labeled_value(text, name, category="relationship") - before_rel == 1.0
        # advice was not in the mapping → unchanged.
        assert _labeled_value(text, name, category="advice") - before_adv == 0.0

    def test_end_to_end_suppression_increments_biographical_category(self):
        """A suppressed biographical claim through the chat path reaches Prometheus."""
        # Build a client whose fake provider emits an un-grounded biographical
        # claim (mirrors tests/api/test_chat_alignment.py).
        backend, _ = _seeded_backend()
        persona = _persona()
        registry = InMemoryPersonaRegistry({persona.id: (persona, backend)})
        keys = InMemoryApiKeyStore({API_KEY: PERSONA_ID}, read_env=False)
        application = create_app(
            api_key_store=keys,
            persona_registry=registry,
            llm_client=FakeLLMClient(
                response="I lived in Marfa for twenty years.", persona_name="Chandler"
            ),
            settings=Settings(persona_chat_real_user_mode="open"),
            start_time=0.0,
        )
        client = TestClient(application)

        name = "huible_alignment_ungrounded_claims_total"
        before = _labeled_value(client.get("/metrics").text, name, category="biographical")
        conv = "sess-metrics-category-e2e"
        _consent(client, conv)
        r = client.post(
            f"/api/v1/chat/{PERSONA_ID}",
            json={"message": "where did you live?", "conversation_id": conv},
            headers=_auth_headers(),
        )
        assert r.status_code == 200, r.text
        # Sanity: the turn was suppressed (the claim was un-grounded).
        assert r.json()["trace"]["alignment"]["disposition"] == "suppressed"

        after = _labeled_value(client.get("/metrics").text, name, category="biographical")
        assert after - before >= 1.0, (before, after)
