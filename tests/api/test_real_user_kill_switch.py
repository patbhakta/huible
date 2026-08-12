"""Stage 0.7 hard kill switch tests — PERSONA_CHAT_REAL_USER_TRAFFIC (HU-1462).

Exercises the MANDATORY hard kill switch on ``POST /api/v1/chat/{persona_id}``
that is the primary rollback path for the real-user rollout (launch plan §4.2).
Distinct from the Stage 0.1 ramp gate (``PERSONA_CHAT_REAL_USER_MODE``,
``test_real_user_gate.py``): the hard switch is a boolean that refuses *every*
real-user turn with HTTP 503 ``SERVICE_DISABLED`` when OFF, regardless of the
ramp mode. Internal/synthetic traffic is unaffected. Crisis/handoff audit
still records on a refused turn (§10.1 invariant 5) — a grieving user in
crisis during a rollback is still routed to the §7.4.1 queue + the 988 line.

Refused real-user turn  → HTTP 503 ``{"error": {"code": "SERVICE_DISABLED",
                          "status": 503, "message": "…988…",
                          "crisis_detected": bool}}``.
Internal turn (switch OFF) → served normally (200 / 409 consent — never 503).
Real-user turn (switch ON)  → flows past the kill switch to the ramp gate /
                          consent gate (never 503 from the kill switch).

Engine-suite default (``tests/api/conftest.py``) leaves the ramp mode at
``open``; these tests pass explicit ``Settings(...)`` per case so the env
default does not interfere (pydantic-settings: constructor kwargs outrank env).
The kill switch default is ``off`` (the safe direction); tests that need it ON
pass ``persona_chat_real_user_traffic="on"`` explicitly.
"""

from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from huible.api.app import create_app
from huible.api.auth import InMemoryApiKeyStore, InMemoryPersonaRegistry
from huible.api.real_user_gate import (
    REAL_USER_TRAFFIC_CLASS_HEADER,
    SERVICE_DISABLED_MESSAGE,
    parse_real_user_traffic_switch,
)
from huible.api.settings import Settings
from huible.llm.client import FakeLLMClient
from huible.safety import InMemoryHandoffQueue, InMemoryRiskProfile

# Reuse the working e2e fixtures (persona + seeded backend).
from tests.api.test_chat_e2e import API_KEY, PERSONA_ID, _persona, _seeded_backend

#: A canonical G1 crisis phrase (matches ``test_chat_guardrails.py``).
CRISIS_MESSAGE = "I am going to kill myself"
PLAIN_MESSAGE = "tell me about fishing"


def _make_app(
    *,
    kill_switch: str = "off",
    ramp_mode: str = "open",
    canary_personas: str = "",
    queue: InMemoryHandoffQueue | None = None,
) -> TestClient:
    """Build an app with explicit kill-switch + ramp-gate state.

    ``ramp_mode`` defaults to ``open`` so the only variable under test is the
    hard kill switch (when ON, real-user traffic should flow past it).
    """
    settings = Settings(
        persona_chat_real_user_traffic=kill_switch,
        persona_chat_real_user_mode=ramp_mode,
        persona_chat_canary_personas=canary_personas,
    )
    backend, _ = _seeded_backend()
    persona = _persona()
    registry = InMemoryPersonaRegistry({persona.id: (persona, backend)})
    keys = InMemoryApiKeyStore({API_KEY: PERSONA_ID}, read_env=False)
    application = create_app(
        api_key_store=keys,
        persona_registry=registry,
        llm_client=FakeLLMClient(persona_name="Chandler"),
        handoff_queue=queue or InMemoryHandoffQueue(available_responders=0),
        risk_profile=InMemoryRiskProfile(),
        settings=settings,
        start_time=0.0,
    )
    return TestClient(application)


def _chat(
    client: TestClient,
    *,
    traffic_class: str | None,
    message: str = PLAIN_MESSAGE,
    conversation_id: str = "sess-kill",
) -> tuple[int, dict]:
    headers = {"Authorization": f"Bearer {API_KEY}"}
    if traffic_class is not None:
        headers[REAL_USER_TRAFFIC_CLASS_HEADER] = traffic_class
    r = client.post(
        f"/api/v1/chat/{PERSONA_ID}",
        json={"message": message, "conversation_id": conversation_id},
        headers=headers,
    )
    if r.headers.get("content-type", "").startswith("application/json"):
        return r.status_code, r.json()
    return r.status_code, {}


# ---------------------------------------------------------------------------
# Refusal cases — real-user turns return 503 SERVICE_DISABLED
# ---------------------------------------------------------------------------


