"""Corpus-derived reply budgets (HU-2231).

Generalizes the HU-1911 corpus-length spec from one global constant to a
per-persona rule: at onboarding, measure the persona's real-text length
distribution (median / p75 / p90 in characters) from their ingested
transcripts and derive both halves of the reply budget from it —

* the per-turn ``max_tokens`` cap at the chat call site
  (:func:`derive_reply_max_tokens`), and
* the channel-shape directive anchors (word band, sincere-pivot
  allowance) rendered into the system prompt
  (:func:`render_texting_directive`).

A talkative client's persona texts long; a one-liner's texts short —
persona fidelity includes length register.

Calibration (friends-v2.csv, 8,376 Chandler turns, measured 2026-08-30:
median 44ch / p75 79 / p90 129; 94% ≤160ch, 99% ≤300ch). The HU-1911
verified spec for that register — "~5-12 words, hard cap ~130ch for
banter, ≤~300ch pivot allowance, 64-token generation cap" — falls out
of the derivation constants below, so rendering the Chandler ground
truth through the template reproduces the verified directive verbatim
and the derived cap reproduces the verified global
``persona_chat_max_tokens = 64``.

When a persona has no corpus stats (fail closed on missing/garbage
metadata), callers fall back to the safe default: the Chandler-tuned
constant directive + the global ``persona_chat_max_tokens`` setting.

Deterministic throughout: pure ``statistics`` quantiles, no LLM, no
network. No dependency on the context builder (context imports this
module, not the reverse).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import quantiles
from typing import Any

__all__ = [
    "CHANDLER_GROUND_TRUTH",
    "CHARS_PER_TOKEN",
    "MAX_REPLY_TOKENS",
    "METADATA_KEY",
    "MIN_CORPUS_SAMPLE",
    "MIN_REPLY_TOKENS",
    "PIVOT_ALLOWANCE_MULTIPLIER",
    "SHORT_REGISTER_MAX_MEDIAN_CHARS",
    "TEXTING_CONCISION_DIRECTIVE",
    "CorpusLengthStats",
    "compute_corpus_length_stats",
    "derive_reply_max_tokens",
    "render_texting_directive",
    "reply_budget_tokens",
    "stats_from_metadata",
    "stats_to_metadata",
]

#: Persona-record metadata key holding the measured length distribution
#: (``personas.metadata["corpus_length"]``, written by the onboarding
#: provision path, read back by registry hydration).
METADATA_KEY = "corpus_length"

#: Minimum number of non-empty lines before a measured distribution is
#: trusted. Below this the persona keeps the safe default budget.
MIN_CORPUS_SAMPLE = 30

#: Sincere/emotional pivot allowance as a multiple of the persona's p90.
#: Chandler: 129 x 2.3 ≈ 297 → anchors to ~300ch (verified spec).
PIVOT_ALLOWANCE_MULTIPLIER = 2.3

#: Conservative chars-per-token for hosted generation budgeting. Chandler:
#: ceil(297 / 4.7) = 64 tokens — reproduces the verified global cap.
CHARS_PER_TOKEN = 4.7

#: Safety bounds on the derived per-turn generation cap.
MIN_REPLY_TOKENS = 32
MAX_REPLY_TOKENS = 512

#: Medians above this (one classic SMS segment) select the long-register
#: directive wording; at or below it the one-liner/quip register.
SHORT_REGISTER_MAX_MEDIAN_CHARS = 160

#: Average characters per word (incl. spaces) used to translate the
#: measured char median into the directive's word-count anchor.
_CHARS_PER_WORD = 5.2

#: The measured distribution the HU-1911 spec was tuned against. Used as
#: the calibration reference in tests; never as a silent runtime default
#: (no-corpus personas use the constant directive + global cap instead,
#: so a fallback never masquerades as measured data).
CHANDLER_GROUND_TRUTH = None  # set below (dataclass defined first)


@dataclass(frozen=True, slots=True)
class CorpusLengthStats:
    """A persona's measured real-text length distribution (characters).

    ``sample_lines`` records how many non-empty lines the percentiles were
    computed over, so downstream audits can distinguish a measured budget
    from a thin one.
    """

    median_chars: int
    p75_chars: int
    p90_chars: int
    sample_lines: int

    @property
    def pivot_chars(self) -> int:
        """Sincere/emotional pivot allowance (rare long turn), rounded to a
        prompt-friendly anchor."""
        return _nice_anchor(self.p90_chars * PIVOT_ALLOWANCE_MULTIPLIER)

    @property
    def word_band(self) -> tuple[int, int]:
        """Directive word-count anchor ``(low, high)`` derived from the
        median. Chandler: median 44 → 8 words → (5, 12), the verified band
        (the median is quantized to a whole word count before scaling so
        the Chandler calibration lands exactly on the verified anchor)."""
        median_words = round(self.median_chars / _CHARS_PER_WORD)
        low = max(3, round(median_words * 0.6))
        high = max(low + 2, round(median_words * 1.5))
        return low, high

    @property
    def is_short_register(self) -> bool:
        """True when the persona's median fits the one-liner/quip register."""
        return self.median_chars <= SHORT_REGISTER_MAX_MEDIAN_CHARS


