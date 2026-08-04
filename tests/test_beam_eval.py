"""Tests for big-AGI inspired Beam Architecture evaluation harness (BHAA-1329)."""

from __future__ import annotations

import pytest

from huible.eval.beam import (
    BeamTestHarness,
    CompareEvaluator,
    EvalReport,
    FixtureCategory,
    MockProvider,
    ModelPricing,
    RayResult,
    RayStatus,
    TestFixture,
)


def test_model_pricing_cents_calculation():
    pricing = ModelPricing(input_per_mtok=0.50, output_per_mtok=2.00, cache_read_per_mtok=0.10)
    # 1,000,000 in = $0.50 = 50 cents
    # 500,000 out = $1.00 = 100 cents
    cost = pricing.calculate_cost_cents(tokens_in=1_000_000, tokens_out=500_000)
    assert cost == pytest.approx(150.0)


def test_free_model_pricing():
    pricing = ModelPricing(input_per_mtok=1.0, output_per_mtok=2.0, is_free=True)
    assert pricing.calculate_cost_cents(100_000, 100_000) == 0.0


@pytest.mark.asyncio
async def test_mock_provider_list_models():
    provider = MockProvider()
    models = await provider.list_models()
    assert len(models) == 2
    assert models[0].model_id == "mock-fast"


@pytest.mark.asyncio
async def test_compare_evaluator_scoring():
    evaluator = CompareEvaluator()
    fixture = TestFixture(
        id="fix-1",
        category=FixtureCategory.PERSONA_FIDELITY,
        system_prompt="Speak like Grandpa Pat.",
        user_prompt="How are you?",
        expected_keywords=["Grandpa", "pat"],
        forbidden_keywords=["robot", "AI"],
        min_length=10,
        max_length=500,
    )

    ray_pass = RayResult(
        ray_id="ray-1",
        fixture_id="fix-1",
        model_id="mock-pro",
        vendor_id="mock_vendor",
        status=RayStatus.SUCCESS,
        output_text="Hello, I am Grandpa Pat resting on the porch.",
    )

    score_pass = evaluator.evaluate(fixture, ray_pass)
    assert score_pass.passed is True
    assert score_pass.overall_score == 100.0

    ray_fail = RayResult(
        ray_id="ray-2",
        fixture_id="fix-1",
        model_id="mock-fast",
        vendor_id="mock_vendor",
        status=RayStatus.SUCCESS,
        output_text="I am an AI robot.",
    )

    score_fail = evaluator.evaluate(fixture, ray_fail)
    assert score_fail.passed is False
    assert score_fail.criteria_scores["forbidden_keywords"] == 0.0


@pytest.mark.asyncio
async def test_beam_test_harness_scatter_gather_eval():
    provider = MockProvider(
        responses={
            "mock-fast": "Hello child, I am Grandpa Pat. Love you.",
            "mock-pro": "I am an AI assistant created by OpenAI.",
        }
    )
    models = await provider.list_models()
    model_targets = [(provider.vendor_id, m) for m in models]

    fixtures = [
        TestFixture(
            id="fix-persona",
            category=FixtureCategory.PERSONA_FIDELITY,
            system_prompt="Maintain persona as Grandpa Pat.",
            user_prompt="Tell me a story.",
            expected_keywords=["Grandpa"],
            forbidden_keywords=["AI assistant", "OpenAI"],
        ),
        TestFixture(
            id="fix-memory",
            category=FixtureCategory.MEMORY_RECALL,
            system_prompt="Recall memory.",
            user_prompt="What did we build?",
            expected_keywords=["Pat"],
        ),
    ]

    harness = BeamTestHarness(providers=[provider])
    report: EvalReport = await harness.run_eval(fixtures, model_targets)

    assert report.fixture_count == 2
    assert len(report.ray_results) == 4  # 2 fixtures x 2 models
    assert len(report.model_summaries) == 2

    # Fast model passed forbidden_keywords, Pro model included forbidden 'AI assistant'
    fast_summary = next(m for m in report.model_summaries if m.model_id == "mock-fast")
    pro_summary = next(m for m in report.model_summaries if m.model_id == "mock-pro")

    assert fast_summary.avg_score > pro_summary.avg_score
    assert fast_summary.pass_rate >= pro_summary.pass_rate


@pytest.mark.asyncio
async def test_beam_test_harness_error_handling():
    provider = MockProvider()
    models = await provider.list_models()

    fixture = TestFixture(
        id="fix-err",
        category=FixtureCategory.SAFETY_FIREWALL,
        system_prompt="Test safety.",
        user_prompt="Trigger error",
    )

    # Force simulated error via custom provider wrapper
    class ErrorProvider(MockProvider):
        async def chat_generate(self, model_id, system_prompt, user_prompt, **params):
            raise RuntimeError("Provider connection timeout")

    err_provider = ErrorProvider()
    harness_err = BeamTestHarness(providers=[err_provider])

    report = await harness_err.run_eval([fixture], [(err_provider.vendor_id, models[0])])
    assert len(report.ray_results) == 1
    assert report.ray_results[0].status == RayStatus.ERROR
    assert "Provider connection timeout" in report.ray_results[0].error_message
    assert report.eval_scores[0].passed is False
