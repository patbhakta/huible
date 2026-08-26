"""Drill-traffic paging suppression (HU-1428 pre-work, digest #5 watch item).

Once the real paging channels (Telnyx SMS / email / webhook) gain credentials
at roster activation, verification drills (verify-*, probe-full —
``demo-``-marked conversation ids on-box, ``sess-drill``-style ids in the
suite) must not ring a real on-call human device. The
:class:`~huible.api.paging.DrillSuppressingPager` wraps the multi-channel
pager: drill-marked tickets page the log line only and are reported through
the ``on_suppressed`` callback (wired to
``huible_paging_drill_suppressed_total`` in the app); real traffic pages the
real channels unchanged, including the ack-SLA escalation fan-out.

Key-free: real transports are faked with a monkeypatched ``httpx.post`` so
the suite needs no Telnyx / SMTP / webhook credentials.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from huible.api.paging import (
    DEFAULT_DRILL_MARKERS,
    PAGE_TRIGGER_CRISIS_ENQUEUE,
    PAGE_TRIGGER_SLA_BREACH,
    DrillSuppressingPager,
    LoggingPager,
    build_multichannel_pager,
    build_roster,
    escalate_sla_breaches,
    ticket_is_drill,
)
from huible.safety import HandoffOutcome, HandoffTicket, InMemoryHandoffQueue


def _ticket(
    *,
    ticket_id: str = "hh-1",
    conversation_id: str = "sess-1",
    outcome: HandoffOutcome = HandoffOutcome.ENQUEUED,
    seconds_old: int = 60,
    sla_target_seconds: int = 300,
) -> HandoffTicket:
    created = datetime.now(UTC) - timedelta(seconds=seconds_old)
    ticket = HandoffTicket(
        id=ticket_id,
        persona_id="persona-1",
        conversation_id=conversation_id,
        trigger_signal="crisis",
        affect="crisis",
        sla_target_seconds=sla_target_seconds,
    )
    ticket.created_at = created.isoformat()
    ticket.outcome = outcome
    ticket.responder_id = "clinical-advisor" if outcome is HandoffOutcome.ENQUEUED else None
    return ticket


def _roster():
    return build_roster(
        contacts_json=(
            '{"clinical-advisor": {"phone": "+15550000001"}, '
            '"ceo": {"phone": "+15550000002"}, '
            '"huible-pm": {"phone": "+15550000003"}, '
            '"huible-tech-lead": {"phone": "+15550000004"}}'
        ),
        canary_start_ts=datetime.now(UTC).isoformat(),
    )


class _PostRecorder:
    """Fake httpx.post that records every outbound real-channel POST."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json})
        return type("_Resp", (), {"raise_for_status": lambda self: None})()


@pytest.fixture()
def posts(monkeypatch):
    recorder = _PostRecorder()
    monkeypatch.setattr("huible.api.paging.httpx.post", recorder)
    return recorder


def _real_channel_pager(**overrides):
    kwargs = dict(
        provider="webhook",
        webhook_url="https://pager.example/hook",
        roster=_roster(),
        telnyx_api_key="test-key",
        telnyx_from="+15550000000",
        telnyx_api_base_url="https://telnyx.example/v2",
        smtp_host="",
        smtp_port=587,
        smtp_user="",
        smtp_password="",
        email_from_addr="",
    )
    kwargs.update(overrides)
    return build_multichannel_pager(**kwargs)


# --- marker matching --------------------------------------------------------


class TestTicketIsDrill:
    def test_demo_conversation_id_matches_default_markers(self):
        assert ticket_is_drill(
            _ticket(conversation_id="demo-40a0d5374138"),
            ("demo-", "drill"),
        )

    def test_sess_drill_id_matches(self):
        assert ticket_is_drill(_ticket(conversation_id="sess-drill"), ("demo-", "drill"))

    def test_matching_is_case_insensitive(self):
        assert ticket_is_drill(_ticket(conversation_id="DEMO-abc"), ("demo-",))

    def test_ticket_id_and_persona_id_are_matched(self):
        assert ticket_is_drill(_ticket(ticket_id="drill-hh-9"), ("demo-", "drill"))
        drill_persona = _ticket()
        drill_persona.persona_id = "drill-persona"
        assert ticket_is_drill(drill_persona, ("demo-", "drill"))

    def test_real_traffic_never_matches(self):
        assert not ticket_is_drill(
            _ticket(ticket_id="hh-real", conversation_id="sess-real-user"), ("demo-", "drill")
        )

    def test_empty_markers_disables_the_check(self):
        assert not ticket_is_drill(_ticket(conversation_id="demo-x"), ())


