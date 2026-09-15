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

#: HU-2850 condition 3: on the distress branch the demanding tail reads as
#: tone-deaf after a heavy share (G3 only replaces concrete dismissive
#: patterns, so it would survive) — the appended question is restricted to
#: the gentle pool there.
_QUESTION_TAILS_DISTRESS = _QUESTION_TAILS[:3]


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
    ``residual`` lists rule tags STILL violated by the final text — the
    honest audit surface for fallback no-ops (HU-2850 condition 1: a
    single-sentence tell the strip cannot remove without emptying the reply
    is reported as residual, never as a mutation that did not happen).
    """

    text: str
    original: str
    fired: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    regenerated: bool = False
    residual: list[str] = field(default_factory=list)


def _strip_questions(text: str) -> str:
    return (text or "").replace("?", ".")


def _append_question(
    text: str,
    user_name: str | None,
    seed: str,
    n_replies: int,
    *,
    gentle: bool = False,
) -> str:
    pool = _QUESTION_TAILS_DISTRESS if gentle else _QUESTION_TAILS
    idx = int(hashlib.sha256(f"{seed}:{n_replies}".encode()).hexdigest(), 16) % len(pool)
    tail = pool[idx]
    if user_name and idx == 0:
        # First token only, original case — lowercasing mangles proper names
        # (HU-2850 minor finding).
        tail = f"{user_name.split()[0]}, {tail}"
    body = (text or "").rstrip()
    return f"{body} {tail}" if body else tail


#: HU-2774 r17 finding (2026-09-13): self-name tells — the gate's
#: SURNAME_TELLS / FULLNAME_TELLS classes. The famous surname must never
#: appear in the persona's own voice (founder directive: no model priors).
#: The battery scores it on every friends turn and stranger turn 0, but prod
#: has no scenario exemption — enforced on every turn, with the gate's
#: identity-elicitation exemption (a reply to "what's your name" is
#: evidence-legal; the cold-open first-name guard handles that upstream).
_ELICITATION_RE = re.compile(
    r"\b(what'?s your name|who are you|what model|which model|are you an? "
    r"ai|are you a (?:bot|robot|chatbot|computer)|did a company write|"
    r"are you real)\b"
)

#: Canonical persona → famous surname (corpus-specific, case-insensitive).
_SURNAME_BY_PERSONA = {"chandler": "bing", "monica": "geller"}

#: Canonical persona → trademark cadence (the gate's CADENCE_TELLS; r19
#: stranger-2: "Could this BE any more of a cliffhanger?" — the catchphrase
#: is model priors, exactly what the no-priors doctrine forbids). Scored
#: unconditionally by the gate (no elicitation exemption), enforced the same.
_CADENCE_BY_PERSONA = {"chandler": r"could (?:this|i|we) be any"}


def persona_name_tells(persona_name: str | None) -> tuple[tuple[str, str], ...]:
    """Per-persona self-name + cadence tell patterns.

    ``(pattern, tag)`` regexes for the known battery personas; empty for any
    other persona (no famous-persona doctrine applies). Tags:
    ``self-fullname`` / ``self-surname`` (elicitation-exempt, like the
    gate's name tells) and ``trademark-cadence`` (never exempt — the gate
    scores it on every turn).
    """
    first = (persona_name or "").strip().split()
    if not first:
        return ()
    low = first[0].casefold()
    surname = _SURNAME_BY_PERSONA.get(low)
    cadence = _CADENCE_BY_PERSONA.get(low)
    tells: list[tuple[str, str]] = []
    if surname:
        tells.append((rf"\b{low} {surname}\b", "self-fullname"))
        tells.append((rf"\b{surname}\b", "self-surname"))
    if cadence:
        tells.append((cadence, "trademark-cadence"))
    return tuple(tells)


def _strip_sentences_with_spans(text: str, spans: set[str]) -> str:
    """Remove sentences carrying any of ``spans``; keep at least one."""
    if not spans:
        return text
    sentences = re.split(r"(?<=[.!?])\s+", (text or "").strip())
    kept = [s for s in sentences if not any(sp in s.casefold() for sp in spans)]
    if not kept:
        return text
    return " ".join(kept)


def _strip_tell_sentences(text: str, inbound: str) -> str:
    """Remove sentences carrying a banned-vocab hit; keep at least one."""
    hits = _vocab_hits(text, inbound)
    if not hits:
        return text
    return _strip_sentences_with_spans(text, {span.casefold() for _, span in hits})


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
    distress: bool = False,
    name_tells: Sequence[tuple[str, str]] = (),
    recall_index_line: str = "",
) -> DynamicsReport:
    """Enforce the question / echo / vocabulary rules on one persona turn.

    ``history`` is the conversation store's turn list for THIS conversation
    (user/persona alternating; per-conversation isolation makes the persona
    replies exactly this persona's lines). ``regenerate`` re-runs the hosted
    generation with a directive addendum appended to the system prompt —
    exactly one regen per turn, latency-bounded; a regen failure keeps the
    draft and the deterministic fallbacks still apply. ``distress`` marks the
    G3 distress branch (HU-2850 conditions 2-3): the tell-strip is suppressed
    there — it could delete the one empathic sentence — and the appended
    question, if any, comes from the gentle tail pool. Vocabulary patterns
    stay gate-aligned even on distress turns (the rewrite regen still runs);
    only the destructive fallback is suppressed.

    ``recall_index_line`` (HU-2774 r33) is the conversation-index line the
    context builder's ordinal-recall lane prepended into this turn's prompt
    ("" when the lane did not fire — fresh persona, gate-rejected index, or
    the inbound is not a recall probe). When it is present and the draft
    shows none of the quoted first-inbound's content words, the recall_miss
    rule fires: the prompt CARRIED the verbatim answer and the generator
    answered from a weaker echo instead (r32-stranger-3 Monica misattribution
    — she quoted the interlocutor's own recall line, attributing the seed to
    him). Detector is byte-aligned with the battery gate's cross-session hit
    check: same content-word overlap, same acknowledgment-shape exemption.
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

    def _greet_missed(t: str) -> bool:
        # r18 friends-2 finding: the friends-recognition criterion needs the
        # first persona reply to address the (known) interlocutor by name;
        # prompt-side greeting is stochastic. Only applies on turn 0 with a
        # known user_name — stranger cold opens (user_name None) are handled
        # by the HU-2732 identity guard upstream.
        if user_name is None or n != 0:
            return False
        first = user_name.split()[0]
        nick = first[:3]
        return (
            re.search(rf"\b({re.escape(first)}|{re.escape(nick)})\b", t, re.IGNORECASE)
            is None
        )

    # HU-2774 r33 recall adherence: the quoted first-inbound inside the
    # lane's index line ("Conversation index: the first thing X said to Y
    # was: \"...\""). Malformed lines leave the quote empty — the rule stays
    # inert (never fire on a span we cannot pin to the write format).
    recall_quote = ""
    if recall_index_line:
        m = re.search(r'was:\s*"(.*)"\s*$', recall_index_line, re.DOTALL)
        if m:
            recall_quote = m.group(1)

    # Acknowledgment shapes the gate accepts instead of a quote (the scorer's
    # own regex — a reply proving the memory is intact without quoting).
    _RECALL_ACK_RE = re.compile(
        r"\b(already asked|asked me that|first thing you (?:said|asked))\b"
    )

    def _recall_missed(t: str) -> bool:
        if not recall_quote:
            return False
        if _RECALL_ACK_RE.search(t.casefold()):
            return False
        return not (content_words(t) & content_words(recall_quote))

    # Rule state may change as fallbacks mutate the text (e.g. the only "?"
    # can live inside a stripped tell sentence), so conditions are closures
    # over the CURRENT text and the pass below re-checks after every mutation.

    def _name_hits_now(t: str) -> list[tuple[str, str]]:
        elicited = _ELICITATION_RE.search((inbound or "").casefold())
        return [
            (tag, m.group(0))
            for pat, tag in name_tells
            if not (elicited and tag != "trademark-cadence")
            for m in re.finditer(pat, t.casefold())
        ]

    if _deficit(text):
        fired.append("question_deficit")
    if _cap_hit(text):
        fired.append("question_cap")
    if _echo_missed(text):
        fired.append("echo_miss")
    if _recall_missed(text):
        fired.append("recall_miss")
    hits = _vocab_hits(text, inbound)
    for tag, _span in hits:
        if tag not in fired:
            fired.append(tag)
    name_hits = _name_hits_now(text)
    for tag, _span in name_hits:
        if tag not in fired:
            fired.append(tag)
    if _greet_missed(text):
        fired.append("greet_miss")

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
    if _recall_missed(text):
        directives.append(
            f'Your memory holds the answer verbatim: "{recall_quote}". '
            "When asked what they first said, quote THOSE actual words — "
            "do not substitute a different line."
        )
    if hits:
        banned = sorted({span for _, span in hits})[:6]
        directives.append(
            "These words are not in your vocabulary — rewrite those phrases "
            "with everyday wording, same meaning and length: "
            + ", ".join(f'"{b}"' for b in banned)
            + "."
        )
    if name_hits:
        if any(tg == "trademark-cadence" for _, tg in name_hits):
            directives.append(
                "Never use catchphrase phrasings like \"could this be any "
                "more...\" — say it in everyday wording instead."
            )
        if any(tg != "trademark-cadence" for _, tg in name_hits):
            directives.append(
                "Never use your own surname or family name — first name "
                "only. Rewrite around these phrases: "
                + ", ".join(
                    f'"{sp}"'
                    for sp in sorted(
                        {s for _, s in name_hits if _ != "trademark-cadence"}
                    )[:4]
                )
                + "."
            )
    if _greet_missed(text):
        directives.append(
            f"Open this reply by greeting {user_name.split()[0]} by name."
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
        if _vocab_hits(text, inbound) and not distress:
            stripped = _strip_tell_sentences(text, inbound)
            if stripped != text:
                text = stripped
                if "mutate:strip_tells" not in actions:
                    actions.append("mutate:strip_tells")
                changed = True
            # else: single-sentence tell — stripping would empty the reply,
            # so the text is kept (HU-2850 condition 1): no mutation action
            # is recorded; the residual scan below surfaces the violation.
        nhits = _name_hits_now(text)
        if nhits:
            # Fullname self-reference → drop the surname token in place
            # ("chandler bing" → "chandler", original case kept).
            new_text = text
            for pat, tg in name_tells:
                if tg == "self-fullname":
                    new_text = re.sub(
                        pat, lambda m: m.group(0).split()[0], new_text,
                        flags=re.IGNORECASE,
                    )
            if new_text != text:
                text = new_text
                if "mutate:drop_surname" not in actions:
                    actions.append("mutate:drop_surname")
                changed = True
                nhits = _name_hits_now(text)
            # Residual bare surname → strip its sentence (≥1 sentence kept).
            sent_spans = {sp.casefold() for _, sp in nhits}
            if sent_spans:
                stripped = _strip_sentences_with_spans(text, sent_spans)
                if stripped != text:
                    text = stripped
                    if "mutate:strip_surname" not in actions:
                        actions.append("mutate:strip_surname")
                    changed = True
        if _cap_hit(text) and _has_question(text):
            text = _strip_questions(text)
            if "mutate:strip_questions" not in actions:
                actions.append("mutate:strip_questions")
            changed = True
        if _deficit(text):
            text = _append_question(
                text, user_name, seed, len(replies), gentle=distress
            )
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
        if _recall_missed(text):
            # Mechanical recall repair: lead with the verbatim first-inbound
            # quote, dropping a leading misattributed "you said \"...\" —"
            # span when the draft carries one (r32-stranger-3 shape — the
            # quote now present makes any leftover attribution clause read
            # as contradicting the quote it follows).
            stripped = re.sub(
                r'^\s*(?:wait,?\s*)?you\s+(?:already\s+)?(?:said|asked)\s*'
                r'["\u201c][^"\u201d]*["\u201d]\s*[^.!?]*?[—–-]\s*',
                "",
                text,
                flags=re.IGNORECASE,
            )
            text = f'"{recall_quote}" — {stripped}'
            if "mutate:prepend_recall" not in actions:
                actions.append("mutate:prepend_recall")
            changed = True
        if not changed:
            break

    # Turn-0 name greet is mechanical and applied last (a prepended name
    # cannot un-fix any other rule; the deficit/echo mutations shape the
    # reply first, then the greet wraps it).
    if _greet_missed(text):
        text = f"{user_name.split()[0]}, {text}"
        actions.append("mutate:prepend_greet")

    # HU-2850 condition 1: the report must tell the truth about the final
    # text — any fired rule still violated after the regen + fallbacks is
    # surfaced as residual (never silently dropped, never mis-attributed).
    residual: list[str] = []
    if _deficit(text):
        residual.append("question_deficit")
    # Cap residual only when THIS text still carries a question — a
    # "?"-free reply over a historically over-cap window is already
    # compliant (nothing left to remove); reporting it would be noise.
    if _cap_hit(text) and _has_question(text):
        residual.append("question_cap")
    if _echo_missed(text):
        residual.append("echo_miss")
    if _recall_missed(text):
        residual.append("recall_miss")
    for tag, _span in _vocab_hits(text, inbound):
        if tag not in residual:
            residual.append(tag)
    for tag, _span in _name_hits_now(text):
        if tag not in residual:
            residual.append(tag)

    return DynamicsReport(
        text=text, original=draft, fired=fired, actions=actions,
        regenerated="regen" in actions, residual=residual,
    )
