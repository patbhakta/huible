"""Tests for ``huible.persona.length`` — corpus-derived reply budgets (HU-2231).

Regression scope from the issue:

- **One-liner persona (Chandler-like):** measured corpus stats (median 44 /
  p75 79 / p90 129 — friends-v2.csv ground truth) must reproduce the
  HU-1911-verified budget exactly: the 64-token cap (== the global
  ``persona_chat_max_tokens``) and the directive anchors "5 to 12 words" /
  "~300 characters" — in fact the rendered directive must be byte-identical
  to the fallback constant.
- **Long-winded persona:** the derived cap must NOT clip the persona's own
  register — it has to cover their sincere-pivot allowance (p90 x 2.3), not
  the Chandler ceiling.
- **Fallback (no corpus):** ``None`` stats keep the verified constant
  directive verbatim and defer to the global cap setting.
- Measurement, metadata round-trip, and fail-closed parsing.
"""

from __future__ import annotations

from uuid import uuid4

from huible.persona.context import (
    TEXTING_CONCISION_DIRECTIVE,
    ContextBuilder,
    PersonaConfig,
    RelationshipTier,
    render_texting_directive,
)
from huible.persona.length import (
    CHANDLER_GROUND_TRUTH,
    CHARS_PER_TOKEN,
    MAX_REPLY_TOKENS,
    METADATA_KEY,
    MIN_CORPUS_SAMPLE,
    MIN_REPLY_TOKENS,
    CorpusLengthStats,
    compute_corpus_length_stats,
    derive_reply_max_tokens,
    reply_budget_tokens,
    stats_from_metadata,
    stats_to_metadata,
)

PERSONA_ID = uuid4()


def _persona(stats: CorpusLengthStats | None = None) -> PersonaConfig:
    return PersonaConfig(
        id=PERSONA_ID,
        name="Bob",
        voice_instructions="Speak slowly.",
        length_stats=stats,
    )


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------


class TestComputeCorpusLengthStats:
    def test_percentiles_match_corpus_ground_truth_method(self):
        """The quantile method reproduces the friends-v2.csv measurement
        (Chandler: median 44 / p75 79 / p90 129) — the same numbers the
        HU-1911 spec was derived from. Inclusive linear interpolation over
        lengths 1..100 gives p50 50.5→50, p75 75.25→75, p90 90.1→90."""
        texts = ["x" * i for i in range(1, 101)]
        stats = compute_corpus_length_stats(texts)
        assert stats is not None
        assert stats.median_chars == 50
        assert stats.p75_chars == 75
        assert stats.p90_chars == 90
        assert stats.sample_lines == 100

    def test_chandler_register_measured_exactly(self):
        """The recorded ground truth matches the measured friends-v2
        distribution anchors used throughout the spec."""
        stats = CorpusLengthStats(
            median_chars=44, p75_chars=79, p90_chars=129, sample_lines=8376
        )
        assert stats == CHANDLER_GROUND_TRUTH

    def test_too_thin_corpus_returns_none(self):
        """< MIN_CORPUS_SAMPLE lines never overrides the safe default."""
        assert compute_corpus_length_stats(["hey"] * (MIN_CORPUS_SAMPLE - 1)) is None

    def test_empty_and_blank_corpus_returns_none(self):
        assert compute_corpus_length_stats([]) is None
        assert compute_corpus_length_stats(["", "   ", None]) is None  # type: ignore[list-item]

    def test_blank_lines_are_excluded_from_sample(self):
        texts = ["x" * 10] * MIN_CORPUS_SAMPLE + ["", "  "]
        stats = compute_corpus_length_stats(texts)
        assert stats is not None
        assert stats.sample_lines == MIN_CORPUS_SAMPLE
        assert stats.median_chars == 10


# ---------------------------------------------------------------------------
# Budget derivation
# ---------------------------------------------------------------------------


class TestDeriveReplyMaxTokens:
    def test_chandler_register_reproduces_verified_global_cap(self):
        """Chandler's measured distribution derives exactly the verified
        64-token cap (== settings.persona_chat_max_tokens)."""
        assert derive_reply_max_tokens(CHANDLER_GROUND_TRUTH) == 64

    def test_long_winded_persona_cap_does_not_clip(self):
        """A talkative persona's cap must cover their own full register —
        normal banter AND the sincere-pivot allowance — not the Chandler
        ceiling (regression: the global 64-token cap clipped personas like
        this before HU-2231)."""
        talkative = CorpusLengthStats(
            median_chars=420, p75_chars=640, p90_chars=900, sample_lines=400
        )
        cap = derive_reply_max_tokens(talkative)
        assert cap > 64
        # The cap must not clip even the persona's rarest register.
        assert cap * CHARS_PER_TOKEN >= talkative.p90_chars * 2.3
        # And it comfortably covers their normal (median..p90) register.
        assert cap * CHARS_PER_TOKEN > talkative.p90_chars

    def test_cap_clamped_to_safety_bounds(self):
        enormous = CorpusLengthStats(
            median_chars=2000, p75_chars=3000, p90_chars=5000, sample_lines=500
        )
        assert derive_reply_max_tokens(enormous) == MAX_REPLY_TOKENS
        tiny = CorpusLengthStats(
            median_chars=2, p75_chars=3, p90_chars=5, sample_lines=500
        )
        assert derive_reply_max_tokens(tiny) == MIN_REPLY_TOKENS

    def test_none_stats_derive_none(self):
        assert derive_reply_max_tokens(None) is None

    def test_reply_budget_tokens_falls_back_to_default(self):
        assert reply_budget_tokens(None, default=64) == 64
        assert reply_budget_tokens(CHANDLER_GROUND_TRUTH, default=160) == 64


