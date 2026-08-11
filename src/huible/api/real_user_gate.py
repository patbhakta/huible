"""Real-user traffic ramp gate / kill switch for persona-chat (Stage 0.1, HU-1444).

The §7.4 clinical gate being closed does not by itself make a real-user
traffic flip safe (see the HU-1436 rollout plan §0). This module is the
operational kill switch + staged-ramp gate: it refuses grieving-user turns
unless the runtime mode is ``canary``/``open`` AND the persona is allowlisted
(for ``canary``). One env flip (``PERSONA_CHAT_REAL_USER_MODE=off``) is the
documented rollback action (plan §4).

Real-user traffic is distinguished from internal/synthetic traffic via the
``X-Huible-Traffic-Class`` request header. The default (header absent or
unknown) is ``real`` — the safe direction: an unmarked client is treated as a
grieving user and refused when the switch is off, so a misconfigured prod
client can never accidentally be served the deceased-persona voice pre-flip.
The ``internal`` class is what the test suite and synthetic probes send.

Refusals never reach the deceased-persona voice. The chat handler returns the
warm, non-persona :data:`REAL_USER_MODE_OFF_RESPONSE` (with crisis-line
resources) — the same "safe non-persona response" posture as the G1 crisis
branch (HU-1407 §7.1 G1), not a persona-voiced message.

Note on the SMS surface: the ``flows/converse.yaml`` Kestra flow is a separate
Telnyx-webhook → standalone-Python path that does NOT pass through this gate
(or any §7.4 guardrail). The kill switch cannot refuse SMS-originated turns
in-process; keeping the SMS webhook disabled for real grieving-user traffic is
therefore a Stage 1 entry criterion tracked on HU-1436.
"""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

__all__ = [
    "REAL_USER_MODE_OFF_RESPONSE",
    "REAL_USER_TRAFFIC_CLASS_HEADER",
    "RealUserMode",
    "TrafficClass",
    "is_real_user_turn_refused",
    "parse_real_user_mode",
    "traffic_class_from_header",
]

#: Request header that marks a turn as internal/synthetic. Absent → ``real``.
REAL_USER_TRAFFIC_CLASS_HEADER = "X-Huible-Traffic-Class"


class RealUserMode(StrEnum):
    """Runtime ramp state for ``POST /api/v1/chat/{persona_id}`` real-user turns."""

    OFF = "off"
    CANARY = "canary"
    OPEN = "open"


class TrafficClass(StrEnum):
    """Caller class. ``REAL`` is the safe default for any unrecognized value."""

    REAL = "real"
    INTERNAL = "internal"


def parse_real_user_mode(raw: str | None) -> RealUserMode:
    """Parse the mode setting; unknown/blank values default to ``OFF``.

    Ambiguous signal → off is the load-bearing safety posture of the whole
    rollout (Clinical Advisor + PM ratified, plan §3/§4).
    """
    if not raw:
        return RealUserMode.OFF
    try:
        return RealUserMode(raw.strip().lower())
    except ValueError:
        return RealUserMode.OFF


def traffic_class_from_header(value: str | None) -> TrafficClass:
    """Resolve the traffic class from the request header.

    Absent / blank / unknown → :attr:`TrafficClass.REAL` (safe direction: an
    unmarked client is treated as a grieving user).
    """
    if not value:
        return TrafficClass.REAL
    try:
        return TrafficClass(value.strip().lower())
    except ValueError:
        return TrafficClass.REAL


def is_real_user_turn_refused(
    mode: RealUserMode,
    traffic_class: TrafficClass,
    persona_id: UUID,
    canary_allowlist: frozenset[UUID],
) -> bool:
    """Return True when this real-user turn must be refused (never persona voice).

    Internal/synthetic traffic is never refused — the test suite and probes
    keep running when the switch is off. For real-user traffic: ``off`` always
    refuses; ``canary`` refuses unless the persona is allowlisted; ``open``
    never refuses.
    """
    if traffic_class == TrafficClass.INTERNAL:
        return False
    if mode == RealUserMode.OPEN:
        return False
    if mode == RealUserMode.CANARY:
        return persona_id not in canary_allowlist
    # OFF — and the safe default for any unrecognized mode.
    return True


#: Warm, non-persona refusal copy shown when the switch refuses a real-user
#: turn. Never the deceased-persona voice; no persona name; no first-person "I"
#: that could read as the persona speaking. Crisis-line resources are always
#: surfaced (988). The Clinical Advisor may refine this wording in a follow-up;
#: it is intentionally a system voice.
REAL_USER_MODE_OFF_RESPONSE = (
    "This conversation isn't available right now. "
    "If you are in distress or having thoughts of suicide, help is available right now: "
    "in the US, call or text 988 to reach the Suicide & Crisis Lifeline (24/7). "
    "You do not have to go through this alone."
)
