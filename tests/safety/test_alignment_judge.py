"""Unit tests for the HU-2161 §7.4.2 LLM-judge backstop (huible.safety.judge).

Covers: provider eligibility (fake/mock never judge), the canon digest build
(canonical tier only, capped), verdict parsing (happy path, prose-wrapped
JSON, unmatched claims fail toward fabricated, malformed output →
unavailable), and the never-raises contract on transport errors / timeouts.
"""

from __future__ import annotations

import asyncio

from huible.safety.alignment import Claim, ClaimCategory
from huible.safety.judge import (
    adjudicate_alignment_claims,
    build_canon_digest,
    judge_eligible,
)


class _RealLLM:
    provider = "zai"

    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.calls = 0

    async def generate(self, prompt: str, *, system_prompt=None, **kwargs) -> str:
        self.calls += 1
        return self._replies.pop(0)


class _FakeLLM:
    provider = "fake"

    async def generate(self, prompt: str, *, system_prompt=None, **kwargs) -> str:
        return "unused"


class _Node:
    def __init__(self, tier: str, content: str) -> None:
        self.tier = tier
        self.content = content


_CLAIM = Claim(
    text="I spent nine years doing statistical analysis and data reconfiguration.",
    category=ClaimCategory.BIOGRAPHICAL,
    salient_entities=("statistical analysis",),
)


def test_fake_and_mock_providers_never_judge():
    assert judge_eligible(None) is False
    assert judge_eligible(_FakeLLM()) is False
    assert judge_eligible(_RealLLM([])) is True


def test_canon_digest_uses_canonical_tier_only_and_caps():
    refs = [
        _Node("canonical", "career — transferred to Tulsa"),
        _Node("accrued", "raw dialogue line that must not bloat the digest"),
    ] * 50
    digest = build_canon_digest(
        persona_name="Chandler Bing",
        voice_instructions="Sarcastic, self-deprecating.",
        era_knowledge_boundary="2004-05-06",
        persona_scope_refs=refs,
    )
    assert "Chandler Bing" in digest
    assert "Tulsa" in digest
    assert "Sarcastic" in digest
    assert "raw dialogue line" not in digest
    assert len(digest) <= 6_000


def test_supported_verdict_overturns():
    llm = _RealLLM(
        [
            '{"verdicts": [{"claim": "' + _CLAIM.text + '", "verdict": "supported"}],'
            ' "reason": "consistent"}'
        ]
    )
    verdict = asyncio.run(
        adjudicate_alignment_claims(
            llm=llm, persona_name="Chandler Bing", canon_digest="digest",
            claims=[_CLAIM],
        )
    )
    assert verdict.outcome == "supported"
    assert verdict.supported == (_CLAIM.text,)
    assert verdict.fabricated == ()


def test_fabricated_verdict_confirms():
    llm = _RealLLM(
        [
            'Sure — here is my review:\n{"verdicts": [{"claim": "'
            + _CLAIM.text
            + '", "verdict": "fabricated"}], "reason": "invented"}\nthanks'
        ]
    )
    verdict = asyncio.run(
        adjudicate_alignment_claims(
            llm=llm, persona_name="Chandler Bing", canon_digest="digest",
            claims=[_CLAIM],
        )
    )
    assert verdict.outcome == "fabricated"
    assert verdict.fabricated == (_CLAIM.text,)


def test_verdict_echo_with_normalized_punctuation_matches():
    """The judge may echo the claim without surrounding quotes/dashes
    (the live HU-2161 probe failure: 'verdicts matched no claims')."""
    quoted = Claim(
        text='"Famously fled his own job" — I\'d be offended if it weren\'t accurate.',
        category=ClaimCategory.BIOGRAPHICAL,
        salient_entities=("Famously",),
    )
    llm = _RealLLM(
        [
            '{"verdicts": [{"claim": "Famously fled his own job - I\'d be '
            'offended if it weren\'t accurate.", "verdict": "supported"}], '
            '"reason": "echoes the user"}'
        ]
    )
    verdict = asyncio.run(
        adjudicate_alignment_claims(
            llm=llm, persona_name="Chandler Bing", canon_digest="digest",
            claims=[quoted],
        )
    )
    assert verdict.outcome == "supported"


def test_unmatched_claim_fails_toward_fabricated():
    other = Claim(text="I lived in Marfa for a winter.", category=ClaimCategory.BIOGRAPHICAL)
    llm = _RealLLM(
        [
            '{"verdicts": [{"claim": "' + _CLAIM.text + '", "verdict": "supported"},'
            ' {"claim": "something else", "verdict": "supported"}]}'
        ]
    )
    verdict = asyncio.run(
        adjudicate_alignment_claims(
            llm=llm, persona_name="Chandler Bing", canon_digest="digest",
            claims=[_CLAIM, other],
        )
    )
    # One flagged claim got no verdict → treated as not cleared (fabricated).
    assert verdict.outcome == "fabricated"


def test_all_verdicts_matching_no_claims_is_unavailable():
    llm = _RealLLM(['{"verdicts": [{"claim": "something else", "verdict": "supported"}]}'])
    verdict = asyncio.run(
        adjudicate_alignment_claims(
            llm=llm, persona_name="Chandler Bing", canon_digest="digest",
            claims=[_CLAIM],
        )
    )
    # The judge answered about claims we never sent — malfunctioning judge.
    assert verdict.outcome == "unavailable"


def test_malformed_output_is_unavailable():
    for raw in ("", "no json here", "{broken", '{"verdicts": []}'):
        llm = _RealLLM([raw])
        verdict = asyncio.run(
            adjudicate_alignment_claims(
                llm=llm, persona_name="Chandler Bing", canon_digest="digest",
                claims=[_CLAIM],
            )
        )
        assert verdict.outcome == "unavailable", raw


def test_transport_error_and_timeout_are_unavailable():
    class _Boom:
        provider = "zai"

        async def generate(self, prompt: str, *, system_prompt=None, **kwargs) -> str:
            raise RuntimeError("provider down")

    verdict = asyncio.run(
        adjudicate_alignment_claims(
            llm=_Boom(), persona_name="Chandler Bing", canon_digest="d",
            claims=[_CLAIM], timeout_s=0.05,
        )
    )
    assert verdict.outcome == "unavailable"

    class _Hang:
        provider = "zai"

        async def generate(self, prompt: str, *, system_prompt=None, **kwargs) -> str:
            await asyncio.sleep(1)
            return "{}"

    verdict = asyncio.run(
        adjudicate_alignment_claims(
            llm=_Hang(), persona_name="Chandler Bing", canon_digest="d",
            claims=[_CLAIM], timeout_s=0.05,
        )
    )
    assert verdict.outcome == "unavailable"
    assert "timeout" in verdict.reason


def test_policy_claims_are_not_judgeable():
    identity = Claim(text="I am really here.", category=ClaimCategory.IDENTITY)
    llm = _RealLLM([])
    verdict = asyncio.run(
        adjudicate_alignment_claims(
            llm=llm, persona_name="Chandler Bing", canon_digest="d",
            claims=[identity],
        )
    )
    assert verdict.outcome == "supported"  # nothing to judge — caller keeps policy path
    assert llm.calls == 0
