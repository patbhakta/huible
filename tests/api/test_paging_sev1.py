"""Unit tests for the Stage 0.4a §3 Sev-1 paging channel build (HU-1451).

Covers the HU-1451 additions on top of the HU-1450 wire:

* :class:`OnCallRoster` window resolution + escalation targeting.
* :class:`TelnyxSmsPager` / :class:`EmailPager` / :class:`MultiChannelPager`
  — key-free fallback, real-channel send, and failure-count reporting.
* :func:`build_roster` parsing of the ``HANDOFF_ONCALL_CONTACTS`` JSON +
  ``HANDOFF_CANARY_START_TS`` clock.
* :func:`page_degraded_net` + :func:`page_sev1_signal` trigger helpers.
* :func:`escalate_sla_breaches` roster-aware escalation (primary + secondary +
  CEO on an ack-SLA miss).
* :func:`record_paging_failures` — the ``huible_paging_failures_total`` counter.

Key-free: every real-channel pager falls back to :class:`LoggingPager` when its
credentials are absent, so the suite runs without Telnyx / SMTP / webhook creds.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from huible.api.metrics import record_paging_failures
from huible.api.paging import (
    DEFAULT_COVERAGE_PRESSURE_THRESHOLD,
    PAGE_SEVERITY_SEV1,
    PAGE_TRIGGER_CONSENT_BYPASS,
    PAGE_TRIGGER_COVERAGE_PRESSURE,
    PAGE_TRIGGER_DEGRADED_NET,
    PAGE_TRIGGER_SLA_BREACH,
    PAGE_TRIGGER_UNGROUNDED_LEAK,
    DrillSuppressingPager,
    EmailPager,
    LoggingPager,
    MultiChannelPager,
    OnCallContact,
    OnCallRoster,
    TelnyxSmsPager,
    build_multichannel_pager,
    build_roster,
    escalate_coverage_pressure,
    escalate_sla_breaches,
    page_degraded_net,
    page_sev1_signal,
)
from huible.safety import HandoffOutcome, HandoffTicket, InMemoryHandoffQueue


def _ticket(
    *,
    ticket_id: str = "hh-sev1",
    outcome: HandoffOutcome = HandoffOutcome.ENQUEUED,
    seconds_old: int = 60,
    sla_target_seconds: int = 300,
    degrade_reason: str | None = None,
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
    ticket.degrade_reason = degrade_reason
    ticket.responder_id = "clinical-advisor" if outcome is HandoffOutcome.ENQUEUED else None
    return ticket


def _contacts() -> dict[str, OnCallContact]:
    return {
        "clinical-advisor": OnCallContact("clinical-advisor", "+15550000001", "ca@huible.example"),
        "ceo": OnCallContact("ceo", "+15550000002", "ceo@huible.example"),
        "huible-pm": OnCallContact("huible-pm", "+15550000003", "pm@huible.example"),
        "huible-tech-lead": OnCallContact("huible-tech-lead", "+15550000004", "tl@huible.example"),
    }


# --- OnCallRoster resolution ----------------------------------------------


class TestOnCallRoster:
    def test_unconfigured_roster_resolves_all_none(self):
        """No canary-start → empty resolution → pager falls back to log."""
        roster = OnCallRoster()
        assert roster.resolve() == (None, None, None)
        assert roster.targets(escalated=False) == []
        assert roster.targets(escalated=True) == []

    def test_w1_window_resolves_clinical_advisor_primary_ceo_secondary(self):
        """T+0 → W1: primary=clinical-advisor, secondary=ceo (HU-1447 §1)."""
        canary_start = datetime.now(UTC) - timedelta(hours=1)  # inside W1
        roster = OnCallRoster(
            windows=[
                ("clinical-advisor", "ceo"),
                ("huible-pm", "clinical-advisor"),
                ("huible-tech-lead", "huible-pm"),
                ("clinical-advisor", "huible-tech-lead"),
            ],
            contacts=_contacts(),
            canary_start=canary_start,
        )
        primary, secondary, ceiling = roster.resolve()
        assert primary is not None and primary.seat_id == "clinical-advisor"
        assert secondary is not None and secondary.seat_id == "ceo"
        assert ceiling is not None and ceiling.seat_id == "ceo"

    def test_w3_window_resolves_tech_lead_primary(self):
        """T+24h..36h → W3: primary=huible-tech-lead, secondary=huible-pm."""
        canary_start = datetime.now(UTC) - timedelta(hours=30)  # inside W3
        roster = OnCallRoster(
            windows=[
                ("clinical-advisor", "ceo"),
                ("huible-pm", "clinical-advisor"),
                ("huible-tech-lead", "huible-pm"),
                ("clinical-advisor", "huible-tech-lead"),
            ],
            contacts=_contacts(),
            canary_start=canary_start,
        )
        primary, secondary, _ceiling = roster.resolve()
        assert primary is not None and primary.seat_id == "huible-tech-lead"
        assert secondary is not None and secondary.seat_id == "huible-pm"

    def test_non_escalated_targets_primary_and_secondary(self):
        """Crisis-enqueue page reaches primary + secondary (both founders see it)."""
        canary_start = datetime.now(UTC) - timedelta(hours=1)
        roster = OnCallRoster(
            windows=[("clinical-advisor", "ceo")],
            contacts=_contacts(),
            canary_start=canary_start,
        )
        targets = roster.targets(escalated=False)
        seat_ids = [t.seat_id for t in targets]
        assert seat_ids == ["clinical-advisor", "ceo"]

    def test_escalated_targets_include_ceiling(self):
        """Ack-SLA miss → primary + secondary + CEO (ceiling joins)."""
        canary_start = datetime.now(UTC) - timedelta(hours=1)
        roster = OnCallRoster(
            windows=[("huible-pm", "huible-tech-lead")],
            contacts=_contacts(),
            canary_start=canary_start,
        )
        targets = roster.targets(escalated=True)
        seat_ids = [t.seat_id for t in targets]
        # primary=huible-pm, secondary=huible-tech-lead, ceiling=ceo
        assert seat_ids == ["huible-pm", "huible-tech-lead", "ceo"]

    def test_targets_de_duplicated(self):
        """When the same seat is primary AND ceiling, it pages once."""
        canary_start = datetime.now(UTC) - timedelta(hours=1)
        roster = OnCallRoster(
            windows=[("ceo", "clinical-advisor")],  # CEO is primary
            contacts=_contacts(),
            canary_start=canary_start,
        )
        targets = roster.targets(escalated=True)
        seat_ids = [t.seat_id for t in targets]
        assert seat_ids.count("ceo") == 1

    def test_past_canary_horizon_pages_ceiling_only(self):
        """Past the 48h canary → primary/secondary lapse; ceiling still paged."""
        canary_start = datetime.now(UTC) - timedelta(hours=100)  # past 4x12h
        roster = OnCallRoster(
            windows=[
                ("clinical-advisor", "ceo"),
                ("huible-pm", "clinical-advisor"),
                ("huible-tech-lead", "huible-pm"),
                ("clinical-advisor", "huible-tech-lead"),
            ],
            contacts=_contacts(),
            canary_start=canary_start,
        )
        primary, secondary, ceiling = roster.resolve()
        assert primary is None
        assert secondary is None
        assert ceiling is not None and ceiling.seat_id == "ceo"

    def test_w3_crisis_targets_always_include_clinical_advisor(self):
        """Ceiling-tier page in W3 reaches Clinical Advisor (§3.4 commitment).

        W3 = Tech Lead primary / PM secondary — neither clinically trained.
        ``clinical_always=True`` appends the Clinical Advisor seat so the §3.4
        "notified of every ceiling escalation" commitment holds in every window.
        """
        canary_start = datetime.now(UTC) - timedelta(hours=30)  # inside W3
        roster = OnCallRoster(
            windows=[
                ("clinical-advisor", "ceo"),
                ("huible-pm", "clinical-advisor"),
                ("huible-tech-lead", "huible-pm"),
                ("clinical-advisor", "huible-tech-lead"),
            ],
            contacts=_contacts(),
            canary_start=canary_start,
        )
        # Sanity: W3 rotated seats are Tech Lead + PM.
        rotated = roster.targets(escalated=False)
        assert [t.seat_id for t in rotated] == ["huible-tech-lead", "huible-pm"]
        # Ceiling-tier page adds Clinical Advisor unconditionally.
        crisis = roster.targets(escalated=False, clinical_always=True)
        assert "clinical-advisor" in {t.seat_id for t in crisis}
        assert "huible-tech-lead" in {t.seat_id for t in crisis}

    def test_clinical_always_not_duplicated_when_already_seated(self):
        """When Clinical Advisor is already primary/secondary, page once."""
        canary_start = datetime.now(UTC) - timedelta(hours=1)  # W1: CA primary
        roster = OnCallRoster(
            windows=[("clinical-advisor", "ceo")],
            contacts=_contacts(),
            canary_start=canary_start,
        )
        targets = roster.targets(escalated=False, clinical_always=True)
        assert sum(1 for t in targets if t.seat_id == "clinical-advisor") == 1


# --- build_roster ----------------------------------------------------------


class TestBuildRoster:
    def test_empty_config_returns_unconfigured_roster(self):
        roster = build_roster(contacts_json="", canary_start_ts="")
        assert roster.resolve() == (None, None, None)

    def test_contacts_json_parsed_into_contact_map(self):
        import json

        contacts = json.dumps(
            {
                "clinical-advisor": {"phone": "+15551112222", "email": "ca@x.test"},
                "ceo": {"phone": "+15553334444"},
            }
        )
        roster = build_roster(
            contacts_json=contacts,
            canary_start_ts=datetime.now(UTC).isoformat(),
        )
        primary, _secondary, _ceiling = roster.resolve()
        assert primary is not None
        assert primary.seat_id == "clinical-advisor"
        assert primary.phone == "+15551112222"
        assert primary.email == "ca@x.test"

    def test_malformed_contacts_json_falls_back_silently(self):
        """A bad JSON never raises; paging falls back to the log line."""
        roster = build_roster(contacts_json="{not json", canary_start_ts="")
        assert roster.contacts == {}

    def test_malformed_canary_ts_falls_back_silently(self):
        roster = build_roster(contacts_json="", canary_start_ts="not-a-timestamp")
        assert roster.canary_start is None

    def test_naive_timestamp_treated_as_utc(self):
        roster = build_roster(
            contacts_json="",
            canary_start_ts="2026-08-12T09:00:00",
        )
        assert roster.canary_start is not None
        assert roster.canary_start.tzinfo is not None  # aware


# --- TelnyxSmsPager --------------------------------------------------------


class TestTelnyxSmsPager:
    def test_no_credentials_falls_back_to_log(self, caplog):
        """Empty key/from → LoggingPager fallback (key-free default)."""
        pager = TelnyxSmsPager(api_key="", from_number="")
        with caplog.at_level(logging.CRITICAL, logger="huible.api.paging"):
            failures = pager.page(_ticket(), severity="crisis", window="always")
        assert failures == 0
        assert any(r.message.startswith("handoff.page") for r in caplog.records)

    def test_no_contacts_falls_back_to_log(self, caplog):
        """Credentials present but no contacts resolved → log fallback."""
        pager = TelnyxSmsPager(api_key="k", from_number="+15559999999")
        with caplog.at_level(logging.CRITICAL, logger="huible.api.paging"):
            failures = pager.page(_ticket(), severity="crisis", window="always", contacts=[])
        assert failures == 0

    def test_sms_send_succeeds_returns_zero_failures(self, monkeypatch):
        posted: list[dict] = []

        def _fake_post(url, *, json, headers, timeout):
            posted.append({"url": url, "json": json})
            return httpx.Response(200, request=httpx.Request("POST", url))

        monkeypatch.setattr("huible.api.paging.httpx.post", _fake_post)
        pager = TelnyxSmsPager(api_key="key", from_number="+15559999999")
        contacts = [OnCallContact("ca", "+15551112222", "")]
        failures = pager.page(_ticket(), severity="crisis", window="always", contacts=contacts)
        assert failures == 0
        assert len(posted) == 1
        assert posted[0]["json"]["to"] == "+15551112222"
        assert "crisis" in posted[0]["json"]["text"]

    def test_sms_send_failure_returns_failure_count(self, monkeypatch, caplog):
        """A Telnyx error is counted; the page falls back to the log line."""
        def _boom(url, *, json, headers, timeout):
            raise httpx.ConnectError("network down")

        monkeypatch.setattr("huible.api.paging.httpx.post", _boom)
        pager = TelnyxSmsPager(api_key="key", from_number="+15559999999")
        contacts = [
            OnCallContact("ca", "+15551112222", ""),
            OnCallContact("ceo", "+15553334444", ""),
        ]
        with caplog.at_level(logging.CRITICAL, logger="huible.api.paging"):
            failures = pager.page(_ticket(), severity="crisis", window="always", contacts=contacts)
        assert failures == 2  # both SMS sends failed
        # The log fallback fired (no silent drop).
        assert any(r.message.startswith("handoff.page") for r in caplog.records)

    def test_sms_skips_contact_without_phone(self, monkeypatch):
        posted: list[dict] = []

        def _fake_post(url, *, json, headers, timeout):
            posted.append(json)
            return httpx.Response(200, request=httpx.Request("POST", url))

        monkeypatch.setattr("huible.api.paging.httpx.post", _fake_post)
        pager = TelnyxSmsPager(api_key="key", from_number="+15559999999")
        contacts = [
            OnCallContact("ca", "+15551112222", ""),
            OnCallContact("email-only", "", "x@y.test"),  # no phone → skipped
        ]
        failures = pager.page(_ticket(), severity="crisis", window="always", contacts=contacts)
        assert failures == 0
        assert len(posted) == 1  # only the phone contact paged


# --- EmailPager ------------------------------------------------------------


class TestEmailPager:
    def test_no_host_falls_back_to_log(self, caplog):
        pager = EmailPager(smtp_host="")
        with caplog.at_level(logging.CRITICAL, logger="huible.api.paging"):
            failures = pager.page(_ticket(), severity="sev-1", window="always")
        assert failures == 0
        assert any(r.message.startswith("handoff.page") for r in caplog.records)

    def test_email_send_records_failure_on_smtp_error(self, monkeypatch):
        """An SMTP error is counted; the page falls back to the log line."""

        class _BoomSmtp:
            def __init__(self, *a, **k):
                raise OSError("relay unreachable")

        monkeypatch.setattr("smtplib.SMTP", _BoomSmtp)
        pager = EmailPager(smtp_host="smtp.example.com", from_addr="oncall@x.test")
        contacts = [OnCallContact("ca", "", "ca@x.test")]
        failures = pager.page(
            _ticket(), severity="sev-1", window="always", contacts=contacts
        )
        assert failures == 1


# --- MultiChannelPager -----------------------------------------------------


class TestMultiChannelPager:
    def test_no_channels_falls_back_to_log(self, caplog):
        """Key-free default: nothing configured → LoggingPager carries the page."""
        pager = MultiChannelPager()
        with caplog.at_level(logging.CRITICAL, logger="huible.api.paging"):
            failures = pager.page(_ticket(), severity="crisis", window="always")
        assert failures == 0
        assert any(r.message.startswith("handoff.page") for r in caplog.records)

    def test_fanout_calls_every_configured_channel(self):
        recorded: list[str] = []

        class _FakeTelnyx:
            def page(self, ticket, *, severity, window, trigger="x", contacts=None, **kw):
                recorded.append("telnyx")
                return 0

        class _FakeEmail:
            def page(self, ticket, *, severity, window, trigger="x", contacts=None, **kw):
                recorded.append("email")
                return 0

        class _FakeWebhook:
            def page(self, ticket, *, severity, window, trigger="x", contacts=None, **kw):
                recorded.append("webhook")
                return 1  # one failure

        pager = MultiChannelPager(
            telnyx=_FakeTelnyx(), email=_FakeEmail(), webhook=_FakeWebhook()
        )
        failures = pager.page(_ticket(), severity="crisis", window="always")
        assert set(recorded) == {"telnyx", "email", "webhook"}
        assert failures == 1  # only the webhook failed

    def test_resolves_contacts_from_roster_when_omitted(self):
        """When contacts are not passed, the roster resolves them (HU-1451 §2)."""
        canary_start = datetime.now(UTC) - timedelta(hours=1)
        roster = OnCallRoster(
            windows=[("clinical-advisor", "ceo")],
            contacts=_contacts(),
            canary_start=canary_start,
        )
        seen_contacts: list = []

        class _FakeTelnyx:
            def page(self, ticket, *, severity, window, trigger="x", contacts=None, **kw):
                seen_contacts.extend(contacts or [])
                return 0

        pager = MultiChannelPager(roster=roster, telnyx=_FakeTelnyx())
        pager.page(_ticket(), severity="crisis", window="always")
        assert {c.seat_id for c in seen_contacts} == {"clinical-advisor", "ceo"}

    def test_escalated_flag_pulls_ceiling_into_targets(self):
        canary_start = datetime.now(UTC) - timedelta(hours=1)
        roster = OnCallRoster(
            windows=[("huible-pm", "huible-tech-lead")],
            contacts=_contacts(),
            canary_start=canary_start,
        )
        seen_contacts: list = []

        class _FakeTelnyx:
            def page(self, ticket, *, severity, window, trigger="x", contacts=None, **kw):
                seen_contacts.extend(contacts or [])
                return 0

        pager = MultiChannelPager(roster=roster, telnyx=_FakeTelnyx())
        pager.page(_ticket(), severity="sev-1", window="always", escalated=True)
        assert "ceo" in {c.seat_id for c in seen_contacts}

    def test_crisis_enqueue_in_w3_always_pages_clinical_advisor(self):
        """§3.4 binding fix (HU-1436): a crisis page reaches Clinical Advisor
        even in W3 where neither rotated seat is clinically trained."""
        canary_start = datetime.now(UTC) - timedelta(hours=30)  # W3
        roster = OnCallRoster(
            windows=[
                ("clinical-advisor", "ceo"),
                ("huible-pm", "clinical-advisor"),
                ("huible-tech-lead", "huible-pm"),
                ("clinical-advisor", "huible-tech-lead"),
            ],
            contacts=_contacts(),
            canary_start=canary_start,
        )
        seen_contacts: list = []

        class _FakeTelnyx:
            def page(self, ticket, *, severity, window, trigger="x", contacts=None, **kw):
                seen_contacts.extend(contacts or [])
                return 0

        pager = MultiChannelPager(roster=roster, telnyx=_FakeTelnyx())
        # Crisis-enqueue (ceiling-tier) — Clinical Advisor must be paged.
        pager.page(_ticket(), severity="crisis", window="always")
        seat_ids = {c.seat_id for c in seen_contacts}
        assert "clinical-advisor" in seat_ids
        assert "huible-tech-lead" in seat_ids  # W3 primary still paged

    def test_operational_sev1_in_w3_does_not_force_clinical_advisor(self):
        """Operational Sev-1 (e.g. consent-bypass) is NOT ceiling-tier — it
        routes through the window rotation without forcing Clinical Advisor.
        Only crisis + ack-SLA escalation are ceiling-tier."""
        canary_start = datetime.now(UTC) - timedelta(hours=30)  # W3
        roster = OnCallRoster(
            windows=[
                ("clinical-advisor", "ceo"),
                ("huible-pm", "clinical-advisor"),
                ("huible-tech-lead", "huible-pm"),
                ("clinical-advisor", "huible-tech-lead"),
            ],
            contacts=_contacts(),
            canary_start=canary_start,
        )
        seen_contacts: list = []

        class _FakeTelnyx:
            def page(self, ticket, *, severity, window, trigger="x", contacts=None, **kw):
                seen_contacts.extend(contacts or [])
                return 0

        pager = MultiChannelPager(roster=roster, telnyx=_FakeTelnyx())
        pager.page(
            _ticket(), severity="sev-1", window="always",
            trigger=PAGE_TRIGGER_CONSENT_BYPASS,
        )
        assert "clinical-advisor" not in {c.seat_id for c in seen_contacts}


# --- build_multichannel_pager ---------------------------------------------


class TestBuildMultichannelPager:
    def test_no_credentials_returns_logging_pager(self):
        """Key-free default: nothing configured → LoggingPager."""
        roster = build_roster(contacts_json="", canary_start_ts="")
        pager = build_multichannel_pager(
            provider="log", webhook_url="", roster=roster,
            telnyx_api_key="", telnyx_from="", telnyx_api_base_url="x",
            smtp_host="", smtp_port=587, smtp_user="", smtp_password="",
            email_from_addr="",
        )
        assert isinstance(pager, LoggingPager)

    def test_telnyx_credentials_build_multichannel(self):
        roster = build_roster(contacts_json="", canary_start_ts="")
        pager = build_multichannel_pager(
            provider="log", webhook_url="", roster=roster,
            telnyx_api_key="key", telnyx_from="+15559999999",
            telnyx_api_base_url="https://api.telnyx.com/v2",
            smtp_host="", smtp_port=587, smtp_user="", smtp_password="",
            email_from_addr="",
        )
        # HU-1428 drill suppression: real channels now default to a
        # DrillSuppressingPager wrapping the MultiChannelPager. Suppression
        # disabled (drill_markers="") returns the raw MultiChannelPager.
        assert isinstance(pager, DrillSuppressingPager)
        assert isinstance(pager._inner, MultiChannelPager)
        unwrapped = build_multichannel_pager(
            provider="log", webhook_url="", roster=roster,
            telnyx_api_key="key", telnyx_from="+15559999999",
            telnyx_api_base_url="https://api.telnyx.com/v2",
            smtp_host="", smtp_port=587, smtp_user="", smtp_password="",
            email_from_addr="", drill_markers="",
        )
        assert isinstance(unwrapped, MultiChannelPager)


# --- Trigger helpers -------------------------------------------------------


class TestTriggerHelpers:
    def test_page_degraded_net_uses_sev1_and_correct_trigger(self):
        recorded: list = []

        class _Rec:
            def page(self, ticket, *, severity, window, trigger="x", **kw):
                recorded.append((ticket.id, severity, trigger))
                return 0

        ticket = _ticket(outcome=HandoffOutcome.DEGRADED)
        failures = page_degraded_net(_Rec(), ticket=ticket, window="always")
        assert failures == 0
        assert recorded == [(ticket.id, PAGE_SEVERITY_SEV1, PAGE_TRIGGER_DEGRADED_NET)]

    def test_page_sev1_signal_without_ticket_mints_synthetic_id(self):
        """Triggers #2/#4 have no handoff ticket → mint an audit-safe synthetic."""
        recorded: list = []

        class _Rec:
            def page(self, ticket, *, severity, window, trigger="x", **kw):
                recorded.append((ticket.id, severity, trigger, ticket.persona_id))
                return 0

        page_sev1_signal(
            _Rec(), ticket=None, trigger=PAGE_TRIGGER_UNGROUNDED_LEAK,
            window="always", persona_id="persona-7",
        )
        assert len(recorded) == 1
        sev_id, sev, trig, pid = recorded[0]
        assert sev_id.startswith("sev1-ungrounded_claim_leak-")
        assert sev == PAGE_SEVERITY_SEV1
        assert trig == PAGE_TRIGGER_UNGROUNDED_LEAK
        assert pid == "persona-7"

    def test_page_sev1_signal_with_ticket_uses_real_ticket(self):
        recorded: list = []

        class _Rec:
            def page(self, ticket, *, severity, window, trigger="x", **kw):
                recorded.append(ticket.id)
                return 0

        ticket = _ticket()
        page_sev1_signal(
            _Rec(), ticket=ticket, trigger=PAGE_TRIGGER_CONSENT_BYPASS, window="always"
        )
        assert recorded == [ticket.id]


