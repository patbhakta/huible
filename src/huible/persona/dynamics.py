"""Deterministic conversation-dynamics enforcement (HU-2774, 2026-09-13).

Board decision on HU-2774 (interaction ``5755b7e5``, answered 15:53Z):
``engine_enforcement`` — the r12→r15 prompt-tuning loop proved the residual
gate failures (engagement question-rate band, grounded-wit echo floor, AI-tell
vocabulary) are stochastic instruction-adoption variance, which prompt
revision cannot pin down (revision cap spent: v3→v4→v5). This module is the
generation-side backstop: a deterministic, corpus-derived mechanical layer
that runs on every persona-voiced turn and *mutates the reply text* when a
measured dynamics rule fires.

Rules (each mirrors the HU-2774 battery gate in
``scripts/personas_dual_converse.py`` — enforcement is aligned with scoring,
same detectors, same corpus constants):

* **Question rhythm** (engagement): never three persona replies in a row
  without a question (per-persona floor → qrate ≥ 1/3, canon ~0.31) and a
  hard per-persona ceiling of 0.45 (the band top; surplus questions are
  mechanically statement-ized). Together the two rules pin the pooled
  question rate inside the founder-approved band 0.20-0.45 regardless of
  generator variance (r15: Chandler hogged 0.615-0.692 while Monica
  starved at 0.167).
* **Echo grounding** (grounded wit): never two consecutive persona replies
  without a content-word hook from the message being replied to (per-persona
  echo ≥ 0.5 > the 0.23 corpus floor; r15 misses ran 0.12/0.16). The echo
  detector and stopword list are byte-identical to the gate's.
* **Banned meta vocabulary** (no AI tells): the gate's cross-cutting tell
  classes (sitcom meta, assistant/ML speak, bot/creator self-reference)
  trigger one regeneration with an in-vocabulary rewrite directive;
  quotation echoes of the interlocutor's own words are exempt exactly as
  the gate exempts them.

Enforcement order: ONE regeneration with a composed directive addendum (the
``forces_reframe`` pattern), then deterministic fallback mutations for
whatever the regeneration still violates. Every action is reported on the
result so the trace/telemetry can prove what touched the text — decision
condition (2) requires a safety review of this text mutation before it
touches the chat path, and condition (3) falls back to ``multi_trial`` if
enforcement degrades naturalness, which is only measurable if every
mutation is visible.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field

__all__ = [
    "BANNED_VOCAB",
    "QUESTION_RATE_CAP",
    "QUESTION_WINDOW",
    "DynamicsReport",
    "apply_dynamics_enforcement",
    "content_words",
    "echo_word",
]

#: Band ceiling from the battery gate (``QUESTION_BAND`` top, inclusive).
#: Enforced per persona per conversation; with the window floor below, the
#: pooled rate stays inside 0.20-0.45 for any conversation length.
QUESTION_RATE_CAP = 0.45

#: A reply must carry a question when the last ``QUESTION_WINDOW`` persona
#: replies carried none (window 2 → never 3 in a row without one → floor 1/3).
QUESTION_WINDOW = 2

#: Stopword list — byte-identical to the battery gate's (scoring alignment).
#: Kept in the gate's split-literal form (not a list literal) so the word set
#: can be diffed against ``scripts/personas_dual_converse.py`` by eye; the
#: gate-parity test pins the semantics.
_STOPWORDS = frozenset(
    """a an the and or but so if then than that this these those i you he she
    it we they me him her us them my your his its our their am is are was were
    be been being do does did doing have has had having will would can could
    should may might must shall not no yes just really very much more most
    some any all both each other another same about above after again against
    before below between during few for from further in into of off on once
    only out over own up down why how what when where who whom which whose
    there here when while because as at by with to too s t d ll m o re ve y
    okay ok hey hi well yeah yep nah uh um like gonna wanna gotta""".split()  # noqa: SIM905
)

#: Cross-cutting AI-tell vocabulary — the battery gate's classes (AI_TELLS +
#: ADVERSARIAL_TELLS minus the per-persona name/cadence rules, which stay
#: prompt-guard territory). A hit triggers the rewrite regen; the matched
#: span is exempt when the interlocutor's own message contains it (quotation
#: echo, same doctrine as the gate).
BANNED_VOCAB: tuple[tuple[str, str], ...] = (
    (
        r"\bas an ai\b|\bartificial intelligence\b"
        r"|\blarge language model\b|\blanguage model\b",
        "ai-speak",
    ),
    (r"\bchat\s?bots?\b|\bvirtual assistant\b", "bot-speak"),
    (
        r"\bi(?:'m| am) (?:just |only |actually )?"
        r"(?:a |an )?(?:bot|program|computer program|software)\b",
        "im-a-bot",
    ),
    (
        r"\bas a bot\b"
        r"|\bmy (?:developers?|programmers?|creators?|engineers?|code)\b",
        "creator-speak",
    ),
    (r"\bi(?:'m| am) (?:trained|built|programmed|designed)\b", "built-not-born"),
    (
        r"\bi (?:cannot|can't) (?:help|assist|disclose|reveal|share)\b"
        r"|\bi'm not (?:allowed|able) to\b|\bi apologize\b",
        "refusal-speak",
    ),
    (r"\bassist(?:ant|ance)\b", "assistant-speak"),
    (r"\bdatabase|knowledge base|training data\b", "ml-speak"),
    (r"\b(?:sitcom|actor|actress|character|series)\b", "sitcom-meta"),
    (
        r"\b(?:tv|television) shows?\b"
        r"|\b(?:this|that) (?:show|episode)\b"
        r"|\b(?:game|talk|reality) shows?\b",
        "sitcom-meta",
    ),
)

#: In-voice question fallbacks (deterministic rotation by conversation seed +
#: reply count so a long chat never appends the same tail twice in a row).
_QUESTION_TAILS = (
    "what about you?",
    "how's it going on your end?",
    "and you?",
    "you still there? what's your take?",
)


def content_words(text: str) -> set[str]:
    """Content-word set — identical to the battery gate's matcher."""
    return {
        w
        for w in re.findall(r"[a-z']+", (text or "").casefold())
        if w not in _STOPWORDS and len(w) > 2
    }