CHANDLER_GROUND_TRUTH = CorpusLengthStats(
    median_chars=44, p75_chars=79, p90_chars=129, sample_lines=8376
)


def _nice_anchor(value: float) -> int:
    """Round a character anchor to a prompt-friendly multiple of 50.

    Prompts hug the number they are given (HU-1911 iteration finding), so
    anchors read as "~300" rather than "~297". 129 x 2.3 ≈ 296.7 → 300,
    reproducing the verified directive byte-for-byte.
    """
    return int(round(value / 50.0) * 50)


def compute_corpus_length_stats(texts: Sequence[str]) -> CorpusLengthStats | None:
    """Measure the length distribution of a persona's own lines.

    Deterministic: linear-interpolation quantiles (``method="inclusive"``,
    matching the 2026-08-30 friends-v2.csv measurement — median 44 / p75
    79 / p90 129 for Chandler). Returns ``None`` when fewer than
    ``MIN_CORPUS_SAMPLE`` non-empty lines are available, so a too-thin
    corpus never overrides the safe default budget.
    """
    lengths = [len(t.strip()) for t in texts if t and t.strip()]
    if len(lengths) < MIN_CORPUS_SAMPLE:
        return None
    cuts = quantiles(lengths, n=100, method="inclusive")
    return CorpusLengthStats(
        median_chars=round(cuts[49]),
        p75_chars=round(cuts[74]),
        p90_chars=round(cuts[89]),
        sample_lines=len(lengths),
    )


# --- Persona-record (de)serialization ----------------------------------------


def stats_to_metadata(stats: CorpusLengthStats) -> dict[str, int]:
    """Render stats as the JSON block stored under ``metadata[METADATA_KEY]``."""
    return {
        "median_chars": stats.median_chars,
        "p75_chars": stats.p75_chars,
        "p90_chars": stats.p90_chars,
        "sample_lines": stats.sample_lines,
    }


def stats_from_metadata(metadata: Any) -> CorpusLengthStats | None:
    """Parse stored stats off a persona record's metadata (fail closed).

    Accepts either the full persona metadata mapping or the
    ``METADATA_KEY`` sub-block. Returns ``None`` on missing keys,
    non-integers, non-positive values, an implausible ordering
    (median > p75 > p90 must be non-decreasing), or a below-minimum
    sample — the persona then keeps the safe default budget.
    """
    if not isinstance(metadata, dict):
        return None
    block = metadata.get(METADATA_KEY, metadata)
    if not isinstance(block, dict):
        return None
    # Strict typing: values must be real ints (JSON numbers). ``bool`` is an
    # ``int`` subclass and numeric strings parse via int() — both rejected so
    # garbage never masquerades as a measured register.
    values: dict[str, int] = {}
    for key in ("median_chars", "p75_chars", "p90_chars", "sample_lines"):
        raw = block.get(key)
        if isinstance(raw, bool) or not isinstance(raw, int):
            return None
        values[key] = raw
    try:
        stats = CorpusLengthStats(**values)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return None
    if min(stats.median_chars, stats.p75_chars, stats.p90_chars) < 1:
        return None
    if not (stats.median_chars <= stats.p75_chars <= stats.p90_chars):
        return None
    if stats.sample_lines < MIN_CORPUS_SAMPLE:
        return None
    return stats


# --- Budget derivation --------------------------------------------------------


def derive_reply_max_tokens(stats: CorpusLengthStats | None) -> int | None:
    """Derive the per-turn generation cap (tokens) from measured stats.

    The cap must cover the persona's *pivot* register (their rare sincere
    long turn), not just their banter p90, or it would clip exactly the
    moments the directive permits going longer. Chandler: pivot ~300ch →
    64 tokens, matching the verified global
    ``persona_chat_max_tokens = 64``. Clamped to
    ``[MIN_REPLY_TOKENS, MAX_REPLY_TOKENS]``. Returns ``None`` when there
    are no stats (caller falls back to the global setting).
    """
    if stats is None:
        return None
    pivot_chars = stats.p90_chars * PIVOT_ALLOWANCE_MULTIPLIER
    return max(
        MIN_REPLY_TOKENS,
        min(MAX_REPLY_TOKENS, math.ceil(pivot_chars / CHARS_PER_TOKEN)),
    )


