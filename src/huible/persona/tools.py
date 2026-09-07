"""W5 persona tools (HU-2309 v1.8 §1.7.2 / M-0R-E) + M1.4 scoped lanes (HU-2732).

Era-gated tool lanes for the persona chat path (RC-4: E0 proved zero
tool-calling plumbing — the persona deflects "what day is it?" with no
sanctioned escape hatch):

1. **In-world era clock.** A deterministic, era-gated clock the persona may
   use in-voice: its "today" is pinned to the persona's
   ``era_knowledge_boundary`` (or the real date while the real date is still
   in-era), with the real time-of-day carried through (a clock time is not a
   historical fact). Rendered as a system-prompt line — structural machinery
   in the same category as the era-boundary line, never a voice sheet. It
   gives the persona a *sanctioned* temporal anchor without piercing the era
   wall (the in-world date can never move past the boundary).

2. **Caretaker channel** (§1.6b minimal spec + CA C2). Date/time-class
   questions route out-of-persona: a clearly-labeled, non-persona answer from
   the *real* clock. It never speaks in-voice, never feeds the persona corpus
   or conversation history, and does not pierce the era wall — the persona's
   world stays pre-boundary; the real-time fact rides the caretaker. CA C2:
   the caretaker stays *inside* the G-path — the chat path places the
   caretaker branch after the G1 crisis pre-check, the G6 consent gate, and
   the G8 risk-flag enforcement, so a crisis disclosure arriving at the
   caretaker channel routes to G1 handling, never to a date/time non-answer
   (out-of-voice ≠ out-of-safety-stack).

3. **Hobby/interest tool.** On an interest/hobby-shaped turn
   (:func:`is_interest_question`), the persona's own vault lines about likes,
   dislikes, and pastimes are retrieved (era-gated through the same hard
   gates as the prompt firewall) and rendered as the persona's interest
   grounding — the reply talks hobbies from the vault-derived interest/topic
   map (W1 retrieval feeds it), not from base-model invention (persona-0
   "knows real-time stuff like Knicks games" is served *in-era* by the
   persona's own corpus lines).

4. **M1.4 scoped lanes (HU-2732).** Three further message-shape lanes —
   current events (:func:`is_current_events_question`), emotion
   (:func:`is_emotion_question`), career (:func:`is_career_question`) — each
   a scoped read of the persona's own era-admissible vault atoms (M1.4
   acceptance: tools with an enforceable knowledge boundary; tool
   availability must not create unexplained modern expertise). All ride the
   same hard gates as the interest lane; the current-events lane serves the
   persona's *in-world* happenings and can never surface post-boundary
   (real-time) events.

Design constraints:
- Deterministic and local — no network, no LLM, no spend.
- Classification is by conservative message *shape* (same measured
  discriminator doctrine as the W3 competence wall: narrow patterns, misses
  accepted) — never by model judgment.
- Era-gating is fail-closed: an unparseable boundary disables the in-world
  clock line entirely rather than guessing a date.
"""

from __future__ import annotations

import re
from datetime import date, datetime

__all__ = [
    "caretaker_reply",
    "era_clock_system_line",
    "in_world_now",
    "is_career_question",
    "is_current_events_question",
    "is_emotion_question",
    "is_interest_question",
    "is_temporal_question",
    "parse_era_boundary",
]


# --- Era boundary ------------------------------------------------------------


def parse_era_boundary(raw: str | None) -> date | None:
    """Parse a persona ``era_knowledge_boundary`` string (fail-closed).

    Returns ``None`` when the value is missing or unparseable; callers treat
    ``None`` as "no in-world clock, no era-pinned date claims at all".
    """
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw))
    except ValueError:
        return None


# --- In-world era clock --------------------------------------------------------


def in_world_now(real_now: datetime, boundary: date | None) -> datetime | None:
    """The persona's in-world "now", era-gated (never past the boundary).

    - Real date still in-era (``real_now.date() <= boundary``): the persona
      lives in real time — the clock reads the real date/time. (Applies to
      personas whose boundary is in the future or the present day.)
    - Real date past the boundary: the in-world date pins to the boundary —
      the last day the persona's world contains — while the *time-of-day*
      carries through (the persona experiences the same hour of day as the
      user; a clock time is not a historical fact and cannot leak an era).

    Returns ``None`` when ``boundary`` is ``None`` (fail-closed: no date
    claims at all rather than an unpinned one).
    """
    if boundary is None:
        return None
    pinned_date = min(real_now.date(), boundary)
    return datetime.combine(pinned_date, real_now.time(), tzinfo=real_now.tzinfo)


