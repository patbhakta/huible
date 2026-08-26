"""Prometheus metrics for persona-chat + the §7.4 guardrails (Stage 0.3, HU-1446).

The HU-1436 rollout plan §3 requires observability of every guardrail under
real load: chat turns/latency/errors, G1 crisis fires, G6 consent-required,
§7.4.1 handoff enqueue/degrade + SLA breach, §7.4.2 un-grounded-claim count +
disposition, §7.4.4 enforcement-action distribution per risk flag. This module
owns those instruments and a single :func:`record_chat_turn` entry point the
chat handler calls at every exit (turn + guardrail-fire bits in one place).

Stage 0.8 (HU-1463) adds the §3 SLO *gauges* on top of the guardrail counters:
the handoff SLA telemetry (degrade rate, pending breach, answered-within-SLA
rate) and the ``/health`` status are mirrored into Prometheus on every scrape
so the launch-plan §3.1/§3.2 SLO table + §4.1 rollback triggers are observable
without parsing the JSON dashboard. The gauges are set from the same
:data:`huible.safety.handoff_monitoring.HandoffTelemetry` the
``/api/v1/handoff/audit`` endpoint already returns, so the Prometheus view and
the JSON dashboard cannot drift. The alert rules in
``examples/prometheus-alerts.yml`` page on these gauges (degrade rate > 0,
pending breach, answered-within-SLA below ramp threshold, health degraded,
latency/error-budget burn).

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
from dataclasses import dataclass, field
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
    "record_handoff_telemetry",
    "record_health_status",
    "record_paging_failures",
    "record_paging_drill_suppressed",
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

# §7.4.2 per-category un-grounded claim signal (Clinical Advisor §7.4.2
# monitoring ask, HU-1461). ``huible_ungrounded_claims_total`` above is the
# aggregate leak volume; this labeled counter is the clinically-meaningful
# breakdown — identity / advice / biographical / relationship — so a real-model
# drift in a *single* category is visible on the SLO dashboard without parsing
# the per-turn ``trace.alignment`` JSON. Label values are the fixed
# :class:`huible.safety.alignment.ClaimCategory` set (tiny cardinality). Derive
# the per-category leak rate via PromQL over the persona-turn denominator:
#   sum(rate(huible_alignment_ungrounded_claims_total[5m])) by (category).
ALIGNMENT_UNGROUNDED_BY_CATEGORY = Counter(
    "huible_alignment_ungrounded_claims_total",
    "§7.4.2 un-grounded persona claims by claim category "
    "(identity/advice/biographical/relationship) — the per-category leak "
    "signal for real-model drift observability.",
    ["category"],
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

# HU-1926 chat-surface consolidation: the generic POST /api/v1/chat is a
# deprecated 308 shim onto the persona-scoped surface. This counter is the
# migration telemetry — any non-zero value after consumer migration means a
# caller is still wired to the retired surface and must be moved before
# cohort exposure.
GENERIC_CHAT_SHIM_REDIRECTS = Counter(
    "huible_generic_chat_shim_redirects_total",
    "Hits on the deprecated generic POST /api/v1/chat, answered with HTTP 308 "
    "to /api/v1/chat/{persona_id} (HU-1926). The shim performs no persona "
    "generation; every hit is a caller that still needs migrating.",
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

# Drill-traffic paging suppression (HU-1428 pre-work, digest #5 watch item).
# A page whose ticket carried a drill marker was routed to the LoggingPager
# instead of the real channels. Non-zero during a drill window *proves*
# suppression is working; a real-user page must never appear here.
PAGING_DRILL_SUPPRESSED = Counter(
    "huible_paging_drill_suppressed_total",
    "Pages whose traffic matched a drill marker and were suppressed from "
    "real channels to the log line (HU-1428 drill suppression). Expected "
    "non-zero during verification drills; must stay untouched by real-user "
    "traffic.",
    ["trigger"],
)


# --- Stage 0.8: §3 SLO gauges (HU-1463) -------------------------------------
# These gauges mirror the handoff SLA telemetry that /api/v1/handoff/audit
# already returns and the /health status. They are *set* on every /metrics
# scrape by :func:`record_handoff_telemetry` / :func:`record_health_status`
# (called from the /metrics handler in app.py) so the Prometheus view cannot
# drift from the JSON dashboard. Together with the counters + histogram above
# they cover every signal in the launch-plan §3.1 (guardrail-health) and §3.2
# (service-health) SLO tables and every §4.1 rollback trigger. The alert rules
# in examples/prometheus-alerts.yml page on these gauges.

# §3.1 guardrail-health SLOs (handoff queue).
HANDOFF_DEGRADE_RATE = Gauge(
    "huible_handoff_degrade_rate",
    "§3.1 handoff degrade rate (degraded / total) over the rolling telemetry "
    "window (HANDOFF_TELEMETRY_WINDOW_SECONDS, default 24h — HU-1865). The "
    "fail-safe firing share of *recent* escalations. Healthy = 0.0 (every "
    "escalation reached a human). Launch-plan §4.1 rollback trigger: > 0 "
    "halts the ramp. Historical degrades outside the window no longer pin "
    "this gauge.",
)
HANDOFF_PENDING_BREACHED = Gauge(
    "huible_handoff_pending_breached",
    "§3.1 open (ENQUEUED) handoff tickets currently past their SLA target — "
    "the live breach count. Healthy = 0. Launch-plan §4.1 rollback trigger: "
    "any unacknowledged pending breach halts the ramp.",
)
HANDOFF_PENDING_BREACH_RATE = Gauge(
    "huible_handoff_pending_breach_rate",
    "§3.1 pending_breached / pending — live breach pressure over open tickets. Healthy = 0.0.",
)
HANDOFF_ANSWERED_WITHIN_SLA_RATE = Gauge(
    "huible_handoff_answered_within_sla_rate",
    "§3.1 answered-within-SLA rate (1 - answered_breach_rate). The direct "
    "ramp-gate metric: >= 0.9 at Stage 1, >= 0.95 at Stage 2+. Higher is better.",
)
HANDOFF_TICKETS_TOTAL = Gauge(
    "huible_handoff_tickets_total",
    "Handoff tickets in the rolling telemetry window "
    "(HANDOFF_TELEMETRY_WINDOW_SECONDS, default 24h) at last scrape "
    "(all outcomes). Context gauge for the rates above — HU-1865.",
)
HANDOFF_PENDING = Gauge(
    "huible_handoff_pending",
    "Open (ENQUEUED) handoff tickets at last scrape. The queue-depth signal.",
)
# Roster-staffing signal (HU-1880 — §7.4 alert-enablement point). Mirrors the
# live queue's available-responder count on every scrape. The alert rules gate
# HuibleHandoffDegradeRate *paging* on this gauge > 0: pre-staffing degrades
# are the clinically-correct G1 fail-safe (expected, tracked at ticket
# severity — never paged); paging arms exactly when the roster is staffed, so
# the enablement point is roster staffing by construction, no timing decision
# baked into code.
HANDOFF_AVAILABLE_RESPONDERS = Gauge(
    "huible_handoff_available_responders",
    "§7.4.1 available responders on the live handoff queue at last scrape "
    "(HANDOFF_AVAILABLE_RESPONDERS). The §7.4 alert-enablement signal: "
    "HuibleHandoffDegradeRate pages only when this is > 0 (roster staffed); "
    "pre-staffing degrades are the expected G1 fail-safe and page no one "
    "(HU-1880).",
)

# §3.2 service-health SLO: /health status. 1 = ok, 0 = degraded. Mirrors the
# ``status`` field of GET /api/v1/health. Launch-plan §4.1 rollback trigger:
# ``degraded`` halts the ramp.
HEALTH_STATUS = Gauge(
    "huible_health_status",
    "§3.2 /health probe status: 1 = ok, 0 = degraded (a wired DB check failed). "
    "Launch-plan §4.1 rollback trigger: degraded halts the ramp.",
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
    # Per-category un-grounded claim counts (Clinical Advisor §7.4.2 monitoring
    # ask, HU-1461). Source: AlignmentReport.category_counts() — only the
    # un-grounded subset. Empty on passed turns (no leak signal).
    ungrounded_by_category: dict[str, int] = field(default_factory=dict)
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
    for category, count in result.ungrounded_by_category.items():
        if count:
            ALIGNMENT_UNGROUNDED_BY_CATEGORY.labels(category=category).inc(count)
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
                "ungrounded_by_category": dict(result.ungrounded_by_category),
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


def record_paging_drill_suppressed(trigger: str) -> None:
    """Record a drill-suppressed page (HU-1428 drill-marker suppression).

    Called by the paging wire when a page matched a drill marker and was
    routed to the log line instead of the real channels. ``trigger`` is the
    :data:`huible.api.paging.PAGE_TRIGGER_*` label. The counter is the
    operator-visible proof that drill traffic can never ring a real on-call
    device once credentials land.
    """
    PAGING_DRILL_SUPPRESSED.labels(trigger=trigger).inc()


def record_handoff_telemetry(telemetry: object) -> None:
    """Mirror the handoff SLA telemetry into the §3.1 SLO gauges (HU-1463).

    Called by the ``/metrics`` handler on every scrape with the
    :data:`huible.safety.handoff_monitoring.compute_handoff_telemetry` result
    over the wired queue's audit log. Keeps the Prometheus view identical to
    the ``/api/v1/handoff/audit`` JSON dashboard so the launch-plan §3.1
    guardrail-health SLOs and §4.1 rollback triggers are observable from a
    scrape alone. Tolerates a ``None`` telemetry (no-op) so the bare-app
    bootstrap path stays metric-safe.

    Reads the telemetry fields by attribute (duck-typed) rather than importing
    the dataclass to avoid a cycle (``handoff_monitoring`` does not depend on
    this module, and we keep it that way).
    """
    if telemetry is None:
        return
    HANDOFF_DEGRADE_RATE.set(getattr(telemetry, "degrade_rate", 0.0) or 0.0)
    HANDOFF_PENDING_BREACHED.set(getattr(telemetry, "pending_breached", 0) or 0)
    HANDOFF_PENDING_BREACH_RATE.set(getattr(telemetry, "pending_breach_rate", 0.0) or 0.0)
    # The launch-plan §3.1 ramp gate reads "answered-within-SLA rate" (higher =
    # better). The telemetry exposes the inverted miss rate
    # (``answered_breach_rate``); convert here so the gauge name matches the
    # SLO table direction.
    answered_breach_rate = getattr(telemetry, "answered_breach_rate", 0.0) or 0.0
    HANDOFF_ANSWERED_WITHIN_SLA_RATE.set(max(0.0, 1.0 - answered_breach_rate))
    HANDOFF_TICKETS_TOTAL.set(getattr(telemetry, "total", 0) or 0)
    HANDOFF_PENDING.set(getattr(telemetry, "pending", 0) or 0)


def record_handoff_responder_readiness(available_responders: object) -> None:
    """Mirror the live queue staffing into the §7.4 enablement gauge (HU-1880).

    Called by the ``/metrics`` handler on every scrape with the wired queue's
    ``available_responders``. The alert rules gate degrade-rate *paging* on
    this gauge > 0 — the enablement point is roster staffing itself, so the
    page arms the moment ops sets ``HANDOFF_AVAILABLE_RESPONDERS`` without a
    code change or timing decision.
    """
    HANDOFF_AVAILABLE_RESPONDERS.set(int(available_responders or 0))


def record_health_status(status: str) -> None:
    """Mirror the ``/health`` probe status into the §3.2 health gauge (HU-1463).

    Called by the ``/metrics`` handler on every scrape. ``status`` is the
    ``status`` field of :class:`huible.api.schemas.HealthCheck` (``ok`` or
    ``degraded``). 1 = ok, 0 = degraded — the convention Prometheus alerting
    expects for a boolean service-health signal. Unknown values map to 0
    (fail-safe: an unexpected status string is itself a degradation signal).
    """
    HEALTH_STATUS.set(1.0 if status == "ok" else 0.0)
