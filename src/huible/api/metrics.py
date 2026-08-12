"""Prometheus metrics for persona-chat + the §7.4 guardrails (Stage 0.3, HU-1446).

The HU-1436 rollout plan §3 requires observability of every guardrail under
real load: chat turns/latency/errors, G1 crisis fires, G6 consent-required,
§7.4.1 handoff enqueue/degrade + SLA breach, §7.4.2 un-grounded-claim count +
disposition, §7.4.4 enforcement-action distribution per risk flag. This module
owns those instruments and a single :func:`record_chat_turn` entry point the
chat handler calls at every exit (turn + guardrail fire bits in one place).

Clinical dependency (Clinical Advisor §3 note, recorded on HU-1446): the three
Sev-1 alert conditions page the 0.4 on-call only once the roster + paging
target are wired ([HU-1447]). The counters here are the *signal*; the
alert→paging wire is a checklist item on HU-1446 that lands when HU-1447 names
the roster. Default-to-OFF on ambiguous signal is the load-bearing posture —
the counters never suppress a page on uncertainty.

No PHI is emitted. Counters carry only labels that are safe to aggregate
(outcome, guardrail kind, risk-flag name, handoff outcome) — never message
text, session ids, or persona names.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

if TYPE_CHECKING:
    from uuid import UUID

logger = logging.getLogger(__name__)

__all__ = [
    "CONTENT_TYPE_LATEST",
    "ChatTurnOutcome",
    "metrics_response",
    "record_chat_turn",
    "record_paging_failures",
]


# --- instruments -----------------------------------------------------------
# Names are prefixed ``huible_`` so they are unambiguous in a shared scrape.
# Label cardinality is deliberately tiny (outcome / kind / action / flag).

CHAT_TURNS = Counter(
    "huible_chat_turns_total",
    "Persona-chat turns processed on POST /api/v1/chat/{persona_id}.",
    ["outcome"],
)

CHAT_LATENCY = Histogram(
    "huible_chat_turn_latency_seconds",
    "Persona-chat turn wall-clock latency (handler entry → response).",
    ["outcome"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0),
)

CHAT_ERRORS = Counter(
    "huible_chat_errors_total",
    "Persona-chat turns that raised (HTTP 4xx/5xx) rather than returning a chat response.",
    ["status_class"],
)

# G1 / §7.4.1 crisis + handoff
CRISIS_FIRES = Counter(
    "huible_crisis_fires_total",
    "G1 crisis signals detected on a persona-chat turn (pre-generation).",
)
HANDOFF_OUTCOMES = Counter(
    "huible_handoff_outcomes_total",
    "§7.4.1 handoff escalations by queue outcome (enqueued/degraded/answered/abandoned).",
    ["outcome"],
)

# G6 / §7.4.3 consent
CONSENT_REQUIRED = Counter(
    "huible_consent_required_total",
    "G6 first-use reality-framing consents required (HTTP 409 before persona voice).",
)

# §7.4.2 alignment
UNGROUNDED_CLAIMS = Counter(
    "huible_ungrounded_claims_total",
    "§7.4.2 persona claims detected as un-grounded (no supporting memory ref).",
)
ALIGNMENT_DISPOSITIONS = Counter(
    "huible_alignment_dispositions_total",
    "§7.4.2 alignment-filter dispositions applied this turn (suppressed/passed/refrained).",
    ["disposition"],
)

# §7.4.4 G8 risk enforcement
RISK_ENFORCEMENT_ACTIONS = Counter(
    "huible_risk_enforcement_actions_total",
    "§7.4.4 G8 binding actions taken, by action "
    "(continue/tighten/reframe/refuse_topic/handoff/pause_session).",
    ["action"],
)
RISK_FLAG_FIRES = Counter(
    "huible_risk_flag_fires_total",
    "§7.4.4 risk flags present on a turn where G8 enforcement evaluated, by flag.",
    ["flag"],
)

# Stage 0.1 kill switch
REAL_USER_REFUSED = Counter(
    "huible_real_user_refused_total",
    "Stage 0.1 kill-switch refusals — real-user turns refused while "
    "PERSONA_CHAT_REAL_USER_MODE blocks.",
)

# Stage 0.7 hard kill switch (HU-1462, MANDATORY). Distinct from the ramp-gate
# counter above: this counts 503 SERVICE_DISABLED refusals under
# PERSONA_CHAT_REAL_USER_TRAFFIC=off — the primary rollback path (plan §4.2).
# A non-zero value during normal (non-rollback) operation is itself an alert:
# it means the emergency brake is engaged.
REAL_USER_TRAFFIC_DISABLED = Counter(
    "huible_real_user_traffic_disabled_total",
    "Stage 0.7 hard kill-switch refusals — real-user turns refused with HTTP "
    "503 SERVICE_DISABLED while PERSONA_CHAT_REAL_USER_TRAFFIC=off "
    "(HU-1462 primary rollback path). Internal/synthetic traffic is unaffected.",
)

# Gauge for ops readiness (alert wiring target)
ALERT_ONCALL_CONFIGURED = Gauge(
    "huible_alert_oncall_configured",
    "1 once the §3 Sev-1 alerts are wired to page the 0.4 on-call roster; 0 "
    "until HU-1447 names the roster + paging target "
    "(Clinical Advisor dependency on HU-1446).",
)

# §3 Sev-1 paging failures (HU-1451 AC #3). A page-send failure (a real channel
# — Telnyx SMS / email / webhook — errored or never landed) is itself a Sev-1
# counter: the clinical turn continued unaffected (paging is fire-and-forget),
# but a page that did not reach a human device is a safety event the operator
# must see. ``trigger`` is the :data:`huible.api.paging.PAGE_TRIGGER_*` label.
PAGING_FAILURES = Counter(
    "huible_paging_failures_total",
    "§3 Sev-1 page-send failures by trigger (HU-1451). A real channel "
    "(Telnyx/email/webhook) errored on send; the clinical turn was not "
    "affected, but the page did not land. The log fallback is not a failure.",
    ["trigger"],
)


@dataclass(slots=True)
class ChatTurnOutcome:
    """Per-turn outcome labels for :func:`record_chat_turn`.

    Carries only aggregate-safe labels — no PHI. ``outcome`` is the coarse turn
    fate (e.g. ``persona``, ``crisis``, ``consent_required``,
    ``real_user_refused``, ``error``). Guardrail-fire bits are optional; the
    handler sets the ones it knows.
    """

    outcome: str
    latency_s: float
    persona_id: UUID | None = None
    crisis: bool = False
    consent_required: bool = False
    real_user_refused: bool = False
    ungrounded_claims: int = 0
    alignment_disposition: str | None = None
    risk_action: str | None = None
    risk_flags: tuple[str, ...] = ()
    handoff_outcome: str | None = None
    status_class: str | None = None  # e.g. "4xx", "5xx" on errors


def record_chat_turn(result: ChatTurnOutcome) -> None:
    """Record one persona-chat turn's metrics + structured access-log line.

    Called by the chat handler at every exit (return or raise). Idempotent per
    turn — the handler invokes it exactly once per request. Emits the JSON
    access log with the guardrail-fire bits (no PHI) and increments every
    relevant counter.
    """
    CHAT_TURNS.labels(outcome=result.outcome).inc()
    CHAT_LATENCY.labels(outcome=result.outcome).observe(result.latency_s)
    if result.status_class:
        CHAT_ERRORS.labels(status_class=result.status_class).inc()
    if result.crisis:
        CRISIS_FIRES.inc()
    if result.consent_required:
        CONSENT_REQUIRED.inc()
    if result.real_user_refused:
        REAL_USER_REFUSED.inc()
    if result.ungrounded_claims:
        UNGROUNDED_CLAIMS.inc(result.ungrounded_claims)
    if result.alignment_disposition:
        ALIGNMENT_DISPOSITIONS.labels(disposition=result.alignment_disposition).inc()
    if result.risk_action:
        RISK_ENFORCEMENT_ACTIONS.labels(action=result.risk_action).inc()
    for flag in result.risk_flags:
        RISK_FLAG_FIRES.labels(flag=flag).inc()
    if result.handoff_outcome:
        HANDOFF_OUTCOMES.labels(outcome=result.handoff_outcome).inc()

    logger.info(
        "persona_chat.turn",
        extra={
            "chat_turn": {
                "outcome": result.outcome,
                "latency_ms": round(result.latency_s * 1000.0, 2),
                "crisis": result.crisis,
                "consent_required": result.consent_required,
                "real_user_refused": result.real_user_refused,
                "ungrounded_claims": result.ungrounded_claims,
                "alignment_disposition": result.alignment_disposition,
                "risk_action": result.risk_action,
                "risk_flags": list(result.risk_flags),
                "handoff_outcome": result.handoff_outcome,
            }
        },
    )


def metrics_response() -> tuple[bytes, str]:
    """Return ``(body_bytes, content_type)`` for the ``GET /metrics`` endpoint."""
    return generate_latest(), CONTENT_TYPE_LATEST


def record_paging_failures(trigger: str, count: int) -> None:
    """Record real-channel page-send failures for the §3 Sev-1 paging counter.

    Called fire-and-forget by the chat-path paging wrappers (HU-1451 AC #3).
    ``trigger`` is the :data:`huible.api.paging.PAGE_TRIGGER_*` label so the
    counter distinguishes *which* Sev-1 condition's page did not land.
    ``count`` is the number of real-channel failures (0 = page landed or only
    the key-free log fallback ran). A non-positive count is a no-op.
    """
    if count <= 0:
        return
    PAGING_FAILURES.labels(trigger=trigger).inc(count)