# --- Roster-aware escalation (ack-SLA miss → secondary/CEO) ---------------


class TestRosterAwareEscalation:
    def test_multichannel_escalates_on_breach(self):
        """An SLA breach on a MultiChannelPager passes escalated=True."""
        canary_start = datetime.now(UTC) - timedelta(hours=1)
        roster = OnCallRoster(
            windows=[("huible-pm", "huible-tech-lead")],
            contacts=_contacts(),
            canary_start=canary_start,
        )
        seen: dict = {}

        class _FakeTelnyx:
            def page(self, ticket, *, severity, window, trigger="x", contacts=None,
                     escalated=False, **kw):
                seen["seats"] = [c.seat_id for c in contacts or []]
                return 0

        pager = MultiChannelPager(roster=roster, telnyx=_FakeTelnyx())
        queue = InMemoryHandoffQueue(available_responders=1)
        breached = _ticket(seconds_old=400, sla_target_seconds=300)
        queue.enqueue(breached)
        count = escalate_sla_breaches(queue, pager, window="always")
        assert count == 1
        # The MultiChannelPager consumed escalated=True internally and resolved
        # primary + secondary + ceiling(ceo) into the channel contacts.
        assert "ceo" in seen["seats"]
        assert "huible-pm" in seen["seats"]