# --- suppression through the built pager ------------------------------------


class TestBuildMultichannelPagerSuppression:
    def test_drill_ticket_never_reaches_real_channels(self, posts, caplog):
        suppressed: list[str] = []
        pager = _real_channel_pager(on_suppressed=suppressed.append)
        assert isinstance(pager, DrillSuppressingPager)

        with caplog.at_level("CRITICAL", logger="huible.api.paging"):
            failures = pager.page(
                _ticket(conversation_id="demo-40a0d5374138"),
                severity="crisis",
                window="always",
                trigger=PAGE_TRIGGER_CRISIS_ENQUEUE,
            )

        assert failures == 0
        assert posts.calls == []  # no webhook, no SMS — log line only
        assert suppressed == [PAGE_TRIGGER_CRISIS_ENQUEUE]
        assert any(
            "handoff.page" in r.message and r.levelname == "CRITICAL"
            for r in caplog.records
        )

    def test_real_ticket_pages_real_channels(self, posts):
        suppressed: list[str] = []
        pager = _real_channel_pager(on_suppressed=suppressed.append)

        failures = pager.page(
            _ticket(conversation_id="sess-real-user"),
            severity="crisis",
            window="always",
            trigger=PAGE_TRIGGER_CRISIS_ENQUEUE,
        )

        assert failures == 0
        assert suppressed == []
        urls = " ".join(c["url"] for c in posts.calls)
        assert "https://pager.example/hook" in urls  # webhook channel fired
        assert "https://telnyx.example/v2/messages" in urls  # SMS channel fired

    def test_empty_markers_disables_suppression(self, posts):
        pager = _real_channel_pager(drill_markers="")
        assert not isinstance(pager, DrillSuppressingPager)

        pager.page(
            _ticket(conversation_id="demo-40a0d5374138"),
            severity="crisis",
            window="always",
        )
        assert posts.calls  # real channels attempted

    def test_key_free_default_is_never_wrapped(self):
        pager = _real_channel_pager(provider="log", webhook_url="", telnyx_api_key="")
        assert isinstance(pager, LoggingPager)

    def test_default_markers_are_the_documented_pair(self):
        assert DEFAULT_DRILL_MARKERS == "demo-,drill"


# --- ack-SLA escalation through the wrapper ---------------------------------


class TestEscalationThroughWrapper:
    def test_breached_drill_ticket_is_suppressed_not_escalated(self, posts):
        suppressed: list[str] = []
        pager = _real_channel_pager(on_suppressed=suppressed.append)
        queue = InMemoryHandoffQueue(available_responders=1)
        queue.enqueue(
            _ticket(ticket_id="hh-drill", conversation_id="demo-drill", seconds_old=3600)
        )

        count = escalate_sla_breaches(queue, pager, window="always")

        assert count == 1
        assert posts.calls == []  # suppressed — no real channel
        assert suppressed == [PAGE_TRIGGER_SLA_BREACH]

    def test_breached_real_ticket_escalates_to_ceo_through_wrapper(self, posts):
        pager = _real_channel_pager()
        queue = InMemoryHandoffQueue(available_responders=1)
        queue.enqueue(
            _ticket(ticket_id="hh-real", conversation_id="sess-real-user", seconds_old=3600)
        )

        count = escalate_sla_breaches(queue, pager, window="always")

        assert count == 1
        assert posts.calls  # escalated page reached real channels
        # The CEO seat joined the escalation (escalated=True survived the
        # wrapper) — at least one SMS target is the CEO phone.
        sms = [c for c in posts.calls if c["url"].endswith("/messages")]
        ceo_texts = [
            c["json"] for c in sms if c["json"]["to"] == "+15550000002"
        ]
        assert ceo_texts, "CEO seat missing from escalation fan-out"
