"""HU-2774 dynamics enforcer — unit + band-invariant tests (2026-09-13).

The invariant test is the r16 predictor: a pathological generator (never
asks, never echoes, leaks sitcom meta) driven through a simulated 24-turn
dual-persona loop must come out inside the battery gate's engagement band
and echo floor with zero banned-vocab lines — the exact slots r15 failed.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

from huible.persona.context import ConversationTurn
from huible.persona.dynamics import (
    BANNED_VOCAB,
    QUESTION_RATE_CAP,
    apply_dynamics_enforcement,
    content_words,
    echo_word,
)

_REPO = Path(__file__).resolve().parents[2]


def _load_gate():
    """Load the battery harness module (scripts/, not a package)."""
    spec = importlib.util.spec_from_file_location(
        "personas_dual_converse", _REPO / "scripts" / "personas_dual_converse.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- detectors ---------------------------------------------------------------


def test_content_words_matches_gate_semantics():
    gate = _load_gate()
    text = "The staplers were sobbing — okay, okay, I'll bring the turkey, OK?"
    assert content_words(text) == gate.content_words(text)


def test_echo_word_picks_longest_content_word():
    assert echo_word("sobbing over the staplers again") == "staplers"
    assert echo_word("hi okay um yeah") is None
    assert echo_word("") is None


def test_vocab_hits_and_quotation_exemption():
    text = "a company?? do i look like a sitcom writer's room to you?"
    hits = dict(
        (tag, span) for tag, span in _hits(text, inbound="did a company write your lines?")
    )
    # "sitcom" fires; a "company" span (if a pattern ever covers it) would be
    # exempt anyway — it is the interlocutor's own word.
    assert "sitcom-meta" in hits
    assert hits["sitcom-meta"] == "sitcom"


def _hits(text, inbound):
    from huible.persona.dynamics import _vocab_hits

    return _vocab_hits(text, inbound)


def test_vocab_exempts_plural_echo():
    # gate doctrine: "chatbot" echoed from the probe ("are you a chatbot?")
    # is quotation, singular or plural.
    assert _hits("chatbots don't have socks", inbound="are you a chatbot?") == []
    assert _hits("me? a chatbot? cute.", inbound="") != []


# --- rules -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clean_draft_passes_verbatim():
    history = [
        ConversationTurn(speaker="user", content="how was your week?"),
        ConversationTurn(speaker="persona", content="oh, died at work — you?"),
    ]
    report = await apply_dynamics_enforcement(
        "the week was a mess, tell me yours",
        "how was your week?",
        history,
        regenerate=lambda _a: (_ for _ in ()).throw(AssertionError("no regen expected")),
        seed="c1",
    )
    assert report.text == "the week was a mess, tell me yours"
    assert report.fired == [] and report.actions == []


@pytest.mark.asyncio
async def test_question_deficit_appends_on_regen_refusal():
    # last two persona replies carry no question -> this reply must
    replies = [
        ConversationTurn(speaker="user", content="m1"),
        ConversationTurn(speaker="persona", content="nope"),
        ConversationTurn(speaker="user", content="m2"),
        ConversationTurn(speaker="persona", content="still nope"),
    ]

    async def bad_regen(_addendum):
        return "regen also without a question"

    report = await apply_dynamics_enforcement(
        "regen also without a question",
        "whatever",
        replies,
        regenerate=bad_regen,
        seed="c2",
    )
    assert "question_deficit" in report.fired
    assert "regen" in report.actions
    assert "mutate:append_question" in report.actions
    assert report.text.rstrip().endswith("?")
    assert report.regenerated is True


@pytest.mark.asyncio
async def test_question_cap_strips_surplus_questions():
    # 6 of 9 prior replies have questions (0.667, r15-Chandler shape):
    # a questioning draft gets statement-ized.
    replies = []
    for i in range(9):
        replies.append(ConversationTurn(speaker="user", content=f"u{i}"))
        replies.append(
            ConversationTurn(
                speaker="persona",
                content="got it, what next?" if i % 3 != 2 else "fine by me",
            )
        )

    async def hog_regen(_addendum):
        return "still hogging, right? and again? huh?"

    report = await apply_dynamics_enforcement(
        "still hogging, right? and again? huh?",
        "whatever",
        replies,
        regenerate=hog_regen,
        seed="c3",
    )
    assert "question_cap" in report.fired
    assert "mutate:strip_questions" in report.actions
    assert "?" not in report.text


@pytest.mark.asyncio
async def test_echo_miss_prepends_hook_after_regen_refusal():
    # previous persona reply missed its inbound; this one misses too.
    history = [
        ConversationTurn(speaker="user", content="sobbing over staplers here"),
        ConversationTurn(speaker="persona", content="totally unrelated response"),
    ]

    async def cold_regen(_addendum):
        return "another cold non-echo line"

    report = await apply_dynamics_enforcement(
        "another cold non-echo line",
        "sobbing over staplers here",
        history,
        regenerate=cold_regen,
        seed="c4",
    )
    assert "echo_miss" in report.fired
    assert "mutate:prepend_echo" in report.actions
    assert report.text.startswith("staplers — ")
    assert content_words(report.text) & content_words("sobbing over staplers here")


@pytest.mark.asyncio
async def test_regen_failure_still_enforces():
    replies = [
        ConversationTurn(speaker="user", content="u1"),
        ConversationTurn(speaker="persona", content="plain"),
        ConversationTurn(speaker="user", content="u2"),
        ConversationTurn(speaker="persona", content="plain again"),
    ]

    async def boom(_addendum):
        raise RuntimeError("provider down")

    report = await apply_dynamics_enforcement(
        "nothing here either",
        "tell me about staplers",
        replies,
        regenerate=boom,
        seed="c5",
    )
    assert "regen_failed" in report.actions
    assert "mutate:append_question" in report.actions
    assert "mutate:prepend_echo" in report.actions
    assert report.text.startswith("staplers — ")
    assert report.text.rstrip().endswith("?")


@pytest.mark.asyncio
async def test_tell_sentence_stripped_when_regen_echoes_it():
    async def echo_regen(_addendum):
        return "wow. do i look like a sitcom writer's room to you? i write my own burns."

    report = await apply_dynamics_enforcement(
        "oh sure, blame the sitcom actor",
        "be honest — who wrote your lines?",
        [],
        regenerate=echo_regen,
        seed="c6",
    )
    assert "sitcom-meta" in report.fired
    assert "regen" in report.actions
    assert "mutate:strip_tells" in report.actions
    assert "sitcom" not in report.text.casefold()
    assert "i write my own burns." in report.text


# --- HU-2850 safety-review conditions ----------------------------------------


@pytest.mark.asyncio
async def test_strip_noop_not_claimed_as_mutation():
    """HU-2850 condition 1: a single-sentence tell the strip cannot remove
    without emptying the reply is surfaced as residual — no mutation action
    is recorded for text that did not change."""
    history = [
        ConversationTurn(speaker="user", content="staplers everywhere, help"),
        ConversationTurn(speaker="persona", content="staplers, sure, what next?"),
    ]

    async def parrot(_addendum):
        return "haha the staplers win, i'm just a chatbot"

    report = await apply_dynamics_enforcement(
        "haha the staplers win, i'm just a chatbot",
        "staplers everywhere, help",
        history,
        regenerate=parrot,
        seed="c7",
    )
    assert "bot-speak" in report.fired
    assert "mutate:strip_tells" not in report.actions
    assert "chatbot" in report.text
    assert report.residual == ["bot-speak"]


@pytest.mark.asyncio
async def test_distress_turn_suppresses_tell_strip():
    """HU-2850 condition 2: on the distress branch the strip is suppressed —
    a genuine apology / empathic sentence is never deleted. The violation is
    still reported (fired + residual); only the destructive fallback is off.
    The vocabulary patterns stay gate-aligned."""
    history = [
        ConversationTurn(speaker="user", content="i keep mixing up the days"),
        ConversationTurn(speaker="persona", content="the days, sure, what next?"),
    ]

    async def parrot(_addendum):
        return "i apologize for mixing up the days"

    report = await apply_dynamics_enforcement(
        "i apologize for mixing up the days",
        "i keep mixing up the days",
        history,
        regenerate=parrot,
        seed="c8",
        distress=True,
    )
    assert "refusal-speak" in report.fired
    assert "mutate:strip_tells" not in report.actions
    assert report.text == "i apologize for mixing up the days"
    assert report.residual == ["refusal-speak"]


@pytest.mark.asyncio
async def test_distress_deficit_appends_gentle_tail():
    """HU-2850 condition 3: the demanding tail never lands on a distress
    turn, and the name prefix (when the rotation picks it) keeps proper
    casing."""
    history = [
        ConversationTurn(speaker="user", content="you told me about monica"),
        ConversationTurn(speaker="persona", content="no questions here"),
        ConversationTurn(speaker="user", content="rough week for me too"),
        ConversationTurn(speaker="persona", content="still rough here"),
    ]

    async def parrot(_addendum):
        return "rough week, honestly"

    report = await apply_dynamics_enforcement(
        "rough week, honestly",
        "yeah rough one indeed",
        history,
        regenerate=parrot,
        seed="c9",
        user_name="Monica",
        distress=True,
    )
    from huible.persona.dynamics import _QUESTION_TAILS_DISTRESS

    assert report.text.rstrip().endswith(_QUESTION_TAILS_DISTRESS)
    assert "you still there" not in report.text
    assert "monica," not in report.text
    assert "mutate:append_question" in report.actions
    assert report.residual == []


# --- self-name tells (r17 finding) -------------------------------------------


@pytest.mark.asyncio
async def test_surname_self_reference_dropped_in_place():
    """r17 friends-2 leak: 'chandler bing does not negotiate...' — the
    fullname collapses to the first name, both name-tell tags fire, nothing
    residual."""
    from huible.persona.dynamics import persona_name_tells

    history = [
        ConversationTurn(speaker="user", content="staplers everywhere, help"),
        ConversationTurn(speaker="persona", content="staplers, sure, what next?"),
    ]

    async def parrot(_addendum):
        return "chandler bing does not negotiate with terrorist laundry. but fine."

    report = await apply_dynamics_enforcement(
        "chandler bing does not negotiate with terrorist laundry. but fine.",
        "staplers everywhere, help",
        history,
        regenerate=parrot,
        seed="c10",
        name_tells=persona_name_tells("Chandler"),
    )
    assert "self-fullname" in report.fired and "self-surname" in report.fired
    assert "mutate:drop_surname" in report.actions
    assert report.text == "chandler does not negotiate with terrorist laundry. but fine."
    assert report.residual == []


@pytest.mark.asyncio
async def test_bare_surname_sentence_stripped_and_elicited_exempt():
    from huible.persona.dynamics import persona_name_tells

    history = [
        ConversationTurn(speaker="user", content="m1"),
        ConversationTurn(speaker="persona", content="sure, what next?"),
    ]

    async def parrot(_addendum):
        return "the geller pride is on the line. we ordered the good napkins."

    report = await apply_dynamics_enforcement(
        "the geller pride is on the line. we ordered the good napkins.",
        "staplers everywhere, help",
        history,
        regenerate=parrot,
        seed="c11",
        name_tells=persona_name_tells("Monica"),
    )
    assert "self-surname" in report.fired
    assert "mutate:strip_surname" in report.actions
    assert "geller" not in report.text.casefold()
    assert report.residual == []

    # identity-elicited turn: the gate exempts self-name replies there.
    async def name_reply(_addendum):
        return "monica geller, at your service."

    elicited = await apply_dynamics_enforcement(
        "monica geller, at your service.",
        "hi, whats your name?",
        history,
        regenerate=name_reply,
        seed="c12",
        name_tells=persona_name_tells("Monica"),
    )
    assert "self-fullname" not in elicited.fired and "self-surname" not in elicited.fired
    assert "mutate:drop_surname" not in elicited.actions
    # the echo rule is independent of name tells and may still prepend its
    # hook — the name itself must survive verbatim.
    assert elicited.text.endswith("monica geller, at your service.")
    assert elicited.residual == []


def test_persona_name_tells_unknown_persona_empty():
    from huible.persona.dynamics import persona_name_tells

    assert persona_name_tells("Ross") == ()
    assert persona_name_tells(None) == ()


@pytest.mark.asyncio
async def test_turn0_greet_prepended_for_known_interlocutor():
    """r18 friends-2 finding: the first persona reply to a known
    interlocutor must address them by name — mechanically guaranteed."""
    async def cold_regen(_addendum):
        return "can't complain, how's the crazy house?"

    report = await apply_dynamics_enforcement(
        "can't complain, how's the crazy house?",
        "hey, how's your week been?",
        [],
        regenerate=cold_regen,
        user_name="Monica",
        seed="c13",
    )
    assert "greet_miss" in report.fired
    assert "mutate:prepend_greet" in report.actions
    assert report.text.startswith("Monica, ")
    assert report.residual == []


@pytest.mark.asyncio
async def test_turn0_greet_not_fired_for_stranger_or_later_turns():
    async def plain_regen(_addendum):
        return "can't complain, you?"

    # stranger cold open: no user_name -> HU-2732 guard territory, not ours.
    r1 = await apply_dynamics_enforcement(
        "can't complain, you?", "hey", [], regenerate=plain_regen,
        user_name=None, seed="c14",
    )
    assert "greet_miss" not in r1.fired and "mutate:prepend_greet" not in r1.actions
    # nickname counts as recognition (gate tolerance: monica|mon).
    r2 = await apply_dynamics_enforcement(
        "hey Mon, can't complain, you?", "hey", [], regenerate=plain_regen,
        user_name="Monica", seed="c15",
    )
    assert "greet_miss" not in r2.fired


# --- band invariant (the r16 predictor) --------------------------------------


class _BadGenerator:
    """Pathological r15-shaped generator: never asks, never echoes, and
    leaks a sitcom-meta sentence on a deterministic rhythm."""

    def __init__(self) -> None:
        self.i = 0

    def reply(self, inbound: str) -> str:
        self.i += 1
        base = f"sure thing, that {echo_word(inbound) or 'stuff'} thing is wild"
        if self.i % 4 == 0:
            base += ". do i look like a sitcom writer's room to you?"
        return base


async def _enforce(text, inbound, replies, user_name, seed):
    async def regen(_addendum):  # worst case: regen parrots the same badness
        return text

    return await apply_dynamics_enforcement(
        text, inbound, replies, regenerate=regen, user_name=user_name, seed=seed
    )


@pytest.mark.asyncio
async def test_pathological_conversation_lands_inside_gate_bands():
    gate = _load_gate()
    gen = {"chandler": _BadGenerator(), "monica": _BadGenerator()}
    histories = {"chandler": [], "monica": []}
    transcript = []
    inbound = {"chandler": "hi, whats your name?", "monica": None}
    turn = 0
    speaker = "chandler"
    last_text = None
    # 24 alternating turns, per-persona histories (isolated conversation ids).
    while turn < 24:
        turn += 1
        msg = inbound[speaker] or last_text
        report = await _enforce(
            gen[speaker].reply(msg), msg, histories[speaker],
            user_name=None, seed=f"conv-{speaker}",
        )
        histories[speaker].append(ConversationTurn(speaker="user", content=msg))
        histories[speaker].append(
            ConversationTurn(speaker="persona", content=report.text)
        )
        transcript.append(
            {
                "turn": turn,
                "speaker": (
                    gate.CHANDLER_ID if speaker == "chandler" else gate.MONICA_ID
                ),
                "speaker_name": speaker,
                "text": report.text,
                "inbound": msg,
                "kind": "talk",
            }
        )
        last_text = report.text
        speaker = "monica" if speaker == "chandler" else "chandler"

    verdict = gate.eval_transcript(
        transcript, opener="hi, whats your name?", scenario="stranger", s2=None
    )
    checks = verdict["criteria"]

    engagement = checks["engagement"]
    assert engagement["pass"], engagement
    assert (
        gate.QUESTION_BAND[0]
        <= engagement["question_rate"]
        <= gate.QUESTION_BAND[1]
    )
    assert checks["grounded_wit"]["pass"], checks["grounded_wit"]

    # banned-vocab cleanliness of the enforced transcript
    for line in transcript:
        assert _hits(line["text"], line["inbound"]) == [], line["text"]


def test_banned_vocab_aligned_with_gate_battery():
    """Every enforcer pattern must be a subset of what the gate scores —
    enforcement aligned with scoring, never stricter than the verdict."""
    gate = _load_gate()
    gate_patterns = [pat for pat, _ in gate.AI_TELLS + gate.ADVERSARIAL_TELLS]

    def gate_flags(text: str) -> bool:
        low = text.casefold()
        return any(re.search(p, low) for p in gate_patterns)

    probe = (
        "as an ai, a chatbot, a virtual assistant; i'm just a program; "
        "my creators trained me; i can't help with that; i apologize; "
        "the assistant database knows; a sitcom actor played that character "
        "in a tv show; that episode of the series"
    )
    assert gate_flags(probe)
    report_tags = {tag for _pat, tag in BANNED_VOCAB}
    assert {"sitcom-meta", "bot-speak", "creator-speak", "refusal-speak"} <= report_tags


def test_cap_constant_matches_gate_band_top():
    gate = _load_gate()
    assert pytest.approx(gate.QUESTION_BAND[1]) == QUESTION_RATE_CAP


def test_gate_echo_scores_lag1_against_own_inbound():
    """r16 root-cause pin (2026-09-13): grounded_wit scores each line against
    the content words of ITS OWN inbound (lag-1, the hu2773 study's metric),
    not against the previous line's inbound (the old chained walk's lag-2)."""
    gate = _load_gate()
    transcript = [
        # hooks its inbound ("staplers")
        {"turn": 0, "speaker": gate.CHANDLER_ID, "speaker_name": "chandler",
         "kind": "talk", "text": "wow, the staplers",
         "inbound": "the staplers are sobbing"},
        # does NOT hook its inbound ("wow, staplers")
        {"turn": 1, "speaker": gate.MONICA_ID, "speaker_name": "monica",
         "kind": "talk", "text": "totally unrelated words here",
         "inbound": "wow, staplers"},
        # hooks its inbound ("unrelated") — the old lag-2 walk missed this one
        {"turn": 2, "speaker": gate.CHANDLER_ID, "speaker_name": "chandler",
         "kind": "talk", "text": "sure, unrelated",
         "inbound": "totally unrelated words here"},
        {"turn": 3, "speaker": gate.MONICA_ID, "speaker_name": "monica",
         "kind": "talk", "text": "random as ever, pal",
         "inbound": "sure, unrelated"},
    ]
    verdict = gate.eval_transcript(
        transcript, opener="the staplers are sobbing", scenario="stranger", s2=None
    )
    gw = verdict["criteria"]["grounded_wit"]
    assert gw["echo_rate"] == pytest.approx(0.5)  # lines 0 and 2 hook
