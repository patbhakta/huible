"""Stage 0.3 metrics tests (HU-1446).

Verifies ``GET /metrics`` emits the §3 guardrail counters and that the
per-turn counters increment on each guardrail fire. Reuses the e2e fixtures so
the full chat path runs against the deterministic fake provider.
"""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

from huible.api.app import create_app
from huible.api.auth import InMemoryApiKeyStore, InMemoryPersonaRegistry
from huible.api.real_user_gate import REAL_USER_TRAFFIC_CLASS_HEADER
from huible.api.settings import Settings
from huible.llm.client import FakeLLMClient
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