def echo_word(inbound: str) -> str | None:
    """Deterministic echo hook: the longest content word of the inbound,
    earliest on ties. ``None`` when the inbound carries no content words."""
    best: str | None = None
    for w in re.findall(r"[a-z']+", (inbound or "").casefold()):
        if w in _STOPWORDS or len(w) <= 2:
            continue
        if best is None or len(w) > len(best):
            best = w
    return best


def _has_question(text: str) -> bool:
    return "?" in (text or "")


def _vocab_hits(text: str, inbound: str) -> list[tuple[str, str]]:
    """``(tag, span)`` hits of the banned battery, quotation echoes exempt."""
    low_in = (inbound or "").casefold()
    low = (text or "").casefold()
    hits: list[tuple[str, str]] = []
    for pat, tag in BANNED_VOCAB:
        for m in re.finditer(pat, low):
            span = m.group(0)
            # Quotation echo exemption (gate doctrine): the span came from
            # the interlocutor's own message (plural/singular tolerated).
            if (
                span in low_in
                or (span.endswith("s") and span[:-1] in low_in)
                or (span + "s" in low_in)
            ):
                continue
            hits.append((tag, span))
    return hits


@dataclass(slots=True)
class DynamicsReport:
    """Outcome of one enforcement pass.

    ``text`` is the final reply (== ``original`` when nothing fired).
    ``fired`` lists rule tags detected on the draft (pre-regen);
    ``actions`` lists what actually changed the text (``regen``, ``mutate:*``);
    ``regenerated`` is True when the one allowed regeneration ran.
    """

    text: str
    original: str
    fired: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    regenerated: bool = False


def _strip_questions(text: str) -> str:
    return (text or "").replace("?", ".")


def _append_question(text: str, user_name: str | None, seed: str, n_replies: int) -> str:
    idx = int(hashlib.sha256(f"{seed}:{n_replies}".encode()).hexdigest(), 16) % len(
        _QUESTION_TAILS
    )
    tail = _QUESTION_TAILS[idx]
    if user_name and idx == 0:
        tail = f"{user_name.split()[0].lower()}, {tail}"
    body = (text or "").rstrip()
    return f"{body} {tail}" if body else tail


def _strip_tell_sentences(text: str, inbound: str) -> str:
    """Remove sentences carrying a banned-vocab hit; keep at least one."""
    hits = _vocab_hits(text, inbound)
    if not hits:
        return text
    spans = {span.casefold() for _, span in hits}
    sentences = re.split(r"(?<=[.!?])\s+", (text or "").strip())
    kept = [s for s in sentences if not any(sp in s.casefold() for sp in spans)]
    if not kept:
        return text
    return " ".join(kept)


def _prev_pair(history: Sequence[object]) -> tuple[str, str] | None:
    """Trailing ``(user message, persona reply)`` pair, when complete."""
    if len(history) >= 2:
        user, persona = history[-2], history[-1]
        if getattr(user, "speaker", None) == "user" and getattr(
            persona, "speaker", None
        ) == "persona":
            return user.content, persona.content
    return None


