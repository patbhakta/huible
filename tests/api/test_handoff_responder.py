"""API tests for the staffed-responder handoff surface (HU-1428 AC #3 / AC #4).

Exercises the three responder routes against the full FastAPI wiring with the
deterministic in-memory queue, key-free:

* ``GET  /api/v1/handoff/tickets``            — pending work queue + live SLA.
* ``POST /api/v1/handoff/tickets/{id}/resolve`` — responder resolve + note.
* ``GET  /api/v1/handoff/audit``              — audit log + SLA/outcome telemetry.

Plus the auth gate (401 without a bearer key) and the 404 / 400 error paths on
resolve. The routes are the staffed-responder surface the Clinical Advisor must
see before real-user traffic; SLA breach detection + telemetry are AC #4.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient

from huible.api.app import create_app
from huible.api.auth import InMemoryApiKeyStore, InMemoryPersonaRegistry
from huible.safety import HandoffOutcome, HandoffTicket, InMemoryHandoffQueue

API_KEY = "key-handoff-responder"
PERSONA_ID = uuid4()


def _make_client(queue: InMemoryHandoffQueue) -> TestClient:
    keys = InMemoryApiKeyStore({API_KEY: PERSONA_ID}, read_env=False)
    registry = InMemoryPersonaRegistry()  # responder routes don't touch personas
    app = create_app(
        api_key_store=keys,
        persona_registry=registry,
        handoff_queue=queue,
        start_time=0.0,
    )
    return TestClient(app)


def _seed_ticket(
    queue: InMemoryHandoffQueue,
    *,
    ticket_id: str,
    outcome: HandoffOutcome = HandoffOutcome.ENQUEUED,
    seconds_old: int = 60,
    sla_target_seconds: int = 300,
) -> HandoffTicket:
    created = datetime.now(UTC) - timedelta(seconds=seconds_old)
    ticket = HandoffTicket(
        id=ticket_id,
        persona_id=str(PERSONA_ID),
        conversation_id="sess-responder",
        trigger_signal="crisis",
        affect="crisis",
        risk_flags=["recent_loss"],
        sla_target_seconds=sla_target_seconds,
    )
    ticket.created_at = created.isoformat()
    ticket.outcome = outcome
    return queue.enqueue(ticket)


_AUTH = {"Authorization": f"Bearer {API_KEY}"}


# --- auth gate --------------------------------------------------------------


class TestHandoffAuth:
    def test_list_pending_requires_bearer(self):
        client = _make_client(InMemoryHandoffQueue(available_responders=1))
        assert client.get("/api/v1/handoff/tickets").status_code == 401

    def test_resolve_requires_bearer(self):
        client = _make_client(InMemoryHandoffQueue(available_responders=1))
        assert (
            client.post(
                "/api/v1/handoff/tickets/whatever/resolve",
                json={"outcome": "answered"},
            ).status_code
            == 401
        )

    def test_audit_requires_bearer(self):
        client = _make_client(InMemoryHandoffQueue(available_responders=1))
        assert client.get("/api/v1/handoff/audit").status_code == 401


# --- GET /handoff/tickets (pending work queue) ------------------------------


class TestListPending:
    def test_empty_queue_returns_empty_list(self):
        client = _make_client(InMemoryHandoffQueue(available_responders=1))
        resp = client.get("/api/v1/handoff/tickets", headers=_AUTH)
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    def test_lists_only_pending_with_sla_status(self):
        queue = InMemoryHandoffQueue(available_responders=1)
        _seed_ticket(queue, ticket_id="hh-fresh", seconds_old=60)  # within SLA
        _seed_ticket(queue, ticket_id="hh-stale", seconds_old=400)  # breached
        client = _make_client(queue)
        resp = client.get("/api/v1/handoff/tickets", headers=_AUTH)
        assert resp.status_code == 200
        items = resp.json()["data"]
        assert {i["ticket_id"] for i in items} == {"hh-fresh", "hh-stale"}
        by_id = {i["ticket_id"]: i for i in items}
        # Fresh ticket: within SLA, not breached, positive countdown.
        assert by_id["hh-fresh"]["sla_status"]["breached"] is False
        assert by_id["hh-fresh"]["sla_status"]["seconds_to_sla"] > 0
        assert by_id["hh-fresh"]["sla_status"]["seconds_overdue"] == 0
        # Stale ticket: breached, negative countdown, overdue magnitude set.
        assert by_id["hh-stale"]["sla_status"]["breached"] is True
        assert by_id["hh-stale"]["sla_status"]["seconds_to_sla"] < 0
        assert by_id["hh-stale"]["sla_status"]["seconds_overdue"] > 0
        # Audit fields surface on the work queue row.
        assert by_id["hh-stale"]["trigger_signal"] == "crisis"
        assert by_id["hh-stale"]["risk_flags"] == ["recent_loss"]


# --- POST /handoff/tickets/{id}/resolve -------------------------------------


class TestResolve:
    def test_resolve_answered_records_note_and_stamps_resolved_at(self):
        queue = InMemoryHandoffQueue(available_responders=1)
        _seed_ticket(queue, ticket_id="hh-resolve")
        client = _make_client(queue)
        resp = client.post(
            "/api/v1/handoff/tickets/hh-resolve/resolve",
            headers=_AUTH,
            json={
                "outcome": "answered",
                "responder_id": "pat-clinician",
                "clinical_review_note": "user stabilized; warm handoff to ongoing care",
            },
        )
        assert resp.status_code == 200
        item = resp.json()["data"]
        assert item["outcome"] == "answered"
        assert item["responder_id"] == "pat-clinician"
        assert item["clinical_review_note"].startswith("user stabilized")
        assert item["resolved_at"] is not None
        # A resolved ticket no longer appears in the pending queue.
        pending = client.get("/api/v1/handoff/tickets", headers=_AUTH).json()["data"]
        assert pending == []

    def test_resolve_unknown_ticket_returns_404(self):
        client = _make_client(InMemoryHandoffQueue(available_responders=1))
        resp = client.post(
            "/api/v1/handoff/tickets/does-not-exist/resolve",
            headers=_AUTH,
            json={"outcome": "answered"},
        )
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"]["code"] == "TICKET_NOT_FOUND"

    def test_resolve_invalid_outcome_returns_400(self):
        queue = InMemoryHandoffQueue(available_responders=1)
        _seed_ticket(queue, ticket_id="hh-bad")
        client = _make_client(queue)
        resp = client.post(
            "/api/v1/handoff/tickets/hh-bad/resolve",
            headers=_AUTH,
            json={"outcome": "enqueued"},  # not a valid finalization outcome
        )
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"]["code"] == "INVALID_OUTCOME"
        # The ticket is untouched (still pending).
        pending = client.get("/api/v1/handoff/tickets", headers=_AUTH).json()["data"]
        assert {p["ticket_id"] for p in pending} == {"hh-bad"}


# --- GET /handoff/audit (audit log + telemetry) -----------------------------


class TestAudit:
    def test_audit_returns_log_and_telemetry(self):
        queue = InMemoryHandoffQueue(available_responders=0)  # degrade by default
        _seed_ticket(queue, ticket_id="hh-deg", seconds_old=60)  # 0 responders → degraded
        # Re-staff then enqueue + resolve one for an answered row.
        queue._available_responders = 1  # test-only roster swap
        _seed_ticket(queue, ticket_id="hh-ok", seconds_old=60)
        queue.resolve("hh-ok", outcome=HandoffOutcome.ANSWERED, clinical_review_note="ok")
        client = _make_client(queue)

        resp = client.get("/api/v1/handoff/audit", headers=_AUTH)
        assert resp.status_code == 200
        data = resp.json()["data"]
        ids = [t["ticket_id"] for t in data["tickets"]]
        assert set(ids) == {"hh-deg", "hh-ok"}
        # HU-1926 finding 2: rows also carry the ticket id under `id` so
        # §10.1 consumers reading that key see the audit key, not null.
        assert all(t["id"] == t["ticket_id"] for t in data["tickets"])
        tel = data["telemetry"]
        assert tel["total"] == 2
        assert tel["by_outcome"]["degraded"] == 1
        assert tel["by_outcome"]["answered"] == 1
        assert tel["degraded"] == 1
        assert tel["answered"] == 1
        assert tel["degrade_rate"] == 0.5