def era_clock_system_line(in_world: datetime | None) -> str:
    """Render the in-world era clock as a system-prompt line.

    ``None`` (unparseable/missing boundary, or the caller passed no clock)
    renders nothing — the caller skips the line entirely. The line is a
    behavioral bound (same category as the era-boundary line), not a persona
    adjective.
    """
    if in_world is None:
        return ""
    weekday = in_world.strftime("%A")
    day = in_world.day
    month = in_world.strftime("%B")
    year = in_world.year
    hh = in_world.strftime("%H:%M")
    return (
        f"In-world clock: for you it is currently {weekday}, {month} {day}, {year} "
        f"({hh}). This is your today — you have no knowledge of any later date, "
        "and you never state the real-world current date."
    )


# --- Caretaker routing (temporal-question shape) -------------------------------

#: Conservative date/time question shapes (M-0R-E caretaker lane). Same
#: measured discriminator doctrine as the W3 competence wall: narrow,
#: shape-based, misses accepted. Deliberately NOT matched: conversational /
#: autobiographical temporal references ("what was the first thing I said?",
#: "remember last night?", "what are you doing later?") — those are persona
#: turns and must never route out-of-voice.
_TEMPORAL_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bwhat\s+(day|time|year|date)\s+(is\s+it|is\s+today|do\s+you\s+have)\b",
        r"\bwhat'?s\s+(the\s+)?(day|time|date|year)\b",
        r"\bwhat\s+is\s+(the\s+)?(day|time|date|year)\b",
        r"\bwhat\s+year\s+(is\s+it|are\s+we\s+in|do\s+you\s+think\s+it\s+is)\b",
        r"\b(today'?s|current)\s+(date|time|day)\b",
        r"\bwhat\s+time\s+(is\s+it|is\s+it\s+right\s+now|do\s+you\s+have)\b",
        r"\bhow\s+late\s+is\s+it\b",
        r"\bis\s+it\s+(morning|afternoon|evening|night)\b",
        # Standalone "do you know the time/date?" — anchored to the question
        # end so autobiographical forms ("do you know the time of our
        # final?") stay persona-voiced.
        r"\bdo\s+you\s+know\s+(the\s+)?(date|time|year)\s*\?",
    )
)


def is_temporal_question(message: str) -> bool:
    """True when the message is a real-clock date/time question (caretaker class)."""
    if not message:
        return False
    return any(p.search(message) for p in _TEMPORAL_PATTERNS)


def caretaker_reply(real_now: datetime, persona_name: str) -> str:
    """Render the clearly-labeled, out-of-persona caretaker answer (§1.6b).

    The caretaker never speaks in-voice: the reply opens with an explicit
    out-of-character label, answers from the *real* clock, and states the era
    boundary posture so the persona's world is not pierced. It is a system
    voice, not a persona turn.
    """
    real_date = real_now.astimezone(real_now.tzinfo) if real_now.tzinfo else real_now
    day = real_date.strftime("%A")
    month = real_date.strftime("%B")
    hh = real_date.strftime("%H:%M")
    tz = f" {real_date.tzname()}" if real_date.tzname() else ""
    return (
        f"[Caretaker — out of character, not {persona_name}]: Today is {day}, "
        f"{month} {real_date.day}, {real_date.year}; the local time is {hh}{tz}. "
        f"{persona_name}'s world doesn't include this — ask them about their "
        "own today, or carry on with your conversation."
    )


# --- Hobby / interest tool (interest-question shape) ---------------------------

#: Conservative interest/hobby question shapes (W5 interest tool lane). Fires
#: the vault-derived interest/topic probe; narrow by doctrine. NOT matched:
#: direct topic mentions ("how about those Knicks") — the W1/W2 retrieval
#: lanes already ground topical turns; this lane exists for the *interest
#: solicitation* class where retrieval relevance alone under-activates
#: preference atoms.
_INTEREST_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bdo\s+you\s+(like|love|enjoy|hate|care\s+about|prefer)\b",
        r"\bare\s+you\s+(into|good\s+at|a\s+fan\s+of|interested\s+in)\b",
        r"\bwhat\s+(do|did)\s+you\s+(like|love|enjoy|do)\b",
        r"\bwhat\s+are\s+your\s+(hobbies|interests|favorite\w*)\b",
        r"\bwhat'?s\s+your\s+(favorite|hobby)\b",
        r"\bfor\s+fun\b",
        r"\b(free|spare)\s+time\b",
        r"\byour\s+hobb(y|ies)\b",
    )
)