# --- Coverage-pressure escalation (HU-1428 AC #2 / Condition 3) ------------


class TestCoveragePressureEscalation:
    def _rec(self):
        recorded: list = []

        class _Rec:
            def page(self, ticket, *, severity, window, trigger="x", **kw):
                recorded.append((ticket.id, severity, trigger))
                return 0

        return _Rec(), recorded

    def _off_shift_degrades(self, n: int) -> list[HandoffTicket]:
        return [
            _ticket(
                ticket_id=f"hh-oc-{i}",
                outcome=HandoffOutcome.DEGRADED,
                degrade_reason="outside_coverage_hours",
            )
            for i in range(n)
        ]

    def test_pages_sev1_at_threshold(self):
        pager, recorded = self._rec()
        tickets = self._off_shift_degrades(DEFAULT_COVERAGE_PRESSURE_THRESHOLD)
        pressure = escalate_coverage_pressure(pager, tickets, window="always")
        assert pressure == DEFAULT_COVERAGE_PRESSURE_THRESHOLD
        assert len(recorded) == 1
        _, sev, trig = recorded[0]
        assert sev == PAGE_SEVERITY_SEV1
        assert trig == PAGE_TRIGGER_COVERAGE_PRESSURE

    def test_pages_above_threshold(self):
        pager, recorded = self._rec()
        tickets = self._off_shift_degrades(DEFAULT_COVERAGE_PRESSURE_THRESHOLD + 2)
        pressure = escalate_coverage_pressure(pager, tickets, window="always")
        assert pressure == DEFAULT_COVERAGE_PRESSURE_THRESHOLD + 2
        # Exactly one aggregate page (a trend signal, not one per ticket).
        assert len(recorded) == 1

    def test_no_page_below_threshold(self):
        pager, recorded = self._rec()
        tickets = self._off_shift_degrades(DEFAULT_COVERAGE_PRESSURE_THRESHOLD - 1)
        pressure = escalate_coverage_pressure(pager, tickets, window="always")
        assert pressure == DEFAULT_COVERAGE_PRESSURE_THRESHOLD - 1
        assert recorded == []

    def test_no_responder_degrades_do_not_count(self):
        pager, recorded = self._rec()
        tickets = [
            _ticket(
                ticket_id=f"hh-nr-{i}",
                outcome=HandoffOutcome.DEGRADED,
                degrade_reason="no_responder_available",
            )
            for i in range(DEFAULT_COVERAGE_PRESSURE_THRESHOLD + 5)
        ]
        pressure = escalate_coverage_pressure(pager, tickets, window="always")
        assert pressure == 0
        assert recorded == []

    def test_threshold_zero_disables_gate(self):
        pager, recorded = self._rec()
        tickets = self._off_shift_degrades(50)
        pressure = escalate_coverage_pressure(pager, tickets, window="always", threshold=0)
        assert pressure == 50
        assert recorded == []

    def test_custom_threshold_pages(self):
        pager, recorded = self._rec()
        tickets = self._off_shift_degrades(2)
        pressure = escalate_coverage_pressure(pager, tickets, window="always", threshold=2)
        assert pressure == 2
        assert len(recorded) == 1


# --- Failure counter -------------------------------------------------------


class TestPagingFailuresCounter:
    def test_counter_increments_by_trigger(self):
        """record_paging_failures drives huible_paging_failures_total{trigger}."""
        from huible.api.metrics import PAGING_FAILURES

        record_paging_failures(PAGE_TRIGGER_DEGRADED_NET, 2)
        record_paging_failures(PAGE_TRIGGER_UNGROUNDED_LEAK, 1)
        # Read the per-label child counter values (module-level singleton).
        labels = PAGING_FAILURES._metrics  # type: ignore[attr-defined]
        assert labels[("degraded_net",)]._value.get() >= 2
        assert labels[("ungrounded_claim_leak",)]._value.get() >= 1

    def test_zero_or_negative_count_is_noop(self):
        # No exception, no increment for <= 0.
        record_paging_failures(PAGE_TRIGGER_SLA_BREACH, 0)
        record_paging_failures(PAGE_TRIGGER_SLA_BREACH, -5)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