class TestRefusedRealUserTurns:
    def test_off_refuses_real_user_no_header(self):
        """switch=off + unmarked client (default real) → 503 SERVICE_DISABLED."""
        client = _make_app(kill_switch="off")
        code, body = _chat(client, traffic_class=None)
        assert code == 503, (code, body)
        assert body["detail"]["error"]["code"] == "SERVICE_DISABLED"
        assert body["detail"]["error"]["status"] == 503
        assert body["detail"]["error"]["crisis_detected"] is False
        assert "988" in body["detail"]["error"]["message"]

    def test_off_refuses_explicit_real_header(self):
        client = _make_app(kill_switch="off")
        code, body = _chat(client, traffic_class="real")
        assert code == 503
        assert body["detail"]["error"]["code"] == "SERVICE_DISABLED"

    def test_off_refuses_unknown_header_value(self):
        """Unknown header value → treated as real (safe direction) → 503."""
        client = _make_app(kill_switch="off")
        code, body = _chat(client, traffic_class="bogus")
        assert code == 503
        assert body["detail"]["error"]["code"] == "SERVICE_DISABLED"

    def test_off_uses_service_disabled_message(self):
        client = _make_app(kill_switch="off")
        _code, body = _chat(client, traffic_class=None)
        assert body["detail"]["error"]["message"] == SERVICE_DISABLED_MESSAGE

    def test_off_overrides_open_ramp_mode(self):
        """Kill switch OFF refuses real-user even when ramp mode is ``open``.

        The hard kill switch is checked *before* the ramp gate and overrides it
        entirely — the ramp mode is irrelevant during a hard rollback.
        """
        client = _make_app(kill_switch="off", ramp_mode="open")
        code, body = _chat(client, traffic_class=None)
        assert code == 503
        assert body["detail"]["error"]["code"] == "SERVICE_DISABLED"

    def test_garbage_value_defaults_to_off(self):
        """An unrecognized kill-switch value defaults to OFF (safe direction)."""
        client = _make_app(kill_switch="definitely-not-a-bool")
        code, body = _chat(client, traffic_class=None)
        assert code == 503
        assert body["detail"]["error"]["code"] == "SERVICE_DISABLED"


# ---------------------------------------------------------------------------
# Crisis audit — handoff queue still records during a hard rollback
# ---------------------------------------------------------------------------


class TestCrisisAuditStillRecords:
    def test_off_crisis_turn_records_handoff_and_returns_503(self):
        """switch=off + real-user crisis → 503 + §7.4.1 queue records ticket.

        §10.1 invariant 5 ("audit every escalation") holds even under a hard
        rollback: the crisis classifier runs in the refusal path so a grieving
        user in crisis is still routed to the handoff queue, and the 503 body
        carries the crisis resources.
        """
        queue = InMemoryHandoffQueue(available_responders=1)
        client = _make_app(kill_switch="off", queue=queue)
        code, body = _chat(
            client,
            traffic_class=None,
            message=CRISIS_MESSAGE,
            conversation_id="sess-kill-crisis",
        )
        # Still 503 — the kill switch refuses the turn.
        assert code == 503
        assert body["detail"]["error"]["code"] == "SERVICE_DISABLED"
        assert body["detail"]["error"]["crisis_detected"] is True
        # The crisis resources (from the handoff acknowledgement) are the body.
        assert "988" in body["detail"]["error"]["message"]
        # The §7.4.1 queue recorded the escalation ticket.
        pending = queue.list_pending()
        assert len(pending) == 1
        assert pending[0].conversation_id == "sess-kill-crisis"
        assert pending[0].persona_id == str(PERSONA_ID)

    def test_off_non_crisis_turn_does_not_enqueue(self):
        """switch=off + real-user non-crisis → 503 + no handoff ticket."""
        queue = InMemoryHandoffQueue(available_responders=1)
        client = _make_app(kill_switch="off", queue=queue)
        _chat(
            client,
            traffic_class=None,
            message=PLAIN_MESSAGE,
            conversation_id="sess-kill-plain",
        )
        assert queue.list_pending() == []


# ---------------------------------------------------------------------------
# Internal/synthetic traffic unaffected — serves when switch is OFF
# ---------------------------------------------------------------------------


class TestInternalTrafficUnaffected:
    def _assert_served(self, code: int, body: dict) -> None:
        # Internal traffic flows past the kill switch → reaches the ramp gate /
        # consent gate / persona voice. Never a 503 from the kill switch.
        assert code != 503, (code, body)
        if code == 200:
            assert body.get("response") != SERVICE_DISABLED_MESSAGE

    def test_off_serves_internal_header(self):
        """switch=off + internal header → served (test suite / probes run)."""
        client = _make_app(kill_switch="off")
        code, body = _chat(client, traffic_class="internal")
        self._assert_served(code, body)

    def test_off_serves_internal_even_with_open_ramp(self):
        client = _make_app(kill_switch="off", ramp_mode="open")
        code, body = _chat(client, traffic_class="internal")
        self._assert_served(code, body)


# ---------------------------------------------------------------------------
# Switch ON — real-user traffic flows past the kill switch
# ---------------------------------------------------------------------------


