"""C2 coverage gate tests — PERSONA_CHAT_COVERAGE_ENFORCEMENT (HU-2245).

Exercises the CA-floor C2 gate (HU-2244 verdict, armed via HU-2245): when
``PERSONA_CHAT_COVERAGE_ENFORCEMENT=on``, a real-user persona-chat turn is
admitted only inside the handoff coverage window (the same
``HANDOFF_COVERAGE_*`` settings the §7.4.1 queue evaluates — single source of
truth). Out-of-window real turns are refused with the warm non-persona
response + 988 — never the deceased-persona voice, never 503 (503 stays
reserved for the Stage 0.7 rollback signal). The crisis classifier still runs
in the refusal path (§10.1 invariant 5): a grieving user in crisis outside
the window is still routed to the handoff queue, which degrades honestly.
Internal/synthetic traffic is unaffected, and the kill switch keeps
precedence. Default ``off`` → zero behaviour change until entry activation.

Test-clock strategy: the coverage check consults
``application.state.chat_coverage_now`` when set (an aware-datetime factory);
each test pins a fixed "now" inside/outside a deterministic window so the
assertions never depend on wall-clock time.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from huible.api.app import create_app
from huible.api.auth import InMemoryApiKeyStore, InMemoryPersonaRegistry
from huible.api.real_user_gate import REAL_USER_MODE_OFF_RESPONSE
from huible.api.settings import Settings
from huible.llm.client import FakeLLMClient
from huible.safety import InMemoryHandoffQueue, InMemoryRiskProfile

# Reuse the working e2e fixtures (persona + seeded backend).
from tests.api.test_chat_e2e import (
    API_KEY,
    PERSONA_ID,
    _consent,
    _persona,
    _seeded_backend,
)
from tests.api.test_real_user_kill_switch import (
    CRISIS_MESSAGE,
    PLAIN_MESSAGE,
    REAL_USER_TRAFFIC_CLASS_HEADER,
)

#: Fixed aware "now" values relative to a 09:00-17:00 UTC window.
_NOON_UTC = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)  # inside 09-17
_TWO_AM_UTC = datetime(2026, 8, 28, 2, 0, tzinfo=UTC)  # outside 09-17


def _make_app(
    *,
    enforcement: str = "off",
    coverage_mode: str = "hours",
    open_hour: int = 9,
    close_hour: int = 17,
    tz: str = "UTC",
    kill_switch: str = "on",
    ramp_mode: str = "open",
    queue: InMemoryHandoffQueue | None = None,
    now: datetime | None = _NOON_UTC,
) -> TestClient:
    """Build an app with the coverage gate in an explicit, deterministic state.

    ``kill_switch`` defaults ON and ``ramp_mode`` to ``open`` so the only
    variable under test is the C2 coverage gate. ``now`` installs the test
    clock override (None → the app uses the real current time).
    """
    settings = Settings(
        persona_chat_real_user_traffic=kill_switch,
        persona_chat_real_user_mode=ramp_mode,
        persona_chat_coverage_enforcement=enforcement,
        handoff_coverage_mode=coverage_mode,
        handoff_coverage_tz=tz,
        handoff_coverage_open_hour=open_hour,
        handoff_coverage_close_hour=close_hour,
    )
    backend, _ = _seeded_backend()
    persona = _persona()
    registry = InMemoryPersonaRegistry({persona.id: (persona, backend)})
    keys = InMemoryApiKeyStore({API_KEY: PERSONA_ID}, read_env=False)
    application = create_app(
        api_key_store=keys,
        persona_registry=registry,
        llm_client=FakeLLMClient(persona_name="Chandler"),
        handoff_queue=queue or InMemoryHandoffQueue(available_responders=1),
        risk_profile=InMemoryRiskProfile(),
        settings=settings,
        start_time=0.0,
    )
    application.state.chat_coverage_now = (lambda t=now: t) if now is not None else None
    return TestClient(application)


def _chat(
    client: TestClient,
    *,
    traffic_class: str | None,
    message: str = PLAIN_MESSAGE,
    conversation_id: str = "sess-cov",
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


def _coverage_refusals() -> float:
    from prometheus_client import REGISTRY

    return REGISTRY.get_sample_value("huible_chat_coverage_refused_total") or 0.0


# ---------------------------------------------------------------------------
# Armed + out-of-window — real-user turns refused (warm 200, never persona)
# ---------------------------------------------------------------------------


class TestOutOfWindowRefusal:
    def test_out_of_window_real_turn_refused_warm_200(self):
        """Armed + closed window + real turn → 200 warm non-persona response."""
        before = _coverage_refusals()
        client = _make_app(enforcement="on", now=_TWO_AM_UTC)
        code, body = _chat(client, traffic_class=None, conversation_id="sess-cov-1")
        assert code == 200, (code, body)
        assert body["response"] == REAL_USER_MODE_OFF_RESPONSE
        assert "988" in body["response"]
        # Persona voice never reached.
        assert "Chandler" not in body["response"]
        # Metric fired exactly once for this refusal.
        assert _coverage_refusals() == before + 1

    def test_out_of_window_explicit_real_header_refused(self):
        client = _make_app(enforcement="on", now=_TWO_AM_UTC)
        code, body = _chat(client, traffic_class="real", conversation_id="sess-cov-2")
        assert code == 200
        assert body["response"] == REAL_USER_MODE_OFF_RESPONSE

    def test_out_of_window_unknown_header_treated_as_real(self):
        """Unknown header value → real (safe direction) → refused."""
        client = _make_app(enforcement="on", now=_TWO_AM_UTC)
        code, body = _chat(client, traffic_class="bogus", conversation_id="sess-cov-3")
        assert code == 200
        assert body["response"] == REAL_USER_MODE_OFF_RESPONSE

    def test_refusal_carries_coverage_closed_safety_event(self):
        client = _make_app(enforcement="on", now=_TWO_AM_UTC)
        _code, body = _chat(client, traffic_class=None, conversation_id="sess-cov-4")
        assert body["trace"]["safety_event"]["kind"] == "coverage_closed"
        assert body["trace"]["safety_event"]["resources_shown"] is True


# ---------------------------------------------------------------------------
# Armed + in-window — real-user turns flow past the gate
# ---------------------------------------------------------------------------


class TestInWindowAdmission:
    def test_in_window_real_turn_served(self):
        """Armed + open window (noon in 09-17 UTC) → real turn flows through."""
        client = _make_app(enforcement="on", now=_NOON_UTC)
        _consent(client, "sess-cov-5")
        code, body = _chat(client, traffic_class=None, conversation_id="sess-cov-5")
        assert code == 200, (code, body)
        assert body["response"] != REAL_USER_MODE_OFF_RESPONSE

    def test_tz_conversion_honoured(self):
        """The window is evaluated in its own timezone (08:00-22:00 ET floor).

        12:00 UTC = 08:00 EDT — the first in-window minute of the verdict's
        C2 configuration (open boundary is inclusive).
        """
        et_open_edge = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)  # 08:00 America/New_York
        et_closed_edge = datetime(2026, 8, 28, 11, 59, tzinfo=UTC)  # 07:59 ET
        open_client = _make_app(
            enforcement="on",
            open_hour=8,
            close_hour=22,
            tz="America/New_York",
            now=et_open_edge,
        )
        _consent(open_client, "sess-cov-et-1")
        code_open, _ = _chat(open_client, traffic_class=None, conversation_id="sess-cov-et-1")
        assert code_open == 200

        closed_client = _make_app(
            enforcement="on",
            open_hour=8,
            close_hour=22,
            tz="America/New_York",
            now=et_closed_edge,
        )
        code_closed, body_closed = _chat(
            closed_client, traffic_class=None, conversation_id="sess-cov-et-2"
        )
        assert code_closed == 200
        assert body_closed["response"] == REAL_USER_MODE_OFF_RESPONSE


# ---------------------------------------------------------------------------
# Internal/synthetic traffic unaffected + default unarmed
# ---------------------------------------------------------------------------


class TestUnaffectedPaths:
    def test_internal_traffic_served_out_of_window(self):
        """Armed + closed window + internal header → served (probes keep running)."""
        client = _make_app(enforcement="on", now=_TWO_AM_UTC)
        _consent(client, "sess-cov-6")
        code, body = _chat(client, traffic_class="internal", conversation_id="sess-cov-6")
        assert code == 200, (code, body)
        assert body["response"] != REAL_USER_MODE_OFF_RESPONSE

    def test_default_off_no_behaviour_change(self):
        """Enforcement unset (default) + closed window → real turn still served.

        The load-bearing deploy property: shipping the gate armed-by-default
        would change canary behaviour on deploy. Default OFF keeps the flip at
        entry activation.
        """
        client = _make_app(enforcement="off", now=_TWO_AM_UTC)
        _consent(client, "sess-cov-7")
        code, body = _chat(client, traffic_class=None, conversation_id="sess-cov-7")
        assert code == 200, (code, body)
        assert body["response"] != REAL_USER_MODE_OFF_RESPONSE

    def test_garbage_enforcement_value_defaults_off(self):
        client = _make_app(enforcement="definitely-not-a-bool", now=_TWO_AM_UTC)
        _consent(client, "sess-cov-8")
        code, body = _chat(client, traffic_class=None, conversation_id="sess-cov-8")
        assert code == 200
        assert body["response"] != REAL_USER_MODE_OFF_RESPONSE


# ---------------------------------------------------------------------------
# Composition — kill switch + ramp gate keep precedence
# ---------------------------------------------------------------------------


class TestGateComposition:
    def test_kill_switch_off_overrides_coverage(self):
        """Kill switch off + closed window → 503 SERVICE_DISABLED (not warm 200).

        The Stage 0.7 hard kill switch is checked first; the coverage gate is
        never reached during a hard rollback.
        """
        client = _make_app(enforcement="on", kill_switch="off", now=_TWO_AM_UTC)
        code, body = _chat(client, traffic_class=None, conversation_id="sess-cov-9")
        assert code == 503
        assert body["detail"]["error"]["code"] == "SERVICE_DISABLED"

    def test_ramp_gate_off_overrides_coverage(self):
        """Ramp mode off + closed window → ramp refusal (mode-off safety event)."""
        client = _make_app(enforcement="on", ramp_mode="off", now=_TWO_AM_UTC)
        code, body = _chat(client, traffic_class=None, conversation_id="sess-cov-10")
        assert code == 200
        assert body["response"] == REAL_USER_MODE_OFF_RESPONSE
        assert body["trace"]["safety_event"]["kind"] == "real_user_mode_off"


# ---------------------------------------------------------------------------
# Crisis audit — §10.1 invariant 5 holds in the coverage refusal path
# ---------------------------------------------------------------------------


class TestCrisisAuditStillRecords:
    def test_out_of_window_crisis_routes_to_handoff(self):
        """Armed + closed window + crisis message → handoff queue records ticket.

        The refusal body is the §7.4.1 acknowledgement (988 resources); the
        queue degrades honestly out-of-window (never claims a person is
        joining when nobody is on-shift).
        """
        queue = InMemoryHandoffQueue(available_responders=1)
        client = _make_app(enforcement="on", queue=queue, now=_TWO_AM_UTC)
        code, body = _chat(
            client,
            traffic_class=None,
            message=CRISIS_MESSAGE,
            conversation_id="sess-cov-crisis",
        )
        assert code == 200
        assert "988" in body["response"]
        pending = queue.list_pending()
        assert len(pending) == 1
        assert pending[0].conversation_id == "sess-cov-crisis"
        assert pending[0].persona_id == str(PERSONA_ID)

    def test_out_of_window_non_crisis_does_not_enqueue(self):
        queue = InMemoryHandoffQueue(available_responders=1)
        client = _make_app(enforcement="on", queue=queue, now=_TWO_AM_UTC)
        _chat(client, traffic_class=None, conversation_id="sess-cov-plain")
        assert queue.list_pending() == []


# ---------------------------------------------------------------------------
# Admin status — the entry-drill verification surface
# ---------------------------------------------------------------------------


class TestAdminStatusReportsCoverageGate:
    def _status(self, client: TestClient) -> dict:
        r = client.get(
            "/api/v1/admin/real-user-mode",
            headers={"Authorization": f"Bearer {API_KEY}"},
        )
        assert r.status_code == 200, r.text
        return r.json()["data"]

    def test_reports_armed_state_and_window(self):
        client = _make_app(enforcement="on", now=_NOON_UTC)
        data = self._status(client)
        assert data["coverage_enforcement"] == "on"
        assert data["coverage_enforcement_enabled"] is True
        assert data["coverage_window"] == "hours 09:00-17:00 UTC"
        assert data["coverage_open_now"] is True

    def test_reports_disarmed_state(self):
        client = _make_app(enforcement="off", now=_TWO_AM_UTC)
        data = self._status(client)
        assert data["coverage_enforcement"] == "off"
        assert data["coverage_enforcement_enabled"] is False

    def test_reports_closed_now_out_of_window(self):
        client = _make_app(enforcement="on", now=_TWO_AM_UTC)
        data = self._status(client)
        assert data["coverage_open_now"] is False

    def test_kill_switch_fields_still_present(self):
        client = _make_app(enforcement="on", kill_switch="on")
        data = self._status(client)
        assert data["kill_switch"] == "on"
        assert data["mode"] == "open"


# ---------------------------------------------------------------------------
# Settings parse — safe default
# ---------------------------------------------------------------------------


class TestSettingsParse:
    def test_default_off(self):
        assert Settings().persona_chat_coverage_enforced is False

    def test_on_spellings(self):
        for v in ("on", "ON", "true", "1", "yes"):
            s = Settings(persona_chat_coverage_enforcement=v)
            assert s.persona_chat_coverage_enforced is True, v

    def test_garbage_defaults_off(self):
        for v in ("", "off", "bogus", "maybe"):
            assert (
                Settings(persona_chat_coverage_enforcement=v).persona_chat_coverage_enforced
                is False
            ), v
