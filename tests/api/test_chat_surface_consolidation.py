"""HU-1926 regression suite — one chat surface, full §7.4 safety stack.

The HU-1911 Stage-A dogfood found the generic ``POST /api/v1/chat`` serving
persona-voiced LLM replies with **none** of the safety stack: a synthetic
crisis probe reached the persona LLM unaudited and unticketed, while the same
input on ``POST /api/v1/chat/{persona_id}`` was intercepted pre-LLM in
milliseconds with a §7.4.1 handoff ticket. This suite pins the
consolidation:

* The generic route is a permanent 308 redirect shim onto the persona-scoped
  surface and never generates by itself — so crisis input on **every** chat
  route is intercepted pre-LLM, the ramp gate / kill switch guard it, and the
  G6 consent gate holds on it (acceptance #3).
* A redirect-following client on the generic URL receives the scoped
  safety-stacked turn (acceptance #1: single chat surface carries the full
  G1/G6/ramp-gate/§7.4.1 stack).
"""

from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from huible.api.app import create_app
from huible.api.auth import InMemoryApiKeyStore, InMemoryPersonaRegistry
from huible.api.real_user_gate import (
    REAL_USER_MODE_OFF_RESPONSE,
)
from huible.api.settings import Settings
from huible.llm.client import FakeLLMClient
from huible.safety import InMemoryHandoffQueue
from tests.api.test_chat_e2e import API_KEY, PERSONA_ID, _persona, _seeded_backend

CRISIS_MESSAGE = "I want to die, I have the pills"

AUTH = {"Authorization": f"Bearer {API_KEY}"}


def _make_app(
    *,
    mode: str = "open",
    traffic: str = "on",
    llm: FakeLLMClient | None = None,
    queue: InMemoryHandoffQueue | None = None,
) -> tuple[TestClient, FakeLLMClient, InMemoryHandoffQueue]:
    fake_llm = llm or FakeLLMClient(persona_name="Chandler")
    handoff_queue = queue or InMemoryHandoffQueue(available_responders=1)
    backend, _ = _seeded_backend()
    persona = _persona()
    registry = InMemoryPersonaRegistry({persona.id: (persona, backend)})
    keys = InMemoryApiKeyStore({API_KEY: PERSONA_ID}, read_env=False)
    application = create_app(
        api_key_store=keys,
        persona_registry=registry,
        llm_client=fake_llm,
        handoff_queue=handoff_queue,
        settings=Settings(
            persona_chat_real_user_mode=mode,
            persona_chat_real_user_traffic=traffic,
        ),
        start_time=0.0,
    )
    return TestClient(application), fake_llm, handoff_queue


def _consent(client: TestClient, conv: str) -> str:
    r = client.post(
        f"/api/v1/chat/{PERSONA_ID}/consent",
        json={"conversation_id": conv},
        headers=AUTH,
    )
    assert r.status_code == 200, r.text
    return conv


# --- The shim itself: redirect, never generate ------------------------------