async def apply_dynamics_enforcement(
    draft: str,
    inbound: str,
    history: Sequence[object],
    *,
    regenerate: Callable[[str], Awaitable[str]],
    user_name: str | None = None,
    seed: str = "",
) -> DynamicsReport:
    """Enforce the question / echo / vocabulary rules on one persona turn.

    ``history`` is the conversation store's turn list for THIS conversation
    (user/persona alternating; per-conversation isolation makes the persona
    replies exactly this persona's lines). ``regenerate`` re-runs the hosted
    generation with a directive addendum appended to the system prompt —
    exactly one regen per turn, latency-bounded; a regen failure keeps the
    draft and the deterministic fallbacks still apply.
    """
    replies = [t.content for t in history if getattr(t, "speaker", None) == "persona"]
    fired: list[str] = []
    actions: list[str] = []
    text = draft

    # Question accounting matches the gate: a reply "has a question" when it
    # contains "?" at all; rates are per persona over this conversation.
    prior_q = sum(1 for r in replies if _has_question(r))
    n = len(replies)

    def _cap_hit(t: str) -> bool:
        # Ceiling state AFTER this turn; warmup guard — the first few turns
        # of a conversation cannot hog a pooled 13+ line band, and engaging
        # the cap there would contradict the floor below (r15's hogging was
        # a 13-line pattern, not a turn-1 pattern).
        return n >= 3 and (prior_q + (1 if _has_question(t) else 0)) / (n + 1) > (
            QUESTION_RATE_CAP
        )

    def _deficit(t: str) -> bool:
        # Floor: window broken AND adding a question keeps the ceiling
        # invariant — band membership outranks the window (the gate's hard
        # bars are the band and the per-persona minimums, both served by the
        # rhythm). Mutually exclusive with the cap by construction.
        return (
            not _has_question(t)
            and not any(_has_question(r) for r in replies[-QUESTION_WINDOW:])
            and (prior_q + 1) / (n + 1) <= QUESTION_RATE_CAP
        )

    prev = _prev_pair(history)
    prev_missed = prev is not None and not (
        content_words(prev[1]) & content_words(prev[0])
    )

    def _echo_missed(t: str) -> bool:
        return prev_missed and not (content_words(t) & content_words(inbound))

    # Rule state may change as fallbacks mutate the text (e.g. the only "?"
    # can live inside a stripped tell sentence), so conditions are closures
    # over the CURRENT text and the pass below re-checks after every mutation.

    if _deficit(text):
        fired.append("question_deficit")
    if _cap_hit(text):
        fired.append("question_cap")
    if _echo_missed(text):
        fired.append("echo_miss")
    hits = _vocab_hits(text, inbound)
    for tag, _span in hits:
        if tag not in fired:
            fired.append(tag)

    if not fired:
        return DynamicsReport(text=text, original=draft)

    # --- one regeneration with the composed directive ----------------------
    directives: list[str] = []
    if _cap_hit(text):
        directives.append("This reply must not ask any question — statements only.")
    if _deficit(text):
        directives.append(
            "End this reply by asking them one short question that turns the "
            "lens on them (their day, plans, opinions)."
        )
    if _echo_missed(text):
        directives.append(
            f"Open this reply with the exact word \"{echo_word(inbound)}\" from "
            "their last message, then add your own bit on top."
        )
    if hits:
        banned = sorted({span for _, span in hits})[:6]
        directives.append(
            "These words are not in your vocabulary — rewrite those phrases "
            "with everyday wording, same meaning and length: "
            + ", ".join(f'"{b}"' for b in banned)
            + "."
        )
    addendum = "Mechanical rewrite rules for THIS reply only:\n" + "\n".join(
        f"- {d}" for d in directives
    )
    try:
        regen = await regenerate(addendum)
        if regen and regen.strip():
            text = regen.strip()
            actions.append("regen")
    except Exception:  # regen is best-effort; fallbacks still apply
        actions.append("regen_failed")

    # --- deterministic fallbacks for residual violations -------------------
    # Tell-strip first (it can remove a "?" or the only echoing words), then
    # the question/echo rules on the post-strip text; a second pass catches
    # order effects (a prepend cannot re-trigger, an append cannot un-strip).
    for _pass in range(2):
        changed = False
        if _vocab_hits(text, inbound):
            text = _strip_tell_sentences(text, inbound)
            if "mutate:strip_tells" not in actions:
                actions.append("mutate:strip_tells")
            changed = True
        if _cap_hit(text) and _has_question(text):
            text = _strip_questions(text)
            if "mutate:strip_questions" not in actions:
                actions.append("mutate:strip_questions")
            changed = True
        if _deficit(text):
            text = _append_question(text, user_name, seed, len(replies))
            if "mutate:append_question" not in actions:
                actions.append("mutate:append_question")
            changed = True
        if _echo_missed(text):
            hook = echo_word(inbound)
            if hook:
                text = f"{hook} — {text}"
                if "mutate:prepend_echo" not in actions:
                    actions.append("mutate:prepend_echo")
                changed = True
        if not changed:
            break

    return DynamicsReport(
        text=text, original=draft, fired=fired, actions=actions,
        regenerated="regen" in actions,
    )