# ---------------------------------------------------------------------------
# Directive templating
# ---------------------------------------------------------------------------


class TestRenderTextingDirective:
    def test_no_corpus_renders_verified_constant_verbatim(self):
        assert render_texting_directive(None) == TEXTING_CONCISION_DIRECTIVE

    def test_chandler_register_renders_verified_directive_byte_identical(self):
        """Rendering the measured Chandler ground truth through the template
        reproduces the live-verified constant exactly — the calibration
        contract that makes templating a safe generalization."""
        assert render_texting_directive(CHANDLER_GROUND_TRUTH) == (
            TEXTING_CONCISION_DIRECTIVE
        )

    def test_one_liner_persona_gets_quip_register_anchors(self):
        stats = CorpusLengthStats(
            median_chars=30, p75_chars=55, p90_chars=80, sample_lines=200
        )
        directive = render_texting_directive(stats)
        assert directive.startswith("[CHANNEL — texting]")
        assert "ONE short sentence" in directive
        assert "one line" in directive
        # Word band: median 30 -> 6 words -> 4..9.
        assert "4 to 9 words" in directive
        # Pivot allowance = p90 x 2.3 = 184 -> nearest-50 anchor 200.
        assert "~200 characters" in directive

    def test_long_winded_persona_gets_fuller_register_wording(self):
        """A talkative persona is told to match their real length — the
        one-liner compression wording must NOT be applied to them."""
        talkative = CorpusLengthStats(
            median_chars=420, p75_chars=640, p90_chars=900, sample_lines=400
        )
        directive = render_texting_directive(talkative)
        assert "run fuller" in directive
        assert "don't compress them into a one-liner" in directive
        # Word band ~ 420/5.2 = 81 words -> 49..122.
        assert "49 to 122 words" in directive
        # Pivot 900 x 2.3 = 2070 -> anchor 2050 (rare sincere allowance).
        assert "~2050 characters" in directive
        # Invariants shared with the short register.
        assert "No bullet points" in directive
        assert "one light line" in directive


# ---------------------------------------------------------------------------
# Persona-record metadata round-trip
# ---------------------------------------------------------------------------


class TestMetadataRoundTrip:
    def test_round_trip(self):
        stats = CorpusLengthStats(
            median_chars=210, p75_chars=300, p90_chars=480, sample_lines=1500
        )
        metadata = {"corpus": "export.csv", METADATA_KEY: stats_to_metadata(stats)}
        assert stats_from_metadata(metadata) == stats

    def test_parses_bare_block(self):
        block = {"median_chars": 44, "p75_chars": 79, "p90_chars": 129, "sample_lines": 8376}
        assert stats_from_metadata(block) == CHANDLER_GROUND_TRUTH

    def test_fail_closed_on_garbage(self):
        assert stats_from_metadata(None) is None
        assert stats_from_metadata("corpus_length") is None
        assert stats_from_metadata({}) is None
        assert stats_from_metadata({METADATA_KEY: {}}) is None
        assert stats_from_metadata({METADATA_KEY: "not-a-block"}) is None
        # Missing keys.
        assert stats_from_metadata({METADATA_KEY: {"median_chars": 44}}) is None
        # Non-integers.
        assert stats_from_metadata(
            {METADATA_KEY: {
                "median_chars": "44", "p75_chars": "79",
                "p90_chars": "129", "sample_lines": "8376",
            }}
        ) is None
        # Implausible ordering (median > p90).
        assert stats_from_metadata(
            {METADATA_KEY: {
                "median_chars": 500, "p75_chars": 300, "p90_chars": 129,
                "sample_lines": 8376,
            }}
        ) is None
        # Below-minimum sample never overrides the default budget.
        assert stats_from_metadata(
            {METADATA_KEY: {
                "median_chars": 44, "p75_chars": 79, "p90_chars": 129,
                "sample_lines": 3,
            }}
        ) is None


# ---------------------------------------------------------------------------
# Context-builder integration (per-persona system prompts)
# ---------------------------------------------------------------------------


class TestContextBuilderLengthRegister:
    def test_persona_without_stats_gets_fallback_directive(self):
        ctx = ContextBuilder().filter_and_render([], _persona(), RelationshipTier.FAMILY)
        assert TEXTING_CONCISION_DIRECTIVE in ctx.system_prompt

    def test_one_liner_persona_gets_measured_anchors(self):
        chandler = PersonaConfig(
            id=PERSONA_ID, name="Chandler Bing", length_stats=CHANDLER_GROUND_TRUTH
        )
        ctx = ContextBuilder().filter_and_render([], chandler, RelationshipTier.FAMILY)
        # Chandler calibration == the verified constant, byte-identical.
        assert TEXTING_CONCISION_DIRECTIVE in ctx.system_prompt

    def test_long_winded_persona_gets_templated_directive(self):
        talkative = CorpusLengthStats(
            median_chars=420, p75_chars=640, p90_chars=900, sample_lines=400
        )
        ctx = ContextBuilder().filter_and_render(
            [], _persona(talkative), RelationshipTier.FAMILY
        )
        assert "run fuller" in ctx.system_prompt
        assert "49 to 122 words" in ctx.system_prompt
        # The Chandler one-liner anchors must not be forced onto them.
        assert "ONE short sentence of 5 to 12 words" not in ctx.system_prompt

    def test_distress_branch_keeps_channel_bound(self):
        from huible.safety.crisis import UserAffect

        talkative = CorpusLengthStats(
            median_chars=420, p75_chars=640, p90_chars=900, sample_lines=400
        )
        ctx = ContextBuilder().filter_and_render(
            [], _persona(talkative), RelationshipTier.FAMILY,
            user_affect=UserAffect.DISTRESS,
        )
        assert "run fuller" in ctx.system_prompt