class TestSwitchOnAllowsRealUser:
    def _assert_past_kill_switch(self, code: int, body: dict) -> None:
        # Past the kill switch → never a 503 SERVICE_DISABLED. The turn reaches
        # the ramp gate (200 warm refusal if ramp=off) or consent gate (409) or
        # the persona voice (200). All are valid — the kill switch let it by.
        assert code != 503, (code, body)
        if code == 200:
            err = body.get("error")
            assert err is None or err.get("code") != "SERVICE_DISABLED"

    def test_on_allows_real_user_no_header(self):
        """switch=on + unmarked client → flows past the kill switch."""
        client = _make_app(kill_switch="on", ramp_mode="open")
        code, body = _chat(client, traffic_class=None)
        self._assert_past_kill_switch(code, body)

    def test_on_allows_explicit_real_header(self):
        client = _make_app(kill_switch="on", ramp_mode="open")
        code, body = _chat(client, traffic_class="real")
        self._assert_past_kill_switch(code, body)

    def test_on_allows_internal(self):
        client = _make_app(kill_switch="on", ramp_mode="open")
        code, body = _chat(client, traffic_class="internal")
        self._assert_past_kill_switch(code, body)

    def test_on_with_ramp_off_refuses_via_ramp_gate_not_kill_switch(self):
        """switch=on + ramp=off → the ramp gate refuses (200 warm), NOT 503.

        Proves the two controls are distinct: with the kill switch ON, the ramp
        gate is the one that blocks (warm 200 non-persona response), not the
        hard kill switch (503).
        """
        from huible.api.real_user_gate import REAL_USER_MODE_OFF_RESPONSE

        client = _make_app(kill_switch="on", ramp_mode="off")
        code, body = _chat(client, traffic_class=None)
        assert code == 200
        assert body["response"] == REAL_USER_MODE_OFF_RESPONSE


# ---------------------------------------------------------------------------
# Admin status endpoint reports the kill-switch state
# ---------------------------------------------------------------------------


class TestAdminStatusReportsKillSwitch:
    def _status(self, client: TestClient) -> dict:
        r = client.get(
            "/api/v1/admin/real-user-mode",
            headers={"Authorization": f"Bearer {API_KEY}"},
        )
        assert r.status_code == 200, r.text
        return r.json()["data"]

    def test_off_reports_kill_switch_off(self):
        client = _make_app(kill_switch="off")
        data = self._status(client)
        assert data["kill_switch"] == "off"
        assert data["kill_switch_enabled"] is False

    def test_on_reports_kill_switch_on(self):
        client = _make_app(kill_switch="on")
        data = self._status(client)
        assert data["kill_switch"] == "on"
        assert data["kill_switch_enabled"] is True

    def test_ramp_mode_still_reported(self):
        """The Stage 0.1 ramp-mode fields are still present alongside."""
        client = _make_app(kill_switch="off", ramp_mode="canary", canary_personas=str(uuid4()))
        data = self._status(client)
        assert data["mode"] == "canary"
        assert data["is_off"] is False
        assert data["canary_persona_count"] == 1
        assert data["kill_switch"] == "off"


# ---------------------------------------------------------------------------
# Parser unit tests — boolean parsing, safe default
# ---------------------------------------------------------------------------


class TestParser:
    def test_on_spellings(self):
        for v in ("on", "ON", "On", "true", "TRUE", "1", "yes", "YES", "Yes"):
            assert parse_real_user_traffic_switch(v) is True, v

    def test_off_spellings_and_garbage_default_off(self):
        for v in (
            None,
            "",
            "off",
            "OFF",
            "false",
            "0",
            "no",
            "bogus",
            "maybe",
            "2",
        ):
            assert parse_real_user_traffic_switch(v) is False, v

    def test_whitespace_tolerant(self):
        assert parse_real_user_traffic_switch("  on  ") is True
        assert parse_real_user_traffic_switch("  ") is False


# ---------------------------------------------------------------------------
# Rollback dry-run (launch plan §4.3) — the proven path
# ---------------------------------------------------------------------------


class TestRollbackDryRun:
    """The exact §4.3 procedure: OFF → real refused + internal serves; ON → real serves.

    Models the operator dry-run that must pass before Stage 1 advance. This is
    the deterministic proof that the kill switch is the instant, verifiable
    rollback path independent of key revocation.
    """

    def test_full_rollback_dry_run(self):
        # ── Step 1: kill switch OFF ──────────────────────────────────────────
        c_off = _make_app(kill_switch="off", ramp_mode="open")

        # Real-user (grieving-user client, no traffic-class header) → refused.
        code_real_off, body_real_off = _chat(
            c_off, traffic_class=None, conversation_id="sess-dry-real-off"
        )
        assert code_real_off == 503
        assert body_real_off["detail"]["error"]["code"] == "SERVICE_DISABLED"

        # Internal/synthetic (probe) → still serves.
        code_int_off, _ = _chat(
            c_off, traffic_class="internal", conversation_id="sess-dry-int-off"
        )
        assert code_int_off != 503

        # ── Step 2: kill switch ON (process restart — settings are cached) ───
        c_on = _make_app(kill_switch="on", ramp_mode="open")

        # Real-user → now served (flows past the kill switch to the ramp gate /
        # consent / persona voice).
        code_real_on, _ = _chat(
            c_on, traffic_class=None, conversation_id="sess-dry-real-on"
        )
        assert code_real_on != 503

        # Internal/synthetic → still served.
        code_int_on, _ = _chat(
            c_on, traffic_class="internal", conversation_id="sess-dry-int-on"
        )
        assert code_int_on != 503
