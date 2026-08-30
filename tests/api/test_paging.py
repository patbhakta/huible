"""Unit tests for the on-call paging notifier (Stage 0.4, HU-1450).

Covers the :class:`Pager` Protocol + both providers + the factory + the SLA-
breach re-page path, key-free. Provider selection, the key-free fallback chain,
and the ack-SLA Sev-1 escalation are the load-bearing behaviors; the chat-path
integration (crisis enqueue actually pages) is covered in
``test_chat_paging.py``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from huible.api.paging import (
    PAGE_SEVERITY_CRISIS,
    PAGE_SEVERITY_SEV1,
    PAGE_TRIGGER_SLA_BREACH,
    HermesBridgePager,
    LoggingPager,
    OnCallContact,
    OnCallRoster,
    Pager,
    WebhookPager,
    build_multichannel_pager,
    build_pager,
    build_roster,
    escalate_sla_breaches,
)
from huible.safety import HandoffOutcome, HandoffTicket, InMemoryHandoffQueue


def _ticket(
    *,
    ticket_id: str = "hh-test",
    outcome: HandoffOutcome = HandoffOutcome.ENQUEUED,
    seconds_old: int = 60,
    sla_target_seconds: int = 300,
) -> HandoffTicket:
    created = datetime.now(UTC) - timedelta(seconds=seconds_old)
    ticket = HandoffTicket(
        id=ticket_id,
        persona_id="persona-1",
        conversation_id="sess-1",
        trigger_signal="crisis",
        affect="crisis",
        sla_target_seconds=sla_target_seconds,
    )
    ticket.created_at = created.isoformat()
    ticket.outcome = outcome
    ticket.responder_id = "pat-clinical" if outcome is HandoffOutcome.ENQUEUED else None
    return ticket


# --- LoggingPager ----------------------------------------------------------


class TestLoggingPager:
    def test_logging_pager_emits_critical_handoff_page_line(self, caplog):
        ticket = _ticket()
        pager = LoggingPager()
        with caplog.at_level(logging.CRITICAL, logger="huible.api.paging"):
            pager.page(ticket, severity=PAGE_SEVERITY_CRISIS, window="always")

        page_records = [r for r in caplog.records if r.message.startswith("handoff.page")]
        assert len(page_records) == 1
        record = page_records[0]
        assert record.levelno == logging.CRITICAL
        # Aggregate-safe fields ride on the log line (no PHI).
        assert "hh-test" in record.getMessage()
        assert PAGE_SEVERITY_CRISIS in record.getMessage()
        assert "always" in record.getMessage()
        assert "pat-clinical" in record.getMessage()

    def test_logging_pager_is_a_pager(self):
        assert isinstance(LoggingPager(), Pager)


# --- WebhookPager ----------------------------------------------------------


class TestWebhookPager:
    def test_webhook_pager_falls_back_to_log_when_url_empty(self, caplog):
        """Key-free default: an empty URL degrades to the LoggingPager."""
        ticket = _ticket()
        pager = WebhookPager("")
        with caplog.at_level(logging.CRITICAL, logger="huible.api.paging"):
            pager.page(ticket, severity=PAGE_SEVERITY_CRISIS, window="always")

        page_records = [r for r in caplog.records if r.message.startswith("handoff.page")]
        assert len(page_records) == 1
        assert page_records[0].levelno == logging.CRITICAL

    def test_webhook_pager_posts_json_when_url_set(self, monkeypatch):
        """A configured URL POSTs the Sev-1 payload to the webhook endpoint."""
        posted: list[dict] = []

        def _fake_post(url, *, json, timeout):
            posted.append({"url": url, "json": json, "timeout": timeout})
            response = httpx.Response(200, request=httpx.Request("POST", url))
            return response

        monkeypatch.setattr("huible.api.paging.httpx.post", _fake_post)
        ticket = _ticket()
        pager = WebhookPager("https://hooks.example.com/pagerduty")
        pager.page(ticket, severity=PAGE_SEVERITY_SEV1, window="always")

        assert len(posted) == 1
        call = posted[0]
        assert call["url"] == "https://hooks.example.com/pagerduty"
        # Aggregate-safe payload (no message text / session ids).
        assert call["json"]["ticket_id"] == "hh-test"
        assert call["json"]["severity"] == PAGE_SEVERITY_SEV1
        assert call["json"]["responder_id"] == "pat-clinical"

    def test_webhook_pager_falls_back_to_log_on_transport_error(self, monkeypatch, caplog):
        """A failed POST degrades to the log line — never drops the page."""
        def _boom(url, *, json, timeout):
            raise httpx.ConnectError("network down")

        monkeypatch.setattr("huible.api.paging.httpx.post", _boom)
        ticket = _ticket()
        pager = WebhookPager("https://hooks.example.com/slack")
        with caplog.at_level(logging.DEBUG, logger="huible.api.paging"):
            pager.page(ticket, severity=PAGE_SEVERITY_CRISIS, window="always")

        # The fallback CRITICAL page fired (distinct from the exception log line,
        # which shares the ``handoff.page`` message prefix).
        critical_pages = [
            r
            for r in caplog.records
            if r.message.startswith("handoff.page") and r.levelno == logging.CRITICAL
        ]
        assert len(critical_pages) == 1
        # The webhook failure was logged at ERROR (not raised).
        assert any("webhook failed" in r.message for r in caplog.records)

    def test_webhook_pager_falls_back_to_log_on_non_2xx(self, monkeypatch, caplog):
        """A non-2xx response degrades to the log line."""
        def _bad(url, *, json, timeout):
            return httpx.Response(500, request=httpx.Request("POST", url))

        monkeypatch.setattr("huible.api.paging.httpx.post", _bad)
        ticket = _ticket()
        pager = WebhookPager("https://hooks.example.com/slack")
        with caplog.at_level(logging.CRITICAL, logger="huible.api.paging"):
            pager.page(ticket, severity=PAGE_SEVERITY_CRISIS, window="always")

        page_records = [r for r in caplog.records if r.message.startswith("handoff.page")]
        assert len(page_records) == 1


# --- build_pager factory ---------------------------------------------------


class TestBuildPager:
    def test_default_provider_is_logging_pager(self):
        pager = build_pager(provider="log", webhook_url="")
        assert isinstance(pager, LoggingPager)

    def test_webhook_provider_with_url_returns_webhook_pager(self):
        pager = build_pager(
            provider="webhook", webhook_url="https://hooks.example.com/x"
        )
        assert isinstance(pager, WebhookPager)

    def test_webhook_provider_without_url_falls_back_to_log(self, caplog):
        """webhook selected but no URL → honest key-free log fallback."""
        pager = build_pager(provider="webhook", webhook_url="")
        # The WebhookPager with an empty URL delegates to LoggingPager.
        assert isinstance(pager, WebhookPager)
        ticket = _ticket()
        with caplog.at_level(logging.CRITICAL, logger="huible.api.paging"):
            pager.page(ticket, severity=PAGE_SEVERITY_CRISIS, window="always")
        page_records = [r for r in caplog.records if r.message.startswith("handoff.page")]
        assert len(page_records) == 1

    def test_unknown_provider_is_logging_pager(self):
        pager = build_pager(provider="bogus", webhook_url="https://x")
        assert isinstance(pager, LoggingPager)


# --- HermesBridgePager (C1 device channel, HU-2245) ------------------------


def _contacts(*whatsapp_ids: str) -> list[OnCallContact]:
    return [
        OnCallContact(seat_id=f"seat-{i}", whatsapp=w) for i, w in enumerate(whatsapp_ids)
    ]


class TestHermesBridgePager:
    def test_fans_out_to_every_whatsapp_contact(self, monkeypatch):
        """One POST /send per roster chat address, bridge contract shape."""
        posted: list[dict] = []

        def _fake_post(url, *, json, timeout):
            posted.append({"url": url, "json": json})
            return httpx.Response(200, request=httpx.Request("POST", url))

        monkeypatch.setattr("huible.api.paging.httpx.post", _fake_post)
        pager = HermesBridgePager("http://127.0.0.1:3000/")
        pager.page(
            _ticket(),
            severity=PAGE_SEVERITY_CRISIS,
            window="always",
            contacts=_contacts("111@s.whatsapp.net", "222@s.whatsapp.net"),
        )

        assert len(posted) == 2
        assert all(c["url"] == "http://127.0.0.1:3000/send" for c in posted)
        bodies = {c["json"]["chatId"] for c in posted}
        assert bodies == {"111@s.whatsapp.net", "222@s.whatsapp.net"}
        # Aggregate-safe body (no PHI): severity + trigger + ticket id + window.
        first = posted[0]["json"]["message"]
        assert "hh-test" in first and "crisis" in first

    def test_no_whatsapp_targets_degrades_to_log(self, caplog):
        pager = HermesBridgePager("http://127.0.0.1:3000")
        with caplog.at_level(logging.CRITICAL, logger="huible.api.paging"):
            failures = pager.page(
                _ticket(),
                severity=PAGE_SEVERITY_CRISIS,
                window="always",
                contacts=[OnCallContact(seat_id="ceo", email="ceo@example.com")],
            )
        page_records = [r for r in caplog.records if r.message.startswith("handoff.page")]
        assert len(page_records) == 1
        assert page_records[0].levelno == logging.CRITICAL
        assert failures == 0  # the log page itself succeeded

    def test_all_sends_failed_falls_back_to_log(self, monkeypatch, caplog):
        """Every device send failing still fires the honest log line."""

        def _boom(url, *, json, timeout):
            raise httpx.ConnectError("bridge down")

        monkeypatch.setattr("huible.api.paging.httpx.post", _boom)
        pager = HermesBridgePager("http://127.0.0.1:3000")
        with caplog.at_level(logging.CRITICAL, logger="huible.api.paging"):
            failures = pager.page(
                _ticket(),
                severity=PAGE_SEVERITY_CRISIS,
                window="always",
                contacts=_contacts("111@s.whatsapp.net"),
            )
        critical_pages = [
            r
            for r in caplog.records
            if r.message.startswith("handoff.page") and r.levelno == logging.CRITICAL
        ]
        assert len(critical_pages) == 1
        assert failures >= 1  # the failed send is counted, page never dropped

    def test_partial_failure_still_counts_but_no_log_duplicate(self, monkeypatch):
        """One channel failing while another lands: count it, no fallback page."""

        def _flaky(url, *, json, timeout):
            if json["chatId"].startswith("bad"):
                raise httpx.ConnectError("nope")
            return httpx.Response(200, request=httpx.Request("POST", url))

        monkeypatch.setattr("huible.api.paging.httpx.post", _flaky)
        pager = HermesBridgePager("http://127.0.0.1:3000")
        failures = pager.page(
            _ticket(),
            severity=PAGE_SEVERITY_CRISIS,
            window="always",
            contacts=_contacts("good@s.whatsapp.net", "bad@s.whatsapp.net"),
        )
        assert failures == 1


class TestBuildMultichannelHermes:
    def _roster(self) -> OnCallRoster:
        return build_roster(
            contacts_json=(
                '{"clinical-advisor": {"whatsapp": "185@lid"}, '
                '"ceo": {"whatsapp": "999@lid"}}'
            ),
            canary_start_ts="2026-08-18T15:20:53Z",
        )

    def test_hermes_provider_builds_device_channel(self):
        pager = build_multichannel_pager(
            provider="hermes",
            webhook_url="http://127.0.0.1:3000",
            roster=self._roster(),
            telnyx_api_key="",
            telnyx_from="",
            telnyx_api_base_url="",
            smtp_host="",
            smtp_port=25,
            smtp_user="",
            smtp_password="",
            email_from_addr="",
        )
        assert isinstance(pager, HermesBridgePager) or type(pager).__name__ == (
            "DrillSuppressingPager"
        )

    def test_roster_parses_whatsapp_seat_addresses(self):
        roster = self._roster()
        assert roster.contacts["clinical-advisor"].whatsapp == "185@lid"
        assert roster.contacts["ceo"].whatsapp == "999@lid"

    def test_log_provider_stays_logging_pager(self):
        pager = build_multichannel_pager(
            provider="log",
            webhook_url="",
            roster=self._roster(),
            telnyx_api_key="",
            telnyx_from="",
            telnyx_api_base_url="",
            smtp_host="",
            smtp_port=25,
            smtp_user="",
            smtp_password="",
            email_from_addr="",
        )
        assert isinstance(pager, LoggingPager)


# --- escalate_sla_breaches (Ack-SLA Sev-1 re-page) --------------------------


class TestEscalateSlaBreaches:
    def test_breached_enqueued_ticket_is_repaged_sev1(self):
        """An ENQUEUED ticket past its SLA triggers a Sev-1 re-page."""
        queue = InMemoryHandoffQueue(available_responders=1)
        breached = _ticket(seconds_old=400, sla_target_seconds=300)  # 400 > 300
        queue.enqueue(breached)
        paged: list[tuple] = []

        class _Recorder:
            def page(self, ticket, *, severity, window, **kwargs):
                paged.append((ticket.id, severity, window))

        count = escalate_sla_breaches(
            queue, _Recorder(), window="always"
        )
        assert count == 1
        assert paged == [(breached.id, PAGE_SEVERITY_SEV1, "always")]

    def test_within_sla_ticket_is_not_repaged(self):
        """A ticket still within its SLA is not re-paged."""
        queue = InMemoryHandoffQueue(available_responders=1)
        fresh = _ticket(seconds_old=60, sla_target_seconds=300)  # 60 < 300
        queue.enqueue(fresh)

        class _Recorder:
            def __init__(self):
                self.pages = 0

            def page(self, ticket, *, severity, window, **kwargs):
                self.pages += 1

        rec = _Recorder()
        count = escalate_sla_breaches(queue, rec, window="always")
        assert count == 0
        assert rec.pages == 0

    def test_degraded_ticket_is_never_repaged(self):
        """A degraded ticket has no responder paged → never re-paged here."""
        queue = InMemoryHandoffQueue(available_responders=0)  # everything degrades
        degraded = _ticket(
            outcome=HandoffOutcome.DEGRADED, seconds_old=999, sla_target_seconds=300
        )
        queue.enqueue(degraded)  # 0 responders → forced DEGRADED in queue

        class _Recorder:
            def __init__(self):
                self.pages = 0

            def page(self, ticket, *, severity, window, **kwargs):
                self.pages += 1

        rec = _Recorder()
        count = escalate_sla_breaches(queue, rec, window="always")
        assert count == 0
        assert rec.pages == 0

    def test_only_breached_tickets_repaged_in_mixed_queue(self):
        queue = InMemoryHandoffQueue(available_responders=1)
        queue.enqueue(_ticket(ticket_id="hh-fresh", seconds_old=60, sla_target_seconds=300))
        queue.enqueue(_ticket(ticket_id="hh-stale", seconds_old=400, sla_target_seconds=300))
        paged: list[str] = []

        class _Recorder:
            def page(self, ticket, *, severity, window, **kwargs):
                paged.append(ticket.id)

        count = escalate_sla_breaches(queue, _Recorder(), window="always")
        assert count == 1
        assert paged == ["hh-stale"]

    def test_breached_repaged_with_sla_breach_trigger_label(self):
        """The Sev-1 re-page carries the ``sla_breach`` trigger label (HU-1451)."""
        queue = InMemoryHandoffQueue(available_responders=1)
        queue.enqueue(_ticket(seconds_old=400, sla_target_seconds=300))
        seen: dict = {}

        class _Recorder:
            def page(self, ticket, *, severity, window, trigger="x", **kwargs):
                seen["trigger"] = trigger

        escalate_sla_breaches(queue, _Recorder(), window="always")
        assert seen["trigger"] == PAGE_TRIGGER_SLA_BREACH


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
