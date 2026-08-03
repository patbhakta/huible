from __future__ import annotations

from tests.f2.conftest import _make_context


class TestF2_5_PertinenceGate:
    """F2.5: Pertinence gate requires content grows persona or deepens relationship."""

    async def test_empty_content_fails(self, pertinence_gate):
        result = await pertinence_gate.evaluate({"content": ""}, _make_context())
        assert result.outcome.value == "fail"

    async def test_short_content_ambiguous(self, pertinence_gate):
        result = await pertinence_gate.evaluate({"content": "ok sure"}, _make_context())
        assert result.outcome.value == "ambiguous"

    async def test_substantive_content_passes(self, pertinence_gate):
        result = await pertinence_gate.evaluate(
            {"content": "Dad took us fishing every summer weekend when we were kids"},
            _make_context(),
        )
        assert result.outcome.value == "pass"

    async def test_unknown_content_type_ambiguous(self, pertinence_gate):
        result = await pertinence_gate.evaluate(
            {"content": "Some reasonably long content here", "content_type": "weird_type"},
            _make_context(),
        )
        assert result.outcome.value == "ambiguous"

    async def test_tier2_low_score_ambiguous(self, pertinence_gate):
        async def mock_tier2(gate_name, candidate, ctx):
            return {"outcome": "pass", "score": 0.1}

        result = await pertinence_gate.evaluate(
            {"content": "Short thing"},
            _make_context(tier2_model=mock_tier2),
        )
        assert result.outcome.value == "ambiguous"

    async def test_tier2_high_score_passes(self, pertinence_gate):
        async def mock_tier2(gate_name, candidate, ctx):
            return {"outcome": "pass", "score": 0.8}

        result = await pertinence_gate.evaluate(
            {"content": "Dad loved fishing and taught us all how to cast a line"},
            _make_context(tier2_model=mock_tier2),
        )
        assert result.outcome.value == "pass"

    async def test_greeting_boilerplate_ambiguous(self, pertinence_gate):
        result = await pertinence_gate.evaluate(
            {"content": "Hi there"},
            _make_context(),
        )
        assert result.outcome.value == "ambiguous"
