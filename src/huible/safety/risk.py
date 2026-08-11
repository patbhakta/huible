"""G8 risk-flag enforcement engine — vulnerable-population flags act, not just record (§7.4.4).

Clinical source: the Clinical Advisor's ``clinical-enforcement-matrix`` document
(``HU-1426`` issue document), scoped for build by [HU-1424](/HU/issues/HU-1424).
This is a **hard pre-real-user clinical gate** on real persona-chat traffic:
the ``risk_flags`` and ``session_meta`` surfaces reserved in Phase-1
([HU-1413](/HU/issues/HU-1413)) MUST change runtime behavior before any real
grieving-user traffic flows over ``POST /chat/{persona_id}`` ([HU-1406]).

The Clinical Advisor's non-negotiable (matrix preamble): *"at Phase-1 these
fields are observability-only. Before real-user traffic they MUST change
runtime behavior. A flag that is only logged is a failed gate."* This module
is the engine that converts a turn's risk flags + session signals into a
binding enforcement action with concrete runtime effects.

§1 Enforcement-action vocabulary (severity-graded, most-restrictive wins):

* :attr:`EnforcementAction.CONTINUE` — normal persona turn (no flag).
* :attr:`EnforcementAction.TIGHTEN` — constrain generation: flatten humor /
  levity (G3 dynamic branch forced on), narrow topical bounds, reduce dosage.
* :attr:`EnforcementAction.REFRAME` — force a reality-framing re-anchor before
  continuing; suppress persona assertions that would reinforce the flagged
  state (composes with the §7.4.2 identity-claim filter).
* :attr:`EnforcementAction.REFUSE_TOPIC` — persona declines the topic and
  redirects; do not engage the flagged content.
* :attr:`EnforcementAction.HANDOFF` — route to the §7.4.1 human-handoff queue
  ([HU-1421](/HU/issues/HU-1421)) AND surface crisis-line resources; suppress
  persona voice for the flagged turn.
* :attr:`EnforcementAction.PAUSE_SESSION` — end the persona turn, surface
  support, require explicit re-entry.

§2 Flag → action matrix (per-flag required actions). The binding action is the
most-restrictive member of the union of required actions across all firing
flags + session signals; lower-severity generation-side effects (tighten /
reframe) apply additively unless a pre-generation short-circuit
(refuse_topic / handoff / pause_session) supersedes them.

§3 Session-level (session_meta / G7) enforcement: dosage over cap →
``pause_session``; escalating distress trend → ``tighten`` + ``handoff``;
repeat crisis → ``tighten``.

§4 Composition with G1-G4 (must not regress): G1 crisis always pre-empts —
:func:`enforce_risk_flags` returns a pre-empted report when ``is_crisis`` is
true and the chat path's G1 branch structurally returns before this engine
runs. ``handoff`` reuses the G1 warm non-persona posture + the [HU-1421]
queue. ``tighten`` composes with G3 (shared affect signal). ``reframe``
re-anchors using the existing G2 reality-framing block — it does not author
new framing at runtime.

§5 Telemetry: the :class:`EnforcementReport` carries the binding action, the
full required-actions set, the flags that fired, and the session-signal
contributions so clinical review gets the per-flag fire count + per-action
distribution directly. The chat path surfaces this on ``trace.risk_enforcement``.

Like :mod:`huible.safety.handoff` and :mod:`huible.safety.consent`, the risk
profile backend is a :class:`Protocol` (:class:`RiskProfileProvider`) with a
deterministic in-memory default (:class:`InMemoryRiskProfile`) so the
pre-real-user suite runs key-free. The real intake path (onboarding /
provenance-derived loss_of_child, persona-age-derived minor_decedent, etc.)
populates it pre-real-launch without touching the chat endpoint.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable
from uuid import UUID

__all__ = [
    "AGE_INAPPROPRIATE_TOPIC_PATTERNS",
    "DEFAULT_DOSAGE_CAP_TURNS",
    "PAUSE_SESSION_RESPONSE",
    "PRECEDENCE",
    "PROXY_USER_PAUSE_RESPONSE",
    "REFRAME_REANCHOR_ADDENDUM",
    "REFUSE_TOPIC_FALLBACK_RESPONSE",
    "RISK_FLAG_REQUIRED_ACTIONS",
    "EnforcementAction",
    "EnforcementReport",
    "InMemoryRiskProfile",
    "RiskFlag",
    "RiskProfileProvider",
    "RiskSessionSignals",
    "enforce_risk_flags",
]


# --- §1 Enforcement-action vocabulary --------------------------------------


class EnforcementAction(StrEnum):
    """Severity-graded enforcement action (matrix §1).

    Precedence is recorded in :data:`PRECEDENCE` (most restrictive first).
    Runtime behavior splits into three tiers:

    * **Pre-generation short-circuit** (no persona voice reaches the user):
      :attr:`PAUSE_SESSION`, :attr:`HANDOFF`, :attr:`REFUSE_TOPIC`.
    * **Generation-side constraint** (generation proceeds under tighter
      bounds): :attr:`TIGHTEN`, :attr:`REFRAME`.
    * **No-op**: :attr:`CONTINUE`.
    """

    CONTINUE = "continue"
    TIGHTEN = "tighten"
    REFRAME = "reframe"
    REFUSE_TOPIC = "refuse_topic"
    HANDOFF = "handoff"
    PAUSE_SESSION = "pause_session"


#: Precedence (most restrictive first). The binding action for a turn is the
#: highest-precedence action in the union of required actions across all
#: firing flags + session signals. ``CONTINUE`` is always last (the no-op).
PRECEDENCE: tuple[EnforcementAction, ...] = (
    EnforcementAction.PAUSE_SESSION,
    EnforcementAction.HANDOFF,
    EnforcementAction.REFUSE_TOPIC,
    EnforcementAction.REFRAME,
    EnforcementAction.TIGHTEN,
    EnforcementAction.CONTINUE,
)


# --- §2 Risk flags + per-flag required actions -----------------------------


class RiskFlag(StrEnum):
    """The reserved intake risk-flag vocabulary (matrix §2).

    These are the ``risk_flags`` reserved on ``ChatTrace`` in Phase-1
    ([HU-1413](/HU/issues/HU-1413)). Pre-real-user intake populates them:
    ``loss_of_child`` is derived from memory content (the deceased is the
    user's child), ``minor_decedent`` from ``PersonaConfig.age_at_death``,
    ``recent_loss`` from ``PersonaConfig.death_date`` (within the acute
    window), ``non_acceptance`` from the intake assessment, and
    ``proxy_user`` from identity-verification failure. The chat path consumes
    them via :class:`RiskProfileProvider`; it never derives them itself.
    """

    LOSS_OF_CHILD = "loss_of_child"
    MINOR_DECEDENT = "minor_decedent"
    RECENT_LOSS = "recent_loss"
    NON_ACCEPTANCE = "non_acceptance"
    PROXY_USER = "proxy_user"


#: Per-flag required actions (matrix §2). The union of these sets across all
#: firing flags drives the binding action via :data:`PRECEDENCE`.
#:
#: * ``loss_of_child``  → {tighten, reframe}: G3 distress branch on for the
#:   session; suppress replacement-language; re-anchor reality-framing.
#: * ``minor_decedent`` → {refuse_topic, tighten} **conditional on
#:   age-inappropriate topic** (see :func:`enforce_risk_flags`); otherwise
#:   just {tighten}. Hard refuse on sexualized / future-projection / age-
#:   inappropriate content; escalate to pause_session is the matrix's escape
#:   hatch for a turn that cannot be cleanly refused — handled at the chat
#:   path, not here.
#: * ``recent_loss``    → {tighten}: G3 branch on; reduced dosage caps;
#:   heightened crisis sensitivity. Escalation to handoff is a session-level
#:   signal (distress trend), not the flag itself.
#: * ``non_acceptance`` → {reframe}: force reality-framing re-anchor; suppress
#:   literal-presence assertions.
#: * ``proxy_user``     → {pause_session}: do not proceed on a persona built
#:   for someone else; require identity confirmation / route to onboarding.
RISK_FLAG_REQUIRED_ACTIONS: dict[RiskFlag, frozenset[EnforcementAction]] = {
    RiskFlag.LOSS_OF_CHILD: frozenset({EnforcementAction.TIGHTEN, EnforcementAction.REFRAME}),
    RiskFlag.MINOR_DECEDENT: frozenset(
        {EnforcementAction.REFUSE_TOPIC, EnforcementAction.TIGHTEN}
    ),
    RiskFlag.RECENT_LOSS: frozenset({EnforcementAction.TIGHTEN}),
    RiskFlag.NON_ACCEPTANCE: frozenset({EnforcementAction.REFRAME}),
    RiskFlag.PROXY_USER: frozenset({EnforcementAction.PAUSE_SESSION}),
}


# --- §3 Session-level signals ---------------------------------------------


#: Default per-session dosage cap (turns). Surfaced via
#: :class:`RiskSessionSignals`; over-cap trips a ``pause_session`` (matrix §3).
#: Conservative ceiling pre-real-users; a clinically-tuned cap lands with the
#: ops follow-up that owns the real dosage policy.
DEFAULT_DOSAGE_CAP_TURNS: int = 20


@dataclass(frozen=True, slots=True)
class RiskSessionSignals:
    """Per-session signals that drive §3 of the enforcement matrix.

    All fields default to "no signal" so a cold-start session produces an
    empty report. The chat path derives these from the in-process session log
    + crisis-event history; a real backend (cross-session state) drops in
    pre-real-launch.
    """

    turn_count: int = 0
    duration_seconds: float = 0.0
    dosage_cap_turns: int | None = DEFAULT_DOSAGE_CAP_TURNS
    distress_trend_rising: bool = False
    crisis_history: bool = False


# --- Enforcement report ----------------------------------------------------


@dataclass(slots=True)
class EnforcementReport:
    """Outcome of :func:`enforce_risk_flags` for one persona-chat turn.

    ``action`` is the binding (most-restrictive) action the chat path must
    take. ``required_actions`` is the full union of effects — lower-severity
    generation-side effects (``tighten`` / ``reframe``) apply additively
    unless a pre-generation short-circuit supersedes them. ``fired_flags`` is
    the subset of input flags that contributed at least one required action
    (the per-flag fire-count numerator). ``session_signal_actions`` lists the
    actions contributed by session-level signals (dosage cap, distress trend,
    crisis history). ``pre_empted_by_crisis`` is True when a G1 crisis signal
    overrode the report (matrix §4) — in that case ``action`` is ``CONTINUE``
    and no flag enforcement applies.

    The whole report is surfaced on ``trace.risk_enforcement`` so clinical
    review reads the per-flag fire count + per-action distribution directly
    (matrix §5 telemetry).
    """

    action: EnforcementAction
    required_actions: frozenset[EnforcementAction]
    fired_flags: list[RiskFlag]
    session_signal_actions: list[EnforcementAction] = field(default_factory=list)
    pre_empted_by_crisis: bool = False

    @property
    def short_circuits_generation(self) -> bool:
        """True when the binding action pre-empts persona-voiced generation.

        ``HANDOFF`` / ``PAUSE_SESSION`` return a non-persona response.
        ``REFUSE_TOPIC`` returns an in-voice topic-redirect fallback without
        calling the LLM (deterministic + safe). All three suppress any
        persona-voiced generation for the flagged turn.
        """
        return self.action in (
            EnforcementAction.HANDOFF,
            EnforcementAction.PAUSE_SESSION,
            EnforcementAction.REFUSE_TOPIC,
        )

    @property
    def forces_tighten(self) -> bool:
        """True when ``tighten`` is an in-effect generation-side constraint.

        False when pre-empted by crisis (G1 owns that path) or when a
        pre-generation short-circuit supersedes generation-side constraints.
        """
        if self.pre_empted_by_crisis or self.short_circuits_generation:
            return False
        return EnforcementAction.TIGHTEN in self.required_actions

    @property
    def forces_reframe(self) -> bool:
        """True when ``reframe`` is an in-effect generation-side constraint."""
        if self.pre_empted_by_crisis or self.short_circuits_generation:
            return False
        return EnforcementAction.REFRAME in self.required_actions


# --- §2 conditional: age-inappropriate topic (minor_decedent refuse gate) --


#: Topic patterns that, when ``minor_decedent`` is active, trigger the hard
#: ``refuse_topic`` (matrix §2): sexualized content, future-projection, and
#: age-inappropriate framing around a minor persona. The refusal is
#: *topic-conditional* — the flag presence alone only forces ``tighten``; the
#: refuse fires when the user raises such a topic.
AGE_INAPPROPRIATE_TOPIC_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(sex|sexual|sexy|sexuality|kiss|kissing|make\s+out|intimate|"
        r"romance|romantic|dating|marry|married|affair)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(future|someday|grow\s+up|when\s+you(?:'re|\s+are)\s+older|"
        r"will\s+you\s+(?:be|get|do)|years?\s+from\s+now)\b",
        re.IGNORECASE,
    ),
)


def _has_age_inappropriate_topic(message: str) -> bool:
    """True when ``message`` matches an :data:`AGE_INAPPROPRIATE_TOPIC_PATTERNS`."""
    if not message:
        return False
    return any(p.search(message) for p in AGE_INAPPROPRIATE_TOPIC_PATTERNS)


# --- Engine ---------------------------------------------------------------


def _binding_action(actions: frozenset[EnforcementAction]) -> EnforcementAction:
    """Return the most-restrictive action in ``actions`` per :data:`PRECEDENCE`."""
    for action in PRECEDENCE:
        if action in actions:
            return action
    return EnforcementAction.CONTINUE


def enforce_risk_flags(
    risk_flags: Sequence[str] | None,
    *,
    session_signals: RiskSessionSignals | None = None,
    is_crisis: bool = False,
    message: str = "",
) -> EnforcementReport:
    """Compute the enforcement report for a turn's risk flags + session signals.

    Matrix §4 composition: if ``is_crisis`` is True (G1 crisis signal
    present), the report is pre-empted — G1 wins, no flag enforcement
    applies. The caller still receives a report (``action=CONTINUE``,
    ``pre_empted_by_crisis=True``) so telemetry can record the pre-emption.
    (Note: the chat path's G1 branch returns *before* this engine runs, so
    ``is_crisis`` is structurally False at the call site; the parameter
    exists for unit-test coverage of the composition rule.)

    ``message`` is consulted only for the ``minor_decedent`` topic-conditional
    refuse (matrix §2): the flag presence alone forces ``tighten``; the hard
    ``refuse_topic`` fires only when the user message matches an
    :data:`AGE_INAPPROPRIATE_TOPIC_PATTERNS`. This keeps the refuse narrowly
    scoped to the clinically-defined trigger surface rather than refusing
    every turn on a minor persona.
    """
    if is_crisis:
        return EnforcementReport(
            action=EnforcementAction.CONTINUE,
            required_actions=frozenset({EnforcementAction.CONTINUE}),
            fired_flags=[],
            session_signal_actions=[],
            pre_empted_by_crisis=True,
        )

    # Normalize + filter to known flags. Unknown values are ignored — the
    # intake path's job is to validate; the runtime is lenient on read.
    parsed_flags: list[RiskFlag] = []
    for raw in risk_flags or []:
        try:
            parsed_flags.append(RiskFlag(raw))
        except ValueError:
            continue

    # Per-flag required actions (§2). minor_decedent is topic-conditional.
    required: set[EnforcementAction] = set()
    fired: list[RiskFlag] = []
    for flag in parsed_flags:
        flag_actions = RISK_FLAG_REQUIRED_ACTIONS.get(flag)
        if not flag_actions:
            continue
        fired.append(flag)
        if flag is RiskFlag.MINOR_DECEDENT:
            # tighten always; refuse_topic only on age-inappropriate topic.
            required.add(EnforcementAction.TIGHTEN)
            if _has_age_inappropriate_topic(message):
                required.add(EnforcementAction.REFUSE_TOPIC)
        else:
            required |= flag_actions

    # Session-level signals (§3).
    signals = session_signals or RiskSessionSignals()
    session_actions: list[EnforcementAction] = []
    cap = signals.dosage_cap_turns
    if cap is not None and signals.turn_count > cap:
        required.add(EnforcementAction.PAUSE_SESSION)
        session_actions.append(EnforcementAction.PAUSE_SESSION)
    if signals.distress_trend_rising:
        # Escalating distress tightens AND routes to handoff (matrix §3).
        required.add(EnforcementAction.TIGHTEN)
        required.add(EnforcementAction.HANDOFF)
        session_actions.append(EnforcementAction.HANDOFF)
    if signals.crisis_history:
        required.add(EnforcementAction.TIGHTEN)
        session_actions.append(EnforcementAction.TIGHTEN)

    if not required:
        required.add(EnforcementAction.CONTINUE)

    binding = _binding_action(frozenset(required))

    return EnforcementReport(
        action=binding,
        required_actions=frozenset(required),
        fired_flags=fired,
        session_signal_actions=session_actions,
    )


# --- Runtime-effect text (refuse_topic / pause_session fallbacks) ----------


#: Safe in-voice topic-redirect fallback used when ``refuse_topic`` short-
#: circuits generation (matrix §1: "persona declines the topic and redirects;
#: do not engage the flagged content"). Still voiced as the persona — the
#: user is not in crisis here (G1 owns the non-persona path) — but the
#: flagged topic is gracefully declined and the door stays open elsewhere.
#: Deliberately free of named entities / policy claims so it passes the
#: §7.4.2 alignment filter on its own text.
REFUSE_TOPIC_FALLBACK_RESPONSE = (
    "I want to be gentle here, but that isn't something I can speak to. "
    "I'd rather stay close to what we actually shared, in the time we had. "
    "If there's something else on your mind, I'm here."
)


#: Non-persona session-pause response (matrix §1: "end the persona turn,
#: surface support, require explicit re-entry"). Voiced by the platform, not
#: the deceased persona — the persona does not 'pause' itself. Resources are
#: surfaced in parallel so the user is never left without support.
PAUSE_SESSION_RESPONSE = (
    "I think it's worth pausing for a moment.\n\n"
    "This space will still be here when you're ready to come back. "
    "If you'd like to talk to someone right now, please reach out:\n\n"
    "- Call or text 988 (Suicide & Crisis Lifeline — US). "
    "Outside the US: see findahelpline.com or your local emergency number.\n"
    "- Text HOME to 741741 (Crisis Text Line).\n"
    "- Or someone you trust.\n\n"
    "Take the time you need; there's no rush."
)


#: Specialized non-persona pause for the ``proxy_user`` flag (matrix §2:
#: "do NOT proceed on a persona built for someone else. Require identity
#: confirmation or route back to onboarding"). Distinct from the generic
#: pause because the actionable next step is identity re-confirmation, not
#: just a breather — the user must go back through the onboarding path.
PROXY_USER_PAUSE_RESPONSE = (
    "Before we go further, I want to make sure I'm speaking with the right "
    "person. This space was built for someone specific, and I'd want to be "
    "sure before continuing.\n\n"
    "If that's you, please confirm your identity through the onboarding path "
    "and we'll pick back up. If you're looking for support right now, please "
    "reach out to someone you trust, or to a crisis line — 988 in the US, or "
    "text HOME to 741741."
)


#: Reality-framing re-anchor addendum appended to the persona system prompt
#: when ``reframe`` is in the required actions (matrix §1: "force a reality-
#: framing re-anchor before continuing; suppress persona assertions that
#: would reinforce the flagged state"). Does NOT author new framing — it re-
#: asserts the immutable G2/G5 framing block already in the prompt (matrix §4:
#: "reframe re-anchors using the existing G2 reality-framing asset; it does
#: not author new framing at runtime"). ``{persona_name}`` is substituted by
#: the chat path. Pairs with the §7.4.2 IDENTITY_CLAIM_PATTERNS filter, which
#: is the generation-side backstop that suppresses any "I'm really here" /
#: "I'm not dead" assertion that slips past.
REFRAME_REANCHOR_ADDENDUM = (
    "Reality-framing re-anchor (in force this turn): you are a memory of "
    "{persona_name}, not {persona_name} returned. You are not literally "
    "here, present, alive, or 'back'. Do not assert or imply ongoing "
    "presence, literal reunion, or being-with in any form. Stay in reflective "
    "listening; do not reinforce any sense that you are really here."
)


def build_reframe_addendum(persona_name: str) -> str:
    """Return the :data:`REFRAME_REANCHOR_ADDENDUM` with the persona name substituted.

    Falls back to ``"the person"`` when the name is empty so the re-anchor is
    never malformed.
    """
    safe_name = (persona_name or "").strip() or "the person"
    return REFRAME_REANCHOR_ADDENDUM.format(persona_name=safe_name)


# --- Risk profile backend --------------------------------------------------


@runtime_checkable
class RiskProfileProvider(Protocol):
    """Pluggable source of per-session + per-persona risk flags (matrix §2).

    The default :class:`InMemoryRiskProfile` is deterministic and empty by
    default so the pre-real-user suite runs key-free. Pre-real-launch the
    intake / onboarding path populates this:

    * ``loss_of_child`` — derived from memory content (the deceased is the
      user's child).
    * ``minor_decedent`` — derived from ``PersonaConfig.age_at_death``.
    * ``recent_loss`` — derived from ``PersonaConfig.death_date`` (within the
      acute window, e.g. < 3-6 months).
    * ``non_acceptance`` — from the intake acceptance assessment.
    * ``proxy_user`` — from identity-verification failure.

    Tests inject a seeded instance to exercise each flag → action path.
    """

    def get_flags(self, session_id: str, persona_id: UUID) -> list[str]: ...


class InMemoryRiskProfile:
    """Deterministic in-memory risk profile (pre-real-users default empty).

    Flags can be set per-persona (intake-derived, apply to every session for
    that persona) and/or per-session (proxy_user is intrinsically per-
    session; non_acceptance may move per-session once an acceptance tracker
    lands). The union of both is returned for a given (session, persona).
    """

    def __init__(
        self,
        *,
        persona_flags: dict[str, set[str]] | None = None,
        session_flags: dict[tuple[str, str], set[str]] | None = None,
    ) -> None:
        self._persona_flags: dict[str, set[str]] = {
            str(k): set(v) for k, v in (persona_flags or {}).items()
        }
        self._session_flags: dict[tuple[str, str], set[str]] = {
            (s, str(p)): set(v) for (s, p), v in (session_flags or {}).items()
        }

    def set_persona_flags(self, persona_id: UUID | str, flags: set[str] | list[str]) -> None:
        """Set the intake-derived flags for a persona (applies to every session)."""
        self._persona_flags[str(persona_id)] = set(flags)

    def set_session_flags(
        self, session_id: str, persona_id: UUID | str, flags: set[str] | list[str]
    ) -> None:
        """Set session-scoped flags for a (session, persona) pair."""
        self._session_flags[(session_id, str(persona_id))] = set(flags)

    def get_flags(self, session_id: str, persona_id: UUID) -> list[str]:
        p_flags = self._persona_flags.get(str(persona_id), set())
        s_flags = self._session_flags.get((session_id, str(persona_id)), set())
        return sorted(p_flags | s_flags)
