"""Stage 0.1 real-user ramp gate / kill switch tests (HU-1444).

Exercises ``POST /api/v1/chat/{persona_id}`` against every combination of the
``PERSONA_CHAT_REAL_USER_MODE`` ramp state and the ``X-Huible-Traffic-Class``
header. The gate is the rollback spine for the HU-1436 rollout: when the
switch is off (the code default), a grieving-user turn is refused with a warm,
non-persona response carrying crisis-line resources — never the deceased-persona
voice. Internal/synthetic traffic is unaffected so the test suite and probes
keep running.

Refused turn  → HTTP 200 ``PersonaChatResponse`` with
                ``trace.safety_event.kind == "real_user_mode_off"`` and the
                warm non-persona ``REAL_USER_MODE_OFF_RESPONSE`` body.
Allowed turn  → the turn flows past the gate to the next guardrail (here the
                G6 consent gate, HTTP 409 ``CONSENT_REQUIRED``) — proof the
                kill switch let it through.

Engine-suite default (``tests/api/conftest.py``) sets the mode to ``open``;
these tests pass explicit ``Settings(...)`` per case so the env default does
not interfere (pydantic-settings: constructor kwargs outrank env).
"""

from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from huible.api.app import create_app
from huible.api.auth import InMemoryApiKeyStore, InMemoryPersonaRegistry
from huible.api.real_user_gate import (
    REAL_USER_MODE_OFF_RESPONSE,
    REAL_USER_TRAFFIC_CLASS_HEADER,
)
from huible.api.settings import Settings
from huible.llm.client import FakeLLMClient

# Reuse the working e2e fixtures (persona + seeded backend) so the "allowed"
# path has a real binding to resolve.
from tests.api.test_chat_e2e import API_KEY, PERSONA_ID


def _make_app_with_settings(
    *,
    mode: str,
    canary_personas: str = "",
) -> TestClient:
    """Build an app with an explicit ramp-gate mode (kwarg wins over env)."""
    settings = Settings(
        persona_chat_real_user_mode=mode,
        persona_chat_canary_personas=canary_personas,
    )
    from tests.api.test_chat_e2e import _persona, _seeded_backend

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


def _chat(
    client: TestClient,
    *,
    traffic_class: str | None,
    message: str = "hi there",
    conversation_id: str = "sess-gate",
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
# Refusal cases — never the persona voice
# ---------------------------------------------------------------------------


class TestRefusedRealUserTurns:
    def test_off_refuses_real_user_no_header(self):
        """mode=off + unmarked client (default real) → warm non-persona refusal."""
        client = _make_app_with_settings(mode="off")
        code, body = _chat(client, traffic_class=None)
        assert code == 200
        assert body["response"] == REAL_USER_MODE_OFF_RESPONSE
        assert body["trace"]["safety_event"]["kind"] == "real_user_mode_off"
        assert body["trace"]["safety_event"]["resources_shown"] is True
        assert "988" in body["response"]

    def test_off_refuses_explicit_real_header(self):
        client = _make_app_with_settings(mode="off")
        code, body = _chat(client, traffic_class="real")
        assert code == 200
        assert body["response"] == REAL_USER_MODE_OFF_RESPONSE
        assert body["trace"]["safety_event"]["kind"] == "real_user_mode_off"

    def test_off_refuses_unknown_header_value(self):
        """Unknown header value → treated as real (safe direction)."""
        client = _make_app_with_settings(mode="off")
        code, body = _chat(client, traffic_class="bogous")
        assert code == 200
        assert body["trace"]["safety_event"]["kind"] == "real_user_mode_off"

    def test_canary_refuses_non_allowlisted_persona(self):
        """mode=canary + persona NOT on allowlist → refused."""
        other = uuid4()
        client = _make_app_with_settings(mode="canary", canary_personas=str(other))
        code, body = _chat(client, traffic_class=None)
        assert code == 200
        assert body["trace"]["safety_event"]["kind"] == "real_user_mode_off"

    def test_garbage_mode_defaults_to_off(self):
        """An unrecognized mode setting defaults to OFF (safe direction)."""
        client = _make_app_with_settings(mode="definitely-not-a-mode")
        code, body = _chat(client, traffic_class=None)
        assert code == 200
        assert body["trace"]["safety_event"]["kind"] == "real_user_mode_off"


# ---------------------------------------------------------------------------
# Allowed cases — turn flows past the gate (reaches the G6 consent gate, 409)
# ---------------------------------------------------------------------------


class TestAllowedTurns:
    def _assert_past_gate(self, code: int, body: dict) -> None:
        # Past the kill switch → the next gate (G6 consent) fires 409, OR a full
        # persona turn returns 200. Either way it is NOT the refusal.
        assert code in (200, 409), (code, body)
        if code == 200:
            assert body["response"] != REAL_USER_MODE_OFF_RESPONSE
            se = body["trace"].get("safety_event")
            assert se is None or se["kind"] != "real_user_mode_off"

    def test_off_allows_internal_traffic(self):
        """mode=off + internal header → allowed (test suite / probes unaffected)."""
        client = _make_app_with_settings(mode="off")
        code, body = _chat(client, traffic_class="internal")
        self._assert_past_gate(code, body)

    def test_canary_allows_allowlisted_persona(self):
        client = _make_app_with_settings(
            mode="canary", canary_personas=str(PERSONA_ID)
        )
        code, body = _chat(client, traffic_class=None)
        self._assert_past_gate(code, body)

    def test_canary_allows_internal_regardless_of_allowlist(self):
        client = _make_app_with_settings(mode="canary", canary_personas=str(uuid4()))
        code, body = _chat(client, traffic_class="internal")
        self._assert_past_gate(code, body)

    def test_open_allows_real_user(self):
        client = _make_app_with_settings(mode="open")
        code, body = _chat(client, traffic_class=None)
        self._assert_past_gate(code, body)

    def test_open_allows_unmarked_client(self):
        client = _make_app_with_settings(mode="open")
        code, body = _chat(client, traffic_class=None)
        self._assert_past_gate(code, body)


# ---------------------------------------------------------------------------
# Admin status endpoint (kill-switch drill / monitoring surface)
# ---------------------------------------------------------------------------


class TestAdminRealUserModeStatus:
    def _status(self, client: TestClient) -> dict:
        r = client.get(
            "/api/v1/admin/real-user-mode",
            headers={"Authorization": f"Bearer {API_KEY}"},
        )
        assert r.status_code == 200, r.text
        return r.json()["data"]

    def test_off_status_reports_off(self):
        client = _make_app_with_settings(mode="off")
        data = self._status(client)
        assert data["mode"] == "off"
        assert data["is_off"] is True
        assert data["canary_persona_count"] == 0

    def test_canary_status_reports_count(self):
        other = uuid4()
        client = _make_app_with_settings(mode="canary", canary_personas=str(other))
        data = self._status(client)
        assert data["mode"] == "canary"
        assert data["is_off"] is False
        assert data["canary_persona_count"] == 1

    def test_open_status_reports_open(self):
        client = _make_app_with_settings(mode="open")
        data = self._status(client)
        assert data["mode"] == "open"
        assert data["is_off"] is False
