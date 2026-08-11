"""Stage 0.4 chat-path paging integration tests (HU-1450).

Exercises the full FastAPI wiring to prove the alert→on-call paging link:

* Crisis enqueue (G1 **and** risk-driven) invokes the pager when
  ``outcome == ENQUEUED`` and does **not** page on ``DEGRADED``.
* ``huible_alert_oncall_configured`` reads ``1`` when responders are staffed and
  ``0`` otherwise.
* An unacked ``ENQUEUED`` ticket past ``HANDOFF_SLA_TARGET_SECONDS`` triggers a
  Sev-1 re-page on the staffed-responder queue read.
* **Paging drill**: a real test page delivered (``handoff.page`` CRITICAL log
  line captured on a crisis turn) **and** acknowledged (a roster responder
  ``resolve()`` on the ticket) — the pre-real-user gate evidence.

Key-free: the deterministic ``LoggingPager`` (default) is the transport under
test, so CI needs no external pager credentials.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from huible.api.app import create_app
from huible.api.auth import InMemoryApiKeyStore, InMemoryPersonaRegistry
from huible.api.paging import (
    PAGE_SEVERITY_CRISIS,
    PAGE_SEVERITY_SEV1,
    PAGE_TRIGGER_DEGRADED_NET,
    Pager,
)
from huible.api.real_user_gate import REAL_USER_TRAFFIC_CLASS_HEADER
from huible.api.settings import Settings
from huible.llm.client import FakeLLMClient
from huible.safety import (
    DEFAULT_HANDOFF_SLA_SECONDS,
    HandoffTicket,
    InMemoryHandoffQueue,
    InMemoryRiskProfile,
    RiskFlag,
)
from huible.safety.handoff_monitoring import sla_status
from tests.api.test_chat_e2e import API_KEY, PERSONA_ID, _persona, _seeded_backend


class _RecordingPager:
    """Test-double pager that records every page for assertion."""

    def __init__(self) -> None:
        self.pages: list[tuple[str, str, str]] = []
        self.triggers: list[str] = []

    def page(self, ticket: HandoffTicket, *, severity: str, window: str, **kwargs) -> int:
        self.pages.append((ticket.id, severity, window))
        self.triggers.append(kwargs.get("trigger", "unspecified"))
        return 0  # no real-channel failures in the test double


def _make_app(
    *,
    queue: InMemoryHandoffQueue | None = None,
    pager: Pager | None = None,
    risk_profile: InMemoryRiskProfile | None = None,
    responders: int = 0,
    settings: Settings | None = None,
) -> TestClient:
    backend, _ = _seeded_backend()
    persona = _persona()
    registry = InMemoryPersonaRegistry({persona.id: (persona, backend)})
    keys = InMemoryApiKeyStore({API_KEY: PERSONA_ID}, read_env=False)
    used_settings = settings or Settings()
    app = create_app(
        api_key_store=keys,
        persona_registry=registry,
        llm_client=FakeLLMClient(persona_name="Chandler"),
        handoff_queue=queue or InMemoryHandoffQueue(available_responders=responders),
        risk_profile=risk_profile,
        pager=pager,
        settings=used_settings,
        start_time=0.0,
    )
    return TestClient(app)


def _auth_headers(*, traffic: str = "internal") -> dict:
    return {"Authorization": f"Bearer {API_KEY}", REAL_USER_TRAFFIC_CLASS_HEADER: traffic}


def _consent(client: TestClient, conv: str) -> None:
    r = client.post(
        f"/api/v1/chat/{PERSONA_ID}/consent",
        json={"conversation_id": conv},
        headers=_auth_headers(),
    )
    assert r.status_code == 200, r.text


def _post_crisis(client: TestClient, conv: str) -> dict:
    r = client.post(
        f"/api/v1/chat/{PERSONA_ID}",
        json={"message": "I want to die, I have the pills", "conversation_id": conv},
        headers=_auth_headers(),
    )
    assert r.status_code == 200, r.text
    return r.json()


# --- Crisis enqueue invokes the pager ---------------------------------------


class TestCrisisEnqueuePages:
    def test_g1_crisis_enqueued_invokes_pager(self):
        """G1 crisis → ENQUEUED → the on-call is paged at severity=crisis."""
        queue = InMemoryHandoffQueue(
            available_responders=1, responder_id_pool=("pat-clinical",)
        )
        pager = _RecordingPager()
        client = _make_app(queue=queue, pager=pager, responders=1)
        _consent(client, "sess-page-g1")
        body = _post_crisis(client, "sess-page-g1")

        assert body["trace"]["handoff"]["outcome"] == "enqueued"
        assert len(pager.pages) == 1
        ticket_id, severity, window = pager.pages[0]
        assert ticket_id == body["trace"]["handoff"]["ticket_id"]
        assert severity == PAGE_SEVERITY_CRISIS
        assert window == "always"

    def test_g1_crisis_degraded_pages_sev1_net_failure(self):
        """G1 crisis → DEGRADED → §3 Sev-1 (B) net-failure page (HU-1451 #3).

        Supersedes the HU-1450 "degraded does not page" contract: a degraded
        ticket means no responder was available — a grieving user in crisis was
        NOT helped by a human, which is itself a Sev-1 operational failure. The
        page is distinct from the crisis-enqueue page (no responder is "on it");
        it carries the ``degraded_net`` trigger so the ceiling intervenes.
        """
        queue = InMemoryHandoffQueue(available_responders=0)  # fail-safe degrade
        pager = _RecordingPager()
        client = _make_app(queue=queue, pager=pager)
        _consent(client, "sess-page-deg")
        body = _post_crisis(client, "sess-page-deg")

        assert body["trace"]["handoff"]["outcome"] == "degraded"
        # The net-failure page fired at Sev-1 (not the crisis-enqueue severity).
        assert len(pager.pages) == 1
        assert pager.pages[0][1] == PAGE_SEVERITY_SEV1
        assert pager.triggers == [PAGE_TRIGGER_DEGRADED_NET]

    def test_risk_driven_handoff_enqueued_invokes_pager(self):
        """§7.4.4 risk-driven handoff → ENQUEUED → the on-call is paged."""
        queue = InMemoryHandoffQueue(
            available_responders=1, responder_id_pool=("pat-clinical",)
        )
        pager = _RecordingPager()
        profile = InMemoryRiskProfile()
        # recent_loss + escalating distress trend → risk-driven handoff.
        profile.set_persona_flags(PERSONA_ID, {RiskFlag.RECENT_LOSS})
        client = _make_app(queue=queue, pager=pager, risk_profile=profile, responders=1)
        conv = "sess-page-risk"
        _consent(client, conv)
        # Turn 1: distress (recorded into history).
        client.post(
            f"/api/v1/chat/{PERSONA_ID}",
            json={"message": "I am heartbroken and crying", "conversation_id": conv},
            headers=_auth_headers(),
        )
        # Turn 2: still distress → trend rising → risk-driven handoff.
        r = client.post(
            f"/api/v1/chat/{PERSONA_ID}",
            json={"message": "the pain is unbearable", "conversation_id": conv},
            headers=_auth_headers(),
        )
        assert r.status_code == 200, r.text
        body = r.json()

        assert body["trace"]["handoff"]["outcome"] == "enqueued"
        assert len(pager.pages) == 1
        assert pager.pages[0][1] == PAGE_SEVERITY_CRISIS

    def test_non_crisis_turn_never_pages(self):
        """A persona-voiced turn (no escalation) never pages the on-call."""
        queue = InMemoryHandoffQueue(available_responders=1)
        pager = _RecordingPager()
        client = _make_app(queue=queue, pager=pager, responders=1)
        conv = "sess-page-none"
        _consent(client, conv)
        client.post(
            f"/api/v1/chat/{PERSONA_ID}",
            json={"message": "tell me about fishing on the lake", "conversation_id": conv},
            headers=_auth_headers(),
        )
        assert pager.pages == []


# --- Gauge flip -------------------------------------------------------------


class TestAlertOncallConfiguredGauge:
    def _gauge_value(self, text: str) -> float:
        pat = re.compile(r"^huible_alert_oncall_configured\s+([0-9.eE+-]+)")
        for line in text.splitlines():
            m = pat.match(line)
            if m:
                return float(m.group(1))
        raise AssertionError("gauge not found in /metrics")

    def test_gauge_reads_one_when_responders_staffed(self):
        """HANDOFF_AVAILABLE_RESPONDERS>0 → gauge flips to 1 (alerts wired)."""
        # The gauge reflects the configured roster (settings), which is the
        # production wiring signal — independent of a test-injected queue.
        client = _make_app(settings=Settings(handoff_available_responders=4))
        r = client.get("/metrics")
        assert r.status_code == 200
        assert self._gauge_value(r.text) == 1.0

    def test_gauge_reads_zero_when_no_roster(self):
        """HANDOFF_AVAILABLE_RESPONDERS=0 → gauge stays 0 (pre-roster)."""
        client = _make_app(settings=Settings(handoff_available_responders=0))
        r = client.get("/metrics")
        assert r.status_code == 200
        assert self._gauge_value(r.text) == 0.0


# --- Ack-SLA Sev-1 re-page on queue read ------------------------------------


class TestAckSlaSev1Repage:
    def test_breached_ticket_repaged_on_queue_read(self):
        """An unacked ENQUEUED ticket past SLA → Sev-1 re-page on GET /handoff/tickets."""
        queue = InMemoryHandoffQueue(
            available_responders=1, responder_id_pool=("pat",), sla_target_seconds=300
        )
        pager = _RecordingPager()
        client = _make_app(queue=queue, pager=pager, responders=1)
        # Seed a ticket whose created_at is well past the SLA.
        created = datetime.now(UTC) - timedelta(seconds=600)  # 600 > 300
        ticket = HandoffTicket(
            id="hh-sev1",
            persona_id=str(PERSONA_ID),
            conversation_id="sess-sev1",
            trigger_signal="crisis",
            affect="crisis",
            sla_target_seconds=300,
        )
        ticket.created_at = created.isoformat()
        queue.enqueue(ticket)  # staffed → ENQUEUED

        # The queue read triggers the Sev-1 re-page.
        r = client.get("/api/v1/handoff/tickets", headers=_auth_headers())
        assert r.status_code == 200
        # One Sev-1 page fired for the breached ticket.
        sev1 = [p for p in pager.pages if p[1] == PAGE_SEVERITY_SEV1]
        assert len(sev1) == 1
        assert sev1[0][0] == "hh-sev1"

    def test_within_sla_ticket_not_repaged_on_queue_read(self):
        queue = InMemoryHandoffQueue(
            available_responders=1, responder_id_pool=("pat",), sla_target_seconds=300
        )
        pager = _RecordingPager()
        client = _make_app(queue=queue, pager=pager, responders=1)
        # Fresh ticket (within SLA).
        ticket = HandoffTicket(
            id="hh-fresh",
            persona_id=str(PERSONA_ID),
            conversation_id="sess-fresh",
            trigger_signal="crisis",
            affect="crisis",
            sla_target_seconds=300,
        )
        queue.enqueue(ticket)

        client.get("/api/v1/handoff/tickets", headers=_auth_headers())
        assert pager.pages == []


# --- Paging drill: real page delivered + acknowledged -----------------------


class TestPagingDrill:
    """Pre-real-user paging-drill evidence (AC #6): page delivered AND acknowledged.

    A real test page (the ``handoff.page`` CRITICAL log line on the default
    LoggingPager) is captured on a crisis turn, then a roster responder
    resolves the ticket. This is the end-to-end drill the runbook records
    before real grieving-user traffic flows.
    """

    def test_drill_page_captured_and_ticket_resolved(self, caplog):
        queue = InMemoryHandoffQueue(
            available_responders=1,
            responder_id_pool=("huible-tech-lead",),
            sla_target_seconds=DEFAULT_HANDOFF_SLA_SECONDS,
        )
        client = _make_app(queue=queue, responders=1)  # real LoggingPager default
        _consent(client, "sess-drill")

        with caplog.at_level(logging.CRITICAL, logger="huible.api.paging"):
            body = _post_crisis(client, "sess-drill")

        ticket_id = body["trace"]["handoff"]["ticket_id"]
        assert body["trace"]["handoff"]["outcome"] == "enqueued"
        assert body["trace"]["handoff"]["responder_id"] == "huible-tech-lead"

        # 1) Page delivered: the handoff.page CRITICAL line was captured.
        page_records = [
            r for r in caplog.records if r.message.startswith("handoff.page")
        ]
        assert len(page_records) == 1
        assert page_records[0].levelno == logging.CRITICAL
        assert ticket_id in page_records[0].getMessage()
        assert PAGE_SEVERITY_CRISIS in page_records[0].getMessage()

        # 2) Page acknowledged: a roster responder resolves the ticket.
        r = client.post(
            f"/api/v1/handoff/tickets/{ticket_id}/resolve",
            headers=_auth_headers(),
            json={
                "outcome": "answered",
                "responder_id": "huible-tech-lead",
                "clinical_review_note": "drill: user stabilized; warm handoff to ongoing care",
            },
        )
        assert r.status_code == 200, r.text
        resolved = r.json()["data"]
        assert resolved["outcome"] == "answered"
        assert resolved["resolved_at"] is not None
        # The resolved ticket cleared the work queue (no longer pending).
        pending = client.get(
            "/api/v1/handoff/tickets", headers=_auth_headers()
        ).json()["data"]
        assert pending == []

        # The ticket was answered within SLA (the drill responder was fast).
        raw = queue.get(ticket_id)
        assert sla_status(raw).breached is False


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