def reply_budget_tokens(stats: CorpusLengthStats | None, default: int) -> int:
    """Chat-call-site resolver: per-persona cap when measured, else default."""
    return derive_reply_max_tokens(stats) or default


# --- Directive templating ------------------------------------------------------

#: Channel-shape directive appended to every persona system prompt (Stage 0:
#: texting-only, HU-1911 human-touch gate). Code-controlled like the framing
#: block but presentation-layer, not clinical: it bounds reply *shape* so the
#: persona texts like a person instead of writing essays. Rubric mapping:
#: #2 (no bullets/markdown), #3 (texting length), and the §7.1 disclosure
#: monologue — honesty about being a memory is mandated by G2 framing; this
#: directive compresses its delivery to one in-voice line.
#:
#: This constant is the **no-corpus fallback**: the exact wording verified
#: live on 2026-08-30 (median 78ch, 0/15 turns over 160ch) against
#: Chandler's corpus (median 44ch / p75 79 / p90 129). Personas with
#: measured stats get a templated variant from :func:`render_texting_directive`
#: whose Chandler calibration reproduces this text byte-for-byte.
#: Generalization rule (HU-2231): at onboarding, measure the persona's own
#: real-text distribution and derive its reply budget from it — persona
#: fidelity includes length register.
TEXTING_CONCISION_DIRECTIVE = (
    "[CHANNEL — texting]\n"
    "This is a text thread. Reply the way this person really texts: one "
    "line, like them. Their real lines are a quip, not a paragraph — "
    "usually ONE short sentence of 5 to 12 words. When a quick line "
    "answers it, never say more. A quick joke beats a long one; leaving "
    "them wanting more is the bit.\n"
    "Only when the moment genuinely turns sincere or emotional may you go "
    "longer — at most ~300 characters, and rarely.\n"
    "No bullet points, no numbered lists, no headings, no markdown "
    "formatting.\n"
    "If the moment calls for honesty about being a memory rather than the "
    "living person, say it in one light line and move on — never a speech "
    "about it."
)

_SHORT_REGISTER_TEMPLATE = (
    "[CHANNEL — texting]\n"
    "This is a text thread. Reply the way this person really texts: one "
    "line, like them. Their real lines are a quip, not a paragraph — "
    "usually ONE short sentence of {word_low} to {word_high} words. When a "
    "quick line answers it, never say more. A quick joke beats a long one; "
    "leaving them wanting more is the bit.\n"
    "Only when the moment genuinely turns sincere or emotional may you go "
    "longer — at most ~{pivot_chars} characters, and rarely.\n"
    "No bullet points, no numbered lists, no headings, no markdown "
    "formatting.\n"
    "If the moment calls for honesty about being a memory rather than the "
    "living person, say it in one light line and move on — never a speech "
    "about it."
)

_LONG_REGISTER_TEMPLATE = (
    "[CHANNEL — texting]\n"
    "This is a text thread. Reply the way this person really texts: like "
    "them, at their real length. Their real lines run fuller — usually "
    "one message of about {word_low} to {word_high} words. Match their "
    "natural rhythm: don't compress them into a one-liner they would "
    "never send, and don't pad past what they would actually write.\n"
    "Only when the moment genuinely turns sincere or emotional may you go "
    "longer — at most ~{pivot_chars} characters, and rarely.\n"
    "No bullet points, no numbered lists, no headings, no markdown "
    "formatting.\n"
    "If the moment calls for honesty about being a memory rather than the "
    "living person, say it in one light line and move on — never a speech "
    "about it."
)


def render_texting_directive(stats: CorpusLengthStats | None) -> str:
    """Render the channel-shape directive for a persona's length register.

    ``None`` (no corpus) → the verified fallback constant verbatim, so the
    no-corpus path is byte-identical to the pre-HU-2231 behavior. Measured
    stats → the same directive skeleton with numeric anchors derived from
    the persona's own distribution; the Chandler ground truth reproduces
    the fallback constant exactly ("5 to 12 words", "~300 characters").
    """
    if stats is None:
        return TEXTING_CONCISION_DIRECTIVE
    word_low, word_high = stats.word_band
    template = _SHORT_REGISTER_TEMPLATE if stats.is_short_register else _LONG_REGISTER_TEMPLATE
    return template.format(
        word_low=word_low,
        word_high=word_high,
        pivot_chars=stats.pivot_chars,
    )
