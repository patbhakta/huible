"""On-call paging notifier for §7.4.1 crisis escalations (Stage 0.4, HU-1450)
and the §3 Sev-1 paging channel build (Stage 0.4a, HU-1451).

[HU-1446] (Stage 0.3) shipped the metrics + per-turn logging substrate and the
``huible_alert_oncall_configured`` gauge as the **wiring target**, and explicitly
deferred the alert→on-call paging link to 0.4: *"the ack-SLA→paging link lands
when [HU-1447] names the roster + paging target."* The roster is now named
([HU-1447] on-call-roster doc §1) — 4x12h rotated windows across PM / Tech Lead
/ Clinical Advisor / CEO. This module is the paging transport that makes the
roster *live*: the missing link between an enqueued crisis ticket and a real
person being paged.

Two severity tiers (Clinical Advisor §3 Sev-1 conditions, recorded on HU-1446):

* :data:`PAGE_SEVERITY_CRISIS` — the **primary trigger**: an immediate page on
  every freshly-enqueued crisis ticket (both G1 ``escalate_to_human`` and
  risk-driven ``escalate_risk_to_human`` when ``outcome == ENQUEUED``). This is
  distinct from the existing ``handoff.enqueue`` INFO audit line at
  :mod:`huible.safety.handoff`; it is the operator-scrapeable CRITICAL page.
* :data:`PAGE_SEVERITY_SEV1` — the **ack-SLA escalation**: an ``ENQUEUED``
  ticket whose :func:`huible.safety.handoff_monitoring.sla_status` reports
  ``breached`` (the per-ticket ``HANDOFF_SLA_TARGET_SECONDS`` has elapsed
  without an acknowledgement) is re-paged at Sev-1 by
  :func:`escalate_sla_breaches`.

Stage 0.4a (HU-1451) extends this to the full §3 Sev-1 paging channel:

* **Three additional Sev-1 triggers** beyond the crisis enqueue (data labels in
  :data:`PAGE_TRIGGER_*`): an un-grounded persona claim released to a user
  (§3 Sev-1 (A), detected by the §7.4.2 alignment guard), a degraded /
  missing-audit escalation (§3 Sev-1 (B) — the safety net failed; the queue
  degraded with no responder), and a consent-bypass (§3 Sev-1 (C) — a
  persona-voiced turn was served without a recorded G6 consent). Each pages
  immediately at the detection point, fire-and-forget, never throttled behind
  the >10%/1h aggregate.
* **A real multi-channel push sender** (:class:`TelnyxSmsPager` +
  :class:`EmailPager` behind a :class:`MultiChannelPager`) so a page reaches a
  real human device — not merely a log line. The roster's primary + secondary
  (and on miss, CEO) are resolved from the canary-start clock by
  :class:`OnCallRoster` and routed to. Telnyx is the SMS transport (the
  codebase already speaks the Telnyx SMS webhook on the inbound side); email is
  the second channel. Both fall back to :class:`LoggingPager` when their
  credentials are absent, preserving the key-free default.
* **Ack-SLA miss → secondary/CEO escalation**: :func:`escalate_sla_breaches`
  re-pages the primary **and** escalates to the secondary + CEO seat on a
  15-min ack miss (the canary commitment), not merely re-pages the same window.
* **Failure counter**: every real-channel page-send failure is reported back to
  the caller (``page()`` returns the failure count) so the chat path can
  increment ``huible_paging_failures_total{trigger}`` — paging never blocks or
  alters a clinical turn, but a page that did not land is itself a Sev-1
  counter.

Key-free default (:class:`LoggingPager`): a structured ``handoff.page``
CRITICAL log line — the minimal honest page an operator can scrape / alert on
without external credentials. This mirrors the ``llm_provider`` /
``generator_provider`` key-free-default convention in :mod:`huible.api.settings`
and keeps the pre-real-user suite running without secrets. A configurable
:class:`WebhookPager` POSTs a JSON payload to ``HANDOFF_PAGER_WEBHOOK_URL``
(Slack incoming webhook / PagerDuty Events API v2 style) and falls back to
:class:`LoggingPager` when the URL is empty — so the key-free default is
preserved and credentials land at deploy time.

The fail-safe in :mod:`huible.safety.handoff` (``available_responders=0`` →
degrade) stays authoritative — the crisis-enqueue page is **additive** on top
of ``ENQUEUED``, never a bypass around the degrade gate. (A ``DEGRADED``
ticket is not crisis-enqueue-paged; it is instead Sev-1-paged via the
:func:`page_degraded_net` trigger — the net failed, which is a different Sev-1
than "a responder was paged".)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import httpx

if TYPE_CHECKING:
    from datetime import datetime

    from huible.safety.handoff import HandoffQueue, HandoffTicket

logger = logging.getLogger(__name__)

__all__ = [
    "PAGE_SEVERITY_CRISIS",
    "PAGE_SEVERITY_SEV1",
    "PAGE_TRIGGER_CONSENT_BYPASS",
    "PAGE_TRIGGER_CRISIS_ENQUEUE",
    "PAGE_TRIGGER_DEGRADED_NET",
    "PAGE_TRIGGER_SLA_BREACH",
    "PAGE_TRIGGER_UNGROUNDED_LEAK",
    "EmailPager",
    "LoggingPager",
    "MultiChannelPager",
    "OnCallContact",
    "OnCallRoster",
    "Pager",
    "TelnyxSmsPager",
    "WebhookPager",
    "build_pager",
    "build_roster",
    "escalate_sla_breaches",
    "page_degraded_net",
    "page_sev1_signal",
]

#: Immediate page on a freshly-enqueued crisis ticket (primary trigger). Fires
#: once per ``ENQUEUED`` escalation, never throttled behind the >10%/1h rate
#: backstop — a grieving user waiting on a crisis turn cannot be rate-limited.
PAGE_SEVERITY_CRISIS: str = "crisis"

#: Sev-1 re-page on an SLA-breach: an ``ENQUEUED`` ticket past its
#: ``HANDOFF_SLA_TARGET_SECONDS`` without an acknowledgement. This is the
#: ack-SLA escalation surface; the existing >10%/1h aggregate stays the rate
#: backstop (unchanged).
PAGE_SEVERITY_SEV1: str = "sev-1"

# --- §3 Sev-1 trigger labels (HU-1451) -------------------------------------
#
# Every page carries a ``trigger`` label so the page message, the routing
# decision, and the ``huible_paging_failures_total{trigger}`` counter can
# distinguish *why* the page fired. The four conditions are the §3 Sev-1 set
# the Clinical Advisor requires to page immediately (not scrape-throttled).

#: Crisis-enqueue trigger (HU-1450 primary): an ``ENQUEUED`` G1 / risk-driven
#: handoff ticket. The one non-Sev-1 severity (``crisis``).
PAGE_TRIGGER_CRISIS_ENQUEUE: str = "crisis_enqueue"

#: Ack-SLA-breach trigger: an ``ENQUEUED`` ticket past its 15-min canary ack
#: target without a responder acknowledgement. Re-pages primary **and**
#: escalates to secondary + CEO.
PAGE_TRIGGER_SLA_BREACH: str = "sla_breach"

#: §3 Sev-1 (A): an un-grounded persona claim was released to a user (the
#: §7.4.2 alignment guard fired ``suppressed`` — the generator confabulated;
#: a degraded generator could leak next time). Pages on the detection.
PAGE_TRIGGER_UNGROUNDED_LEAK: str = "ungrounded_claim_leak"

#: §3 Sev-1 (B): the safety net failed — a handoff ticket was ``DEGRADED``
#: (no responder / enqueue failed) or its audit row is missing/unaligned.
#: Distinct from crisis-enqueue: no responder was paged by the queue, so this
#: is the "the net failed, escalate the ceiling" page, not "a responder is on
#: it." Kill-switch-eligible.
PAGE_TRIGGER_DEGRADED_NET: str = "degraded_net"

#: §3 Sev-1 (C): a persona-voiced turn was served without a recorded G6
#: consent (the consent gate was bypassed/skipped for a real-user turn).
#: Detected post-hoc on the persona path.
PAGE_TRIGGER_CONSENT_BYPASS: str = "consent_bypass"

#: HTTP timeout for outbound page POSTs (Telnyx / email / webhook). Kept short
#: so a slow transport never stalls a clinical turn — the chat path pages
#: synchronously on the detection point.
_WEBHOOK_TIMEOUT_S: float = 10.0

#: The CEO seat id — the escalation ceiling for ack-SLA misses. Resolved from
#: the roster contact map; the CEO is the final human point for emergency
#: services contact per the [HU-1447] on-call-roster §1.
_CEILING_SEAT: str = "ceo"


@dataclass(slots=True, frozen=True)
class OnCallContact:
    """A single human on-call target (one seat in one window).

    Carries the push channels the multi-channel pager sends to. ``seat_id`` is
    the roster label (e.g. ``"clinical-advisor"`` / ``"ceo"``); ``phone`` is the
    Telnyx SMS destination (E.164); ``email`` is the seat contact for the email
    channel. Either may be empty — the :class:`MultiChannelPager` only fires
    the channels that have both a destination and a credential.
    """

    seat_id: str
    phone: str = ""
    email: str = ""


@dataclass(slots=True)
class OnCallRoster:
    """Stage 1 canary on-call roster: the 4x12h rotation + contact map.

    Built by :func:`build_roster` from the ``HANDOFF_ONCALL_CONTACTS`` JSON map
    + the ``HANDOFF_CANARY_START_TS`` canary T+0 clock. The roster from
    [HU-1447] on-call-roster §1 is four 12h windows (W1-W4) with a primary
    (ack-SA bearer) and a secondary (ceiling-escalation owner) per window; the
    CEO seat is the terminal escalation point. ``resolve(now)`` returns the
    ``(primary, secondary, ceiling)`` contacts active at ``now``.

    The rotation is anchored to ``canary_start`` (the first real grieving-user
    turn); each window is 12h. When ``canary_start`` is unset the roster is
    considered unconfigured and ``resolve`` returns empty contacts — the pager
    then degrades to :class:`LoggingPager` (the key-free default). This keeps
    paging honest: an unconfigured deploy never silently pages the wrong human.
    """

    windows: list[tuple[str, str]] = field(default_factory=list)
    contacts: dict[str, OnCallContact] = field(default_factory=dict)
    canary_start: datetime | None = None
    window_seconds: int = 12 * 60 * 60

    def resolve(
        self, *, now: datetime | None = None
    ) -> tuple[OnCallContact | None, OnCallContact | None, OnCallContact | None]:
        """Return ``(primary, secondary, ceiling)`` contacts active at ``now``.

        ``now`` defaults to current UTC. When the roster is unconfigured (no
        windows, no canary-start, or ``now`` outside the 48h canary horizon)
        every slot is ``None`` — callers fall back to the key-free
        :class:`LoggingPager` rather than paging a possibly-stale seat.
        """
        from datetime import UTC
        from datetime import datetime as _dt

        if not self.windows or self.canary_start is None:
            return None, None, None
        now = now or _dt.now(UTC)
        elapsed = (now - self.canary_start).total_seconds()
        if elapsed < 0:
            # Before the canary clock — no live roster yet.
            return None, None, None
        idx = int(elapsed // self.window_seconds)
        if idx < 0 or idx >= len(self.windows):
            # Past the 48h canary horizon — the formal roster ended. Page the
            # ceiling (CEO) only; the primary/secondary rotation has lapsed.
            return None, None, self.contacts.get(_CEILING_SEAT)
        primary_seat, secondary_seat = self.windows[idx]
        return (
            self.contacts.get(primary_seat),
            self.contacts.get(secondary_seat),
            self.contacts.get(_CEILING_SEAT),
        )

    def targets(
        self,
        *,
        escalated: bool,
        now: datetime | None = None,
    ) -> list[OnCallContact]:
        """Return the contact list to page.

        Non-escalated (the crisis-enqueue primary page): primary + secondary —
        both founders on the active window see the page so a missed device does
        not drop a crisis turn. Escalated (an ack-SLA miss): primary + secondary
        + CEO — the ceiling joins per [HU-1447] §3. De-duplicated, order
        preserved. Empty when the roster is unconfigured.
        """
        primary, secondary, ceiling = self.resolve(now=now)
        seats = [primary, secondary]
        if escalated:
            seats.append(ceiling)
        seen: set[str] = set()
        targets: list[OnCallContact] = []
        for seat in seats:
            if seat is None or seat.seat_id in seen:
                continue
            if not seat.phone and not seat.email:
                continue
            seen.add(seat.seat_id)
            targets.append(seat)
        return targets


@runtime_checkable
class Pager(Protocol):
    """Pluggable on-call paging transport.

    The default :class:`LoggingPager` is key-free (a CRITICAL log line). A real
    transport (Slack incoming webhook / PagerDuty Events API v2 / Telnyx SMS /
    email) drops in via :class:`WebhookPager` / :class:`MultiChannelPager` and
    the corresponding env vars at deploy time without touching callers.
    ``page`` is best-effort: a paging failure must never break a clinical turn
    (the chat path wraps the call defensively), so implementations swallow
    transport errors and fall back to the log line.

    ``page`` returns the count of **real-channel send failures** (0 when every
    attempted real channel succeeded, or when the key-free LoggingPager ran).
    The chat path adds this to ``huible_paging_failures_total{trigger}`` — a
    page that did not land is itself a Sev-1 counter, never a broken turn.
    """

    def page(
        self,
        ticket: HandoffTicket,
        *,
        severity: str,
        window: str,
        trigger: str = PAGE_TRIGGER_CRISIS_ENQUEUE,
        contacts: list[OnCallContact] | None = None,
    ) -> int:
        """Page the on-call for ``ticket`` at ``severity`` within ``window``.

        ``window`` is a human-readable label for the active coverage window
        (e.g. ``"always"``) so the operator knows which seat is paged.
        ``trigger`` (data label in :data:`PAGE_TRIGGER_*`) records *why* the
        page fired for the failure counter + the page message. ``contacts`` is
        the resolved roster targets for the real channels; when empty or
        absent the pager falls back to the key-free log line. No PHI is emitted
        — only the ticket id, trigger signal, severity, window, and seat ids.

        Returns the number of real-channel send failures (0 on success or when
        only the log fallback ran).
        """
        ...


class LoggingPager:
    """Key-free default pager: a structured ``handoff.page`` CRITICAL log line.

    This is the minimal honest page an operator can scrape and alert on without
    external credentials. It is deliberately distinct from the existing
    ``handoff.enqueue`` INFO audit line (which records the queue outcome for the
    clinical audit log) — ``handoff.page`` is the operator alert channel. The
    log carries only aggregate-safe fields (ticket id, trigger signal,
    severity, window, persona id, responder id, trigger label, seat targets);
    never message text or session ids. An operator wires a log scrape rule
    (e.g. match ``level=CRITICAL`` on a ``handoff.page`` message prefix) to
    turn this into a real alert.
    """

    def page(
        self,
        ticket: HandoffTicket,
        *,
        severity: str,
        window: str,
        trigger: str = PAGE_TRIGGER_CRISIS_ENQUEUE,
        contacts: list[OnCallContact] | None = None,
    ) -> int:
        seats = ",".join(c.seat_id for c in contacts) if contacts else "-"
        logger.critical(
            "handoff.page ticket=%s severity=%s signal=%s window=%s persona=%s "
            "responder=%s trigger=%s seats=%s",
            ticket.id,
            severity,
            ticket.trigger_signal,
            window,
            ticket.persona_id,
            ticket.responder_id,
            trigger,
            seats,
        )
        return 0


class WebhookPager:
    """POST a JSON payload to a Slack/PagerDuty-style webhook URL.

    Falls back to :class:`LoggingPager` when the URL is empty (the key-free
    default), and on any transport error (network, non-2xx, timeout) — so a
    misconfigured or unreachable pager degrades to the honest log line rather
    than dropping the page silently or breaking the clinical turn. Mirrors the
    ``llm_provider`` / ``generator_provider`` key-free-default convention.

    The payload is shaped for a generic incoming webhook (Slack-style
    ``text`` + PagerDuty Events API v2-style ``severity`` / ``routing_key``
    passthrough). Provider-specific adapters are a deploy-time concern; this is
    the hook.
    """

    def __init__(self, webhook_url: str, *, fallback: Pager | None = None) -> None:
        self._url = webhook_url
        self._fallback = fallback or LoggingPager()

    def page(
        self,
        ticket: HandoffTicket,
        *,
        severity: str,
        window: str,
        trigger: str = PAGE_TRIGGER_CRISIS_ENQUEUE,
        contacts: list[OnCallContact] | None = None,
    ) -> int:
        if not self._url:
            return self._fallback.page(
                ticket, severity=severity, window=window, trigger=trigger,
                contacts=contacts,
            )
        payload = {
            "ticket_id": ticket.id,
            "severity": severity,
            "trigger": trigger,
            "trigger_signal": ticket.trigger_signal,
            "window": window,
            "persona_id": ticket.persona_id,
            "responder_id": ticket.responder_id,
            "sla_target_seconds": ticket.sla_target_seconds,
            "seats": [c.seat_id for c in contacts] if contacts else [],
            "text": (
                f"[Huible handoff] {severity} page ({trigger}) for ticket "
                f"{ticket.id} ({ticket.trigger_signal}); "
                f"on-call={ticket.responder_id} window={window}"
            ),
        }
        try:
            resp = httpx.post(self._url, json=payload, timeout=_WEBHOOK_TIMEOUT_S)
            resp.raise_for_status()
            return 0
        except Exception:
            logger.exception(
                "handoff.page webhook failed; falling back to log pager (ticket=%s)",
                ticket.id,
            )
            return 1 + self._fallback.page(
                ticket, severity=severity, window=window, trigger=trigger,
                contacts=contacts,
            )


def _page_text(ticket: HandoffTicket, *, severity: str, trigger: str, window: str) -> str:
    """Build the aggregate-safe page body (no PHI) for SMS / email channels."""
    return (
        f"[Huible {severity}] trigger={trigger} ticket={ticket.id} "
        f"signal={ticket.trigger_signal} window={window}"
    )


class TelnyxSmsPager:
    """Send an SMS page via the Telnyx Messaging API (HU-1451).

    Telnyx is the codebase's SMS transport — the inbound side already speaks
    the Telnyx SMS webhook (``flows/converse.yaml``). This is the outbound
    complement: a page over Telnyx to each resolved roster phone (E.164).
    Requires ``telnyx_api_key`` + a ``telnyx_from`` sender (E.164); falls back
    to :class:`LoggingPager` when either is absent (key-free default) or on any
    transport error — a failed SMS never breaks a clinical turn and is counted
    as a real-channel failure for the ``huible_paging_failures_total`` counter.
    """

    def __init__(
        self,
        *,
        api_key: str,
        from_number: str,
        api_base_url: str = "https://api.telnyx.com/v2",
        fallback: Pager | None = None,
    ) -> None:
        self._api_key = api_key
        self._from = from_number
        self._api_base_url = api_base_url.rstrip("/")
        self._fallback = fallback or LoggingPager()

    def page(
        self,
        ticket: HandoffTicket,
        *,
        severity: str,
        window: str,
        trigger: str = PAGE_TRIGGER_CRISIS_ENQUEUE,
        contacts: list[OnCallContact] | None = None,
    ) -> int:
        if not self._api_key or not self._from or not contacts:
            return self._fallback.page(
                ticket, severity=severity, window=window, trigger=trigger,
                contacts=contacts,
            )
        body = _page_text(ticket, severity=severity, trigger=trigger, window=window)
        headers = {"Authorization": f"Bearer {self._api_key}"}
        failures = 0
        any_sent = False
        for contact in contacts:
            if not contact.phone:
                continue
            try:
                resp = httpx.post(
                    f"{self._api_base_url}/messages",
                    json={
                        "from": self._from,
                        "to": contact.phone,
                        "text": body,
                    },
                    headers=headers,
                    timeout=_WEBHOOK_TIMEOUT_S,
                )
                resp.raise_for_status()
                any_sent = True
            except Exception:
                failures += 1
                logger.exception(
                    "handoff.page telnyx SMS failed (ticket=%s seat=%s)",
                    ticket.id,
                    contact.seat_id,
                )
        if not any_sent:
            # No SMS landed at all — emit the honest log line so the page is
            # never silently dropped.
            self._fallback.page(
                ticket, severity=severity, window=window, trigger=trigger,
                contacts=contacts,
            )
        return failures


class EmailPager:
    """Send an email page via a simple SMTP relay (HU-1651 channel).

    The second push channel per [HU-1447] §1: email to each resolved roster
    seat contact. Uses ``smtplib`` (stdlib) so no new dependency lands; the
    relay host / port / user / password come from settings. Falls back to
    :class:`LoggingPager` when the relay host is absent (key-free default) or on
    any transport error. Send is synchronous-but-short (mirrors the
    fire-and-forget contract); a failed email is counted as a real-channel
    failure.
    """

    def __init__(
        self,
        *,
        smtp_host: str,
        smtp_port: int = 587,
        smtp_user: str = "",
        smtp_password: str = "",
        from_addr: str = "",
        fallback: Pager | None = None,
    ) -> None:
        self._host = smtp_host
        self._port = smtp_port
        self._user = smtp_user
        self._password = smtp_password
        self._from = from_addr or smtp_user
        self._fallback = fallback or LoggingPager()

    def page(
        self,
        ticket: HandoffTicket,
        *,
        severity: str,
        window: str,
        trigger: str = PAGE_TRIGGER_CRISIS_ENQUEUE,
        contacts: list[OnCallContact] | None = None,
    ) -> int:
        if not self._host or not contacts:
            return self._fallback.page(
                ticket, severity=severity, window=window, trigger=trigger,
                contacts=contacts,
            )
        import smtplib
        from email.message import EmailMessage

        subject = f"[Huible {severity}] {trigger} — ticket {ticket.id}"
        body = _page_text(ticket, severity=severity, trigger=trigger, window=window)
        failures = 0
        any_sent = False
        for contact in contacts:
            if not contact.email:
                continue
            msg = EmailMessage()
            msg["From"] = self._from
            msg["To"] = contact.email
            msg["Subject"] = subject
            msg.set_content(body)
            try:
                with smtplib.SMTP(self._host, self._port, timeout=_WEBHOOK_TIMEOUT_S) as smtp:
                    if self._user and self._password:
                        smtp.starttls()
                        smtp.login(self._user, self._password)
                    smtp.send_message(msg)
                any_sent = True
            except Exception:
                failures += 1
                logger.exception(
                    "handoff.page email failed (ticket=%s seat=%s)",
                    ticket.id,
                    contact.seat_id,
                )
        if not any_sent:
            self._fallback.page(
                ticket, severity=severity, window=window, trigger=trigger,
                contacts=contacts,
            )
        return failures


class MultiChannelPager:
    """Fan a page across every configured real channel (HU-1451 §2).

    Holds the Telnyx + email pagers and the :class:`OnCallRoster`. On each page
    it resolves the roster targets (primary+secondary, or +CEO when
    ``escalated``) and fans out to each configured channel. Channels that lack
    credentials internally fall back to :class:`LoggingPager`; the aggregate
    failure count is the sum across channels. When no channel is configured
    (the key-free default), the wrapped :class:`LoggingPager` carries the page.

    This is the in-app fire-and-forget sender the Clinical Advisor requires:
    immediate, never scrape-throttled, and never blocks a clinical turn.
    """

    def __init__(
        self,
        *,
        roster: OnCallRoster | None = None,
        telnyx: TelnyxSmsPager | None = None,
        email: EmailPager | None = None,
        webhook: WebhookPager | None = None,
        fallback: Pager | None = None,
    ) -> None:
        self._roster = roster
        self._telnyx = telnyx
        self._email = email
        self._webhook = webhook
        self._fallback = fallback or LoggingPager()

    def page(
        self,
        ticket: HandoffTicket,
        *,
        severity: str,
        window: str,
        trigger: str = PAGE_TRIGGER_CRISIS_ENQUEUE,
        contacts: list[OnCallContact] | None = None,
        escalated: bool = False,
    ) -> int:
        # Resolve targets from the roster when the caller did not pass an
        # explicit contact list (the chat path relies on this resolution).
        if contacts is None and self._roster is not None:
            contacts = self._roster.targets(escalated=escalated)

        if not self._telnyx and not self._email and not self._webhook:
            # Key-free default: no real channel configured → log only.
            return self._fallback.page(
                ticket, severity=severity, window=window, trigger=trigger,
                contacts=contacts,
            )

        failures = 0
        if self._webhook is not None:
            failures += self._webhook.page(
                ticket, severity=severity, window=window, trigger=trigger,
                contacts=contacts,
            )
        if self._telnyx is not None:
            failures += self._telnyx.page(
                ticket, severity=severity, window=window, trigger=trigger,
                contacts=contacts,
            )
        if self._email is not None:
            failures += self._email.page(
                ticket, severity=severity, window=window, trigger=trigger,
                contacts=contacts,
            )
        return failures


def build_pager(*, provider: str, webhook_url: str) -> Pager:
    """Construct the configured :class:`Pager` from settings (HU-1450 factory).

    ``provider="webhook"`` selects :class:`WebhookPager`; when the URL is empty
    it transparently falls back to :class:`LoggingPager` (key-free default).
    Any other value (including the default ``"log"``) selects
    :class:`LoggingPager`. This is the paging equivalent of
    :func:`huible.llm.client.build_llm_client`'s key-free-fallback rule.

    The HU-1451 :class:`MultiChannelPager` (Telnyx + email + roster) is wired
    separately in :func:`huible.api.app.create_app` via :func:`build_multichannel_pager`
    so the richer transport composes on top of this base factory.
    """
    if provider == "webhook":
        return WebhookPager(webhook_url)
    return LoggingPager()


def build_roster(
    *,
    contacts_json: str,
    canary_start_ts: str,
) -> OnCallRoster:
    """Build the :class:`OnCallRoster` from the env config (HU-1451 §4).

    ``contacts_json`` is the ``HANDOFF_ONCALL_CONTACTS`` JSON: a map of seat id
    → ``{"phone": ..., "email": ...}``. ``canary_start_ts`` is the ISO-8601
    canary T+0 clock (``HANDOFF_CANARY_START_TS``). The 4x12h window rotation
    (W1: clinical-advisor/ceo, W2: huible-pm/clinical-advisor, W3:
    huible-tech-lead/huible-pm, W4: clinical-advisor/huible-tech-lead) is the
    [HU-1447] on-call-roster §1 default; a future op can override the window
    list when the rotation changes.

    Returns an empty (unconfigured) roster when either input is absent — the
    pager then degrades to :class:`LoggingPager`. A malformed contacts JSON is
    logged and treated as empty (never raises; paging must stay best-effort).
    """
    from datetime import UTC
    from datetime import datetime as _dt

    roster = OnCallRoster()
    if contacts_json.strip():
        try:
            raw = json.loads(contacts_json)
        except json.JSONDecodeError:
            logger.exception(
                "HANDOFF_ONCALL_CONTACTS is not valid JSON; paging will fall "
                "back to the log line"
            )
            raw = {}
        if isinstance(raw, dict):
            for seat_id, entry in raw.items():
                if not isinstance(entry, dict):
                    continue
                roster.contacts[seat_id] = OnCallContact(
                    seat_id=seat_id,
                    phone=str(entry.get("phone", "")),
                    email=str(entry.get("email", "")),
                )
    if canary_start_ts.strip():
        try:
            dt = _dt.fromisoformat(canary_start_ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            roster.canary_start = dt
        except ValueError:
            logger.exception(
                "HANDOFF_CANARY_START_TS is not a valid ISO-8601 timestamp; "
                "paging will fall back to the log line"
            )
    # The [HU-1447] §1 4x12h rotation. Order matters: index 0 = W1 (T+0).
    roster.windows = [
        ("clinical-advisor", "ceo"),
        ("huible-pm", "clinical-advisor"),
        ("huible-tech-lead", "huible-pm"),
        ("clinical-advisor", "huible-tech-lead"),
    ]
    return roster


def build_multichannel_pager(
    *,
    provider: str,
    webhook_url: str,
    roster: OnCallRoster,
    telnyx_api_key: str,
    telnyx_from: str,
    telnyx_api_base_url: str,
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_password: str,
    email_from_addr: str,
) -> Pager:
    """Construct the HU-1651 multi-channel pager, composing real channels.

    When Telnyx / email / webhook credentials are absent, each channel simply
    omits itself and the :class:`MultiChannelPager` falls through to the
    :class:`LoggingPager` — the key-free default is preserved. When credentials
    are present, the page fans out across every configured channel to the
    roster-resolved primary + secondary (+ CEO on escalation).
    """
    telnyx = (
        TelnyxSmsPager(api_key=telnyx_api_key, from_number=telnyx_from,
                       api_base_url=telnyx_api_base_url)
        if (telnyx_api_key and telnyx_from)
        else None
    )
    email = (
        EmailPager(smtp_host=smtp_host, smtp_port=smtp_port, smtp_user=smtp_user,
                   smtp_password=smtp_password, from_addr=email_from_addr)
        if smtp_host
        else None
    )
    webhook = WebhookPager(webhook_url) if (provider == "webhook" and webhook_url) else None
    if telnyx is None and email is None and webhook is None:
        # No real channel configured → key-free log pager (preserve HU-1450
        # default + the provider="log" path).
        return LoggingPager()
    return MultiChannelPager(roster=roster, telnyx=telnyx, email=email, webhook=webhook)


# --- §3 Sev-1 paging helpers (HU-1651 triggers #2/#3/#4) --------------------


def page_sev1_signal(
    pager: Pager,
    *,
    ticket: HandoffTicket | None,
    trigger: str,
    window: str,
    persona_id: str | None = None,
    responder_id: str | None = None,
) -> int:
    """Page the on-call for a §3 Sev-1 condition without a handoff ticket.

    Triggers #2 (un-grounded claim leak) and #4 (consent bypass) are detected
    on the persona-voiced path where there is no handoff ticket to page on.
    This mints a synthetic, audit-safe page payload (no PHI) so the same
    :class:`Pager` transport carries the Sev-1, then returns the failure count
    so the chat path can increment ``huible_paging_failures_total{trigger}``.

    When ``ticket`` is provided (trigger #3, degraded) it is paged directly so
    the real ticket id rides on the page. ``persona_id`` / ``responder_id``
    override the synthetic ticket's fields when no real ticket exists.
    """
    # Imported here to avoid a module-load cycle (app imports paging; handoff
    # is imported by app first). HandoffTicket is only in TYPE_CHECKING at the
    # top of this module.
    from huible.safety.handoff import HandoffTicket

    if ticket is not None:
        return pager.page(
            ticket, severity=PAGE_SEVERITY_SEV1, window=window, trigger=trigger,
        )
    import uuid as _uuid

    synthetic = HandoffTicket(
        id=f"sev1-{trigger}-{_uuid.uuid4().hex[:12]}",
        persona_id=persona_id or "unknown",
        conversation_id=None,
        trigger_signal=trigger,
        affect="sev-1",
        matched_patterns=[],
        risk_flags=[],
    )
    if responder_id:
        synthetic.responder_id = responder_id
    return pager.page(
        synthetic, severity=PAGE_SEVERITY_SEV1, window=window, trigger=trigger,
    )


def page_degraded_net(
    pager: Pager,
    *,
    ticket: HandoffTicket,
    window: str,
) -> int:
    """Page the on-call when the handoff net failed (§3 Sev-1 (B), HU-1451 #3).

    A ``DEGRADED`` ticket means no responder was available / the enqueue
    failed: a grieving user in crisis was NOT helped by a human. That is itself
    a Sev-1 operational failure requiring immediate ceiling intervention
    (distinct from the crisis-enqueue page, which says "a responder is on it").
    This re-uses :data:`PAGE_SEVERITY_SEV1` with the
    :data:`PAGE_TRIGGER_DEGRADED_NET` label. Returns the failure count.
    """
    return pager.page(
        ticket,
        severity=PAGE_SEVERITY_SEV1,
        window=window,
        trigger=PAGE_TRIGGER_DEGRADED_NET,
    )


def escalate_sla_breaches(
    queue: HandoffQueue,
    pager: Pager,
    *,
    window: str,
    now: datetime | None = None,
) -> int:
    """Re-page every ``ENQUEUED`` ticket past its ack SLA (Sev-1 escalation).

    The ack-SLA Sev-1 alert path (item 5): an ``ENQUEUED`` ticket whose
    :func:`huible.safety.handoff_monitoring.sla_status` reports ``breached``
    (the per-ticket ``HANDOFF_SLA_TARGET_SECONDS`` elapsed without an
    acknowledgement) is re-paged at :data:`PAGE_SEVERITY_SEV1`. Called from the
    monitoring touch points (the staffed-responder work-queue read) so a
    breached ticket gets re-paged on every queue check — the canary 900s
    (15-min) ack SLA is the threshold.

    HU-1451: when the pager is a :class:`MultiChannelPager`, the re-page
    escalates — it pages the primary **and** escalates to the secondary + CEO
    (``escalated=True``) so a missed 15-min ack climbs the ceiling, not merely
    re-rings the same device. The roster resolution happens inside the pager.

    Returns the count of re-paged tickets (the Sev-1 pressure signal). The
    existing >10%/1h aggregate stays the rate backstop (unchanged). Paging is
    additive on top of ``ENQUEUED`` and never bypasses the degrade gate: a
    ``DEGRADED`` ticket has no responder paged and so is never re-paged here
    (it is instead handled at enqueue time by :func:`page_degraded_net`).
    """
    # Imported here to avoid a module-load cycle (handoff_monitoring imports
    # handoff; paging is imported by app which imports handoff first).
    from huible.safety.handoff import HandoffOutcome
    from huible.safety.handoff_monitoring import sla_status

    count = 0
    for ticket in queue.list_pending():
        if ticket.outcome is HandoffOutcome.ENQUEUED and sla_status(
            ticket, now=now
        ).breached:
            if isinstance(pager, MultiChannelPager):
                pager.page(
                    ticket,
                    severity=PAGE_SEVERITY_SEV1,
                    window=window,
                    trigger=PAGE_TRIGGER_SLA_BREACH,
                    escalated=True,
                )
            else:
                pager.page(
                    ticket,
                    severity=PAGE_SEVERITY_SEV1,
                    window=window,
                    trigger=PAGE_TRIGGER_SLA_BREACH,
                )
            count += 1
    return count
