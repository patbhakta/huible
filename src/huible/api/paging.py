"""On-call paging notifier for §7.4.1 crisis escalations (Stage 0.4, HU-1450).

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
degrade) stays authoritative — paging is **additive** on top of ``ENQUEUED``,
never a bypass around the degrade gate. A degraded ticket is never paged (no
responder was paged, so the on-call must not be told one was).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import httpx

if TYPE_CHECKING:
    from datetime import datetime

    from huible.safety.handoff import HandoffQueue, HandoffTicket

logger = logging.getLogger(__name__)

__all__ = [
    "PAGE_SEVERITY_CRISIS",
    "PAGE_SEVERITY_SEV1",
    "LoggingPager",
    "Pager",
    "WebhookPager",
    "build_pager",
    "escalate_sla_breaches",
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

#: HTTP timeout for the webhook POST. Kept short so a slow pager never stalls a
#: clinical turn — the chat path pages synchronously on enqueue.
_WEBHOOK_TIMEOUT_S: float = 10.0


@runtime_checkable
class Pager(Protocol):
    """Pluggable on-call paging transport.

    The default :class:`LoggingPager` is key-free (a CRITICAL log line). A real
    transport (Slack incoming webhook / PagerDuty Events API v2) drops in via
    :class:`WebhookPager` + ``HANDOFF_PAGER_WEBHOOK_URL`` at deploy time without
    touching callers. ``page`` is best-effort: a paging failure must never
    break a clinical turn (the chat path wraps the call defensively), so
    implementations swallow transport errors and fall back to the log line.
    """

    def page(self, ticket: HandoffTicket, *, severity: str, window: str) -> None:
        """Page the on-call for ``ticket`` at ``severity`` within ``window``.

        ``window`` is a human-readable label for the active coverage window
        (e.g. ``"always"`` or ``"hours 09:00-17:00 America/New_York"``) so the
        operator knows which seat is paged. No PHI is emitted — only the
        ticket id, trigger signal, severity, and window.
        """
        ...


class LoggingPager:
    """Key-free default pager: a structured ``handoff.page`` CRITICAL log line.

    This is the minimal honest page an operator can scrape and alert on without
    external credentials. It is deliberately distinct from the existing
    ``handoff.enqueue`` INFO audit line (which records the queue outcome for the
    clinical audit log) — ``handoff.page`` is the operator alert channel. The
    log carries only aggregate-safe fields (ticket id, trigger signal,
    severity, window, persona id, responder id); never message text or session
    ids.     An operator wires a log scrape rule (e.g. match ``level=CRITICAL`` on a
    ``handoff.page`` message prefix) to turn this into a real alert.
    """

    def page(self, ticket: HandoffTicket, *, severity: str, window: str) -> None:
        logger.critical(
            "handoff.page ticket=%s severity=%s signal=%s window=%s persona=%s responder=%s",
            ticket.id,
            severity,
            ticket.trigger_signal,
            window,
            ticket.persona_id,
            ticket.responder_id,
        )


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

    def page(self, ticket: HandoffTicket, *, severity: str, window: str) -> None:
        if not self._url:
            self._fallback.page(ticket, severity=severity, window=window)
            return
        payload = {
            "ticket_id": ticket.id,
            "severity": severity,
            "trigger_signal": ticket.trigger_signal,
            "window": window,
            "persona_id": ticket.persona_id,
            "responder_id": ticket.responder_id,
            "sla_target_seconds": ticket.sla_target_seconds,
            "text": (
                f"[Huible handoff] {severity} page for ticket {ticket.id} "
                f"({ticket.trigger_signal}); on-call={ticket.responder_id} "
                f"window={window}"
            ),
        }
        try:
            resp = httpx.post(self._url, json=payload, timeout=_WEBHOOK_TIMEOUT_S)
            resp.raise_for_status()
        except Exception:
            logger.exception(
                "handoff.page webhook failed; falling back to log pager (ticket=%s)",
                ticket.id,
            )
            self._fallback.page(ticket, severity=severity, window=window)


def build_pager(*, provider: str, webhook_url: str) -> Pager:
    """Construct the configured :class:`Pager` from settings.

    ``provider="webhook"`` selects :class:`WebhookPager`; when the URL is empty
    it transparently falls back to :class:`LoggingPager` (key-free default).
    Any other value (including the default ``"log"``) selects
    :class:`LoggingPager`. This is the paging equivalent of
    :func:`huible.llm.client.build_llm_client`'s key-free-fallback rule.
    """
    if provider == "webhook":
        return WebhookPager(webhook_url)
    return LoggingPager()


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

    Returns the count of re-paged tickets (the Sev-1 pressure signal). The
    existing >10%/1h aggregate stays the rate backstop (unchanged). Paging is
    additive on top of ``ENQUEUED`` and never bypasses the degrade gate: a
    ``DEGRADED`` ticket has no responder paged and so is never re-paged here.
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
            pager.page(ticket, severity=PAGE_SEVERITY_SEV1, window=window)
            count += 1
    return count