class TestGenericRouteIsRedirectShim:
    def test_authenticated_post_gets_308_to_scoped_surface(self):
        client, llm, _ = _make_app()
        r = client.post(
            "/api/v1/chat",
            json={"message": "hi"},
            headers=AUTH,
            follow_redirects=False,
        )
        assert r.status_code == 308
        assert r.headers["location"] == f"/api/v1/chat/{PERSONA_ID}"
        assert r.headers["deprecation"] == "true"
        # The shim generated nothing — no LLM call may ever happen here.
        assert llm.calls == []

    def test_scope_guard_fires_before_redirect(self):
        client, _, _ = _make_app()
        r = client.post(
            "/api/v1/chat",
            json={"message": "hi", "persona_id": str(uuid4())},
            headers=AUTH,
            follow_redirects=False,
        )
        assert r.status_code == 403
        assert r.json()["detail"]["error"]["code"] == "FORBIDDEN"

    def test_invalid_disclosure_tier_rejected_before_redirect(self):
        client, _, _ = _make_app()
        r = client.post(
            "/api/v1/chat",
            json={"message": "hi", "disclosure_tier": "world"},
            headers=AUTH,
            follow_redirects=False,
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error"]["code"] == "VALIDATION_ERROR"

    def test_shim_hit_is_counted(self):
        from prometheus_client import REGISTRY

        client, _, _ = _make_app()
        before = REGISTRY.get_sample_value("huible_generic_chat_shim_redirects_total") or 0
        client.post("/api/v1/chat", json={"message": "hi"}, headers=AUTH, follow_redirects=False)
        after = REGISTRY.get_sample_value("huible_generic_chat_shim_redirects_total") or 0
        assert after > before


# --- Acceptance #3: crisis input intercepted pre-LLM on EVERY chat route ----


class TestCrisisInterceptedPreLLMOnEveryRoute:
    def test_crisis_on_scoped_route_intercepted_pre_llm(self):
        client, llm, queue = _make_app()
        r = client.post(
            f"/api/v1/chat/{PERSONA_ID}",
            json={"message": CRISIS_MESSAGE, "conversation_id": "crisis-scoped"},
            headers=AUTH,
        )
        assert r.status_code == 200
        body = r.json()
        # Pre-LLM: the persona voice was never invoked.
        assert llm.calls == []
        # Non-persona crisis response with resources.
        assert "988" in body["response"]
        assert body["trace"]["safety_event"]["kind"] == "crisis_escalation"
        # Audited §7.4.1 handoff ticket exists.
        assert len(queue.audit_log()) == 1
        assert body["trace"]["handoff"]["ticket_id"] == queue.audit_log()[0].id

    def test_crisis_on_generic_route_intercepted_pre_llm(self):
        """The regression the dogfood found: generic route reached the LLM.

        With the shim, a redirect-following client on the generic URL gets
        the scoped safety-stacked turn instead — the crisis branch fires
        pre-LLM with an audited handoff ticket.
        """
        client, llm, queue = _make_app()
        r = client.post(
            "/api/v1/chat",
            json={"message": CRISIS_MESSAGE, "conversation_id": "crisis-generic"},
            headers=AUTH,  # TestClient follows the 308 by default
        )
        assert r.status_code == 200
        body = r.json()
        assert llm.calls == []
        assert "988" in body["response"]
        assert body["trace"]["safety_event"]["kind"] == "crisis_escalation"
        assert len(queue.audit_log()) == 1


# --- Acceptance #1: the single surface carries G6 + ramp gate + kill switch --


class TestShimLandsOnGatedSurface:
    def test_consent_gate_holds_via_generic_route(self):
        client, llm, _ = _make_app()
        r = client.post(
            "/api/v1/chat",
            json={"message": "tell me about fishing", "conversation_id": "cg-1"},
            headers=AUTH,
        )
        assert r.status_code == 409
        err = r.json()["detail"]["error"]
        assert err["code"] == "CONSENT_REQUIRED"
        assert err["acknowledge_url"] == f"/api/v1/chat/{PERSONA_ID}/consent"
        # No persona voice before the acknowledgment.
        assert llm.calls == []
        # Acknowledge via the scoped surface, then the same generic request
        # completes — the turn now runs on the stacked surface.
        _consent(client, "cg-1")
        r2 = client.post(
            "/api/v1/chat",
            json={"message": "tell me about fishing", "conversation_id": "cg-1"},
            headers=AUTH,
        )
        assert r2.status_code == 200
        assert r2.json()["response"]
        assert len(llm.calls) == 1

    def test_ramp_gate_refuses_real_user_via_generic_route(self):
        client, llm, _ = _make_app(mode="off")
        r = client.post(
            "/api/v1/chat",
            json={"message": "tell me about fishing", "conversation_id": "rg-1"},
            headers=AUTH,  # no traffic-class header -> REAL (safe default)
        )
        assert r.status_code == 200
        body = r.json()
        assert body["response"] == REAL_USER_MODE_OFF_RESPONSE
        assert body["trace"]["safety_event"]["kind"] == "real_user_mode_off"
        assert llm.calls == []

    def test_kill_switch_503s_real_user_via_generic_route(self):
        client, llm, _ = _make_app(mode="open", traffic="off")
        r = client.post(
            "/api/v1/chat",
            json={"message": "tell me about fishing", "conversation_id": "ks-1"},
            headers=AUTH,
        )
        assert r.status_code == 503
        assert r.json()["detail"]["error"]["code"] == "SERVICE_DISABLED"
        assert llm.calls == []


# --- Finding 2: audit rows carry the ticket id -------------------------------


class TestAuditViewCarriesTicketId:
    def test_audit_rows_have_non_null_id_matching_ticket_id(self):
        from huible.safety import HandoffOutcome, HandoffTicket

        queue = InMemoryHandoffQueue(available_responders=0)  # degrade by default
        ticket = HandoffTicket(
            id="hh-1926-audit",
            persona_id=str(PERSONA_ID),
            conversation_id="sess-1926",
            trigger_signal="crisis",
            affect="crisis",
            risk_flags=[],
            sla_target_seconds=300,
        )
        queue.enqueue(ticket)
        queue.resolve("hh-1926-audit", outcome=HandoffOutcome.ABANDONED)

        keys = InMemoryApiKeyStore({API_KEY: PERSONA_ID}, read_env=False)
        app = create_app(
            api_key_store=keys,
            persona_registry=InMemoryPersonaRegistry(),
            handoff_queue=queue,
            start_time=0.0,
        )
        client = TestClient(app)
        r = client.get("/api/v1/handoff/audit", headers=AUTH)
        assert r.status_code == 200
        rows = r.json()["data"]["tickets"]
        row = next(t for t in rows if t["ticket_id"] == "hh-1926-audit")
        assert row["id"] is not None
        assert row["id"] == row["ticket_id"]