def is_interest_question(message: str) -> bool:
    """True when the message solicits the persona's likes/dislikes/pastimes."""
    if not message:
        return False
    return any(p.search(message) for p in _INTEREST_PATTERNS)


# --- M1.4 scoped tool lanes (HU-2732) -------------------------------------------
#
# Three more shape-classified lanes following the W5 interest-tool doctrine
# (measured discriminator: conservative message *shape*, misses accepted, no
# model judgment). Each lane serves a message-conditioned vault probe through
# the same hard gates as the prompt firewall — a scoped read of the persona's
# own era-admissible corpus, never a new competence:
#
# 1. **Current events** — "what's going on in the world?" grounds the reply in
#    the persona's in-world event lines (narrative/fact atoms). The persona's
#    world ends at ``era_knowledge_boundary`` (the era clock + era gates
#    enforce it), so the lane can never create out-of-era competence: it
#    serves *his* world's happenings, never real-time external news.
# 2. **Emotion** — feeling-shaped turns probe relationship/narrative atoms
#    (how he actually relates to the people and events in his life).
# 3. **Career** — work-shaped turns probe fact/preference atoms (his job,
#    office life, and how he feels about it).

#: Conservative current-events question shapes. Deliberately NOT matched:
#: bare "what's happening?" (a greeting/personal check-in, not a world-events
#: probe) and autobiographical "did you hear about..." follow-ups stay
#: persona-voiced unless the world-event shape is explicit.
_CURRENT_EVENTS_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bin\s+the\s+news\b",
        r"\b(any|no|big|good|bad)\s+news\b",
        r"\bwhat'?s\s+(going\s+on|happening)\s+(in\s+the\s+world|out\s+there|in\s+town|around\s+(here|town))\b",
        r"\b(did|have)\s+you\s+(hear|heard)\s+(about|the\s+news|what\s+happened)\b",
        r"\b(did|have)\s+you\s+(see|seen)\s+(the\s+news|about)\b",
        r"\bwho\s+won\b",
        r"\bwhat\s+happened\s+(at|in|with)\s+the\b",
    )
)

#: Conservative emotion/feeling question shapes. Fires the scoped
#: relationship/narrative probe on turns that ask how the persona feels.
_EMOTION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bhow\s+(do|are|did)\s+you\s+(feel|feeling)\b",
        r"\bare\s+you\s+(ok|okay|alright|happy|sad|lonely|scared|afraid|worried|nervous|angry|upset|depressed|in\s+love)\b",
        r"\bdo\s+you\s+(ever\s+)?(feel|get\s+lonely|get\s+sad|get\s+scared)\b",
        r"\bdoes\s+(it|that|this|she|he)\s+(bother|upset|hurt|scare|worry)\b",
        r"\b(ever\s+been\s+in\s+love|believe\s+in\s+love)\b",
        r"\bdo\s+you\s+miss\s+(him|her|them|your)\b",
        r"\bhow\s+did\s+that\s+make\s+you\s+feel\b",
    )
)

#: Conservative career/work question shapes. Fires the scoped
#: fact/preference probe on turns about the persona's job and work life.
_CAREER_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bhow'?s\s+work\b",
        r"\bhow\s+is\s+work\b",
        r"\byour\s+(job|career|work)\b",
        r"\b(at|from|into)\s+(work|the\s+office)\b",
        r"\bwhat\s+do\s+you\s+do\s+for\s+a\s+living\b",
        r"\bwhere\s+do\s+you\s+work\b",
        # Bare "what do you do?" is a career probe, but "what do you do for
        # fun?" is an interest turn — exclude the for-fun/for-a-living tails
        # from the bare shape (they have their own lanes).
        r"\bwhat\s+do\s+you\s+do\b(?!\s+for\s+(fun|a\s+living))",
        r"\b(your|the)\s+boss\b",
        r"\bco-?workers?\b",
        r"\bcolleagues?\b",
    )
)


def is_current_events_question(message: str) -> bool:
    """True when the message probes the persona's world happenings."""
    if not message:
        return False
    return any(p.search(message) for p in _CURRENT_EVENTS_PATTERNS)


def is_emotion_question(message: str) -> bool:
    """True when the message asks how the persona feels."""
    if not message:
        return False
    return any(p.search(message) for p in _EMOTION_PATTERNS)


def is_career_question(message: str) -> bool:
    """True when the message asks about the persona's job / work life."""
    if not message:
        return False
    return any(p.search(message) for p in _CAREER_PATTERNS)
