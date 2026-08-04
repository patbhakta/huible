"""big-AGI inspired Beam Architecture evaluation harness for Huible persona engine.

Implements Scatter (parallel multi-model fan-out) and Gather/Fusion (scoring & synthesis),
with unified provider abstraction and cents-based metrics tracking.
"""

from __future__ import annotations

import asyncio
import enum
import time
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4


class FixtureCategory(enum.StrEnum):
    PERSONA_FIDELITY = "persona_fidelity"
    MEMORY_RECALL = "memory_recall"
    HALLUCINATION_RESISTANCE = "hallucination_resistance"
    INSTRUCTION_FOLLOWING = "instruction_following"
    MULTI_TURN_COHERENCE = "multi_turn_coherence"
    SAFETY_FIREWALL = "safety_firewall"


class RayStatus(enum.StrEnum):
    EMPTY = "empty"
    SCATTERING = "scattering"
    SUCCESS = "success"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass(frozen=True)
class ModelPricing:
    """Pricing model per million tokens (and cache), cents tracking."""

    input_per_mtok: float = 0.0
    output_per_mtok: float = 0.0
    cache_read_per_mtok: float = 0.0
    is_free: bool = False

    def calculate_cost_cents(
        self, tokens_in: int, tokens_out: int, cache_read_tokens: int = 0
    ) -> float:
        if self.is_free:
            return 0.0
        cost_in = (tokens_in / 1_000_000.0) * self.input_per_mtok
        cost_out = (tokens_out / 1_000_000.0) * self.output_per_mtok
        cost_cache = (cache_read_tokens / 1_000_000.0) * self.cache_read_per_mtok
        # Convert $ to cents
        return round((cost_in + cost_out + cost_cache) * 100.0, 6)


@dataclass(frozen=True)
class ModelInfo:
    """Model description equivalent to big-AGI KnownModel."""

    model_id: str
    vendor_id: str
    label: str
    context_window: int = 128_000
    max_output_tokens: int = 4_096
    pricing: ModelPricing = field(default_factory=ModelPricing)


@runtime_checkable
class LLMProvider(Protocol):
    """Unified provider interface inspired by big-AGI IModelVendor / AIX dispatch."""

    @property
    def vendor_id(self) -> str:
        ...

    async def chat_generate(
        self,
        model_id: str,
        system_prompt: str,
        user_prompt: str,
        **params: Any,
    ) -> str:
        ...

    async def list_models(self) -> list[ModelInfo]:
        ...


class MockProvider:
    """In-memory mock provider for testing and offline harness runs."""

    def __init__(
        self,
        vendor_id: str = "mock_vendor",
        models: list[ModelInfo] | None = None,
        responses: dict[str, str] | None = None,
        delay_s: float = 0.01,
    ) -> None:
        self._vendor_id = vendor_id
        self._delay_s = delay_s
        self._models = models or [
            ModelInfo(
                model_id="mock-fast",
                vendor_id=vendor_id,
                label="Mock Fast Model",
                pricing=ModelPricing(0.15, 0.60),
            ),
            ModelInfo(
                model_id="mock-pro",
                vendor_id=vendor_id,
                label="Mock Pro Model",
                pricing=ModelPricing(1.25, 5.00),
            ),
        ]
        self._responses = responses or {}

    @property
    def vendor_id(self) -> str:
        return self._vendor_id

    async def list_models(self) -> list[ModelInfo]:
        return self._models

    async def chat_generate(
        self,
        model_id: str,
        system_prompt: str,
        user_prompt: str,
        **params: Any,
    ) -> str:
        if self._delay_s > 0:
            await asyncio.sleep(self._delay_s)

        if params.get("simulated_error"):
            raise RuntimeError(f"Simulated error for model {model_id}")

        custom = self._responses.get(model_id)
        if custom:
            return custom

        return f"[{model_id}] Response to '{user_prompt}' with system prompt constraint."


@dataclass
class TestFixture:
    """Benchmark test fixture for persona engine evaluation."""

    __test__ = False

    id: str
    category: FixtureCategory
    system_prompt: str
    user_prompt: str
    expected_keywords: list[str] = field(default_factory=list)
    forbidden_keywords: list[str] = field(default_factory=list)
    min_length: int = 0
    max_length: int = 10_000
    criteria: dict[str, str] = field(default_factory=dict)


@dataclass
class RayResult:
    """Individual scatter ray output and metrics."""

    ray_id: str
    fixture_id: str
    model_id: str
    vendor_id: str
    status: RayStatus
    output_text: str = ""
    latency_ms: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_cents: float = 0.0
    error_message: str | None = None


@dataclass
class EvaluationScore:
    """Gather/Fusion evaluation output for a single ray result."""

    ray_id: str
    model_id: str
    overall_score: float  # 1 - 100
    criteria_scores: dict[str, float] = field(default_factory=dict)
    passed: bool = True
    reasoning: str = ""


@dataclass
class ModelSummary:
    """Aggregated evaluation metrics per candidate model."""

    model_id: str
    total_runs: int
    passed_runs: int
    pass_rate: float
    avg_score: float
    avg_latency_ms: float
    total_cost_cents: float


@dataclass
class EvalReport:
    """Complete Beam evaluation run report."""

    eval_id: str
    fixture_count: int
    model_summaries: list[ModelSummary]
    ray_results: list[RayResult]
    eval_scores: list[EvaluationScore]
    total_cost_cents: float
    total_elapsed_ms: float


class CompareEvaluator:
    """Evaluates ray outputs against test fixture criteria.

    Inspired by big-AGI Compare/eval factory.
    """

    def evaluate(self, fixture: TestFixture, ray: RayResult) -> EvaluationScore:
        if ray.status != RayStatus.SUCCESS or not ray.output_text:
            return EvaluationScore(
                ray_id=ray.ray_id,
                model_id=ray.model_id,
                overall_score=0.0,
                passed=False,
                reasoning=f"Ray failed: {ray.error_message or 'No output'}",
            )

        text = ray.output_text.lower()
        criteria_scores: dict[str, float] = {}
        reasons: list[str] = []

        # 1. Expected keywords check
        if fixture.expected_keywords:
            matched = sum(1 for kw in fixture.expected_keywords if kw.lower() in text)
            kw_score = (matched / len(fixture.expected_keywords)) * 100.0
            criteria_scores["expected_keywords"] = kw_score
            if kw_score < 100.0:
                missing = [kw for kw in fixture.expected_keywords if kw.lower() not in text]
                reasons.append(f"Missing expected keywords: {missing}")

        # 2. Forbidden keywords check (hallucination / safety)
        if fixture.forbidden_keywords:
            violations = [kw for kw in fixture.forbidden_keywords if kw.lower() in text]
            forb_score = 100.0 if not violations else 0.0
            criteria_scores["forbidden_keywords"] = forb_score
            if violations:
                reasons.append(f"Found forbidden keywords: {violations}")

        # 3. Length compliance
        char_len = len(ray.output_text)
        if fixture.min_length <= char_len <= fixture.max_length:
            len_score = 100.0
        else:
            len_score = 50.0
            reasons.append(
                f"Length {char_len} out of bounds [{fixture.min_length}, {fixture.max_length}]"
            )
        criteria_scores["length_compliance"] = len_score

        # Overall average score across criteria
        overall = sum(criteria_scores.values()) / len(criteria_scores) if criteria_scores else 100.0

        no_forbidden = (
            "forbidden_keywords" not in criteria_scores
            or criteria_scores["forbidden_keywords"] == 100.0
        )
        passed = overall >= 70.0 and no_forbidden

        return EvaluationScore(
            ray_id=ray.ray_id,
            model_id=ray.model_id,
            overall_score=round(overall, 2),
            criteria_scores={k: round(v, 2) for k, v in criteria_scores.items()},
            passed=passed,
            reasoning="; ".join(reasons) if reasons else "Passed all criteria",
        )


class BeamTestHarness:
    """Scatter-Gather Multi-Model Test Harness for Huible."""

    def __init__(
        self,
        providers: list[LLMProvider],
        evaluator: CompareEvaluator | None = None,
        max_parallel_rays: int = 16,
    ) -> None:
        self.providers = {p.vendor_id: p for p in providers}
        self.evaluator = evaluator or CompareEvaluator()
        self.max_parallel_rays = max_parallel_rays

    async def _execute_ray(
        self,
        provider: LLMProvider,
        model_info: ModelInfo,
        fixture: TestFixture,
        semaphore: asyncio.Semaphore,
    ) -> RayResult:
        ray_id = str(uuid4())
        tokens_in = len((fixture.system_prompt + fixture.user_prompt).split()) * 2
        start_time = time.perf_counter()

        async with semaphore:
            try:
                output = await provider.chat_generate(
                    model_id=model_info.model_id,
                    system_prompt=fixture.system_prompt,
                    user_prompt=fixture.user_prompt,
                )
                elapsed_ms = (time.perf_counter() - start_time) * 1000.0
                tokens_out = len(output.split()) * 2
                cost = model_info.pricing.calculate_cost_cents(tokens_in, tokens_out)

                return RayResult(
                    ray_id=ray_id,
                    fixture_id=fixture.id,
                    model_id=model_info.model_id,
                    vendor_id=provider.vendor_id,
                    status=RayStatus.SUCCESS,
                    output_text=output,
                    latency_ms=round(elapsed_ms, 2),
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    cost_cents=cost,
                )
            except Exception as err:
                elapsed_ms = (time.perf_counter() - start_time) * 1000.0
                return RayResult(
                    ray_id=ray_id,
                    fixture_id=fixture.id,
                    model_id=model_info.model_id,
                    vendor_id=provider.vendor_id,
                    status=RayStatus.ERROR,
                    latency_ms=round(elapsed_ms, 2),
                    error_message=str(err),
                )

    async def run_eval(
        self,
        fixtures: list[TestFixture],
        model_targets: list[tuple[str, ModelInfo]],  # (vendor_id, ModelInfo)
    ) -> EvalReport:
        eval_id = str(uuid4())
        semaphore = asyncio.Semaphore(self.max_parallel_rays)
        start_total = time.perf_counter()

        # 1. Scatter Phase: Fan-out N fixtures x M models in parallel
        tasks = []
        for fixture in fixtures:
            for vendor_id, model_info in model_targets:
                provider = self.providers.get(vendor_id)
                if not provider:
                    continue
                tasks.append(
                    self._execute_ray(provider, model_info, fixture, semaphore)
                )

        ray_results: list[RayResult] = await asyncio.gather(*tasks)

        # 2. Gather / Fusion Phase: Score each ray against fixture
        eval_scores: list[EvaluationScore] = []
        fixture_map = {f.id: f for f in fixtures}
        for ray in ray_results:
            fixture = fixture_map[ray.fixture_id]
            score = self.evaluator.evaluate(fixture, ray)
            eval_scores.append(score)

        # 3. Aggregate Summaries per Model
        scores_by_model: dict[str, list[EvaluationScore]] = {}
        rays_by_model: dict[str, list[RayResult]] = {}
        for ray, score in zip(ray_results, eval_scores, strict=True):
            scores_by_model.setdefault(ray.model_id, []).append(score)
            rays_by_model.setdefault(ray.model_id, []).append(ray)

        model_summaries: list[ModelSummary] = []
        for model_id, scores in scores_by_model.items():
            rays = rays_by_model[model_id]
            total_runs = len(scores)
            passed_runs = sum(1 for s in scores if s.passed)
            avg_score = sum(s.overall_score for s in scores) / total_runs if total_runs else 0.0
            avg_latency = sum(r.latency_ms for r in rays) / total_runs if total_runs else 0.0
            total_cost = sum(r.cost_cents for r in rays)

            model_summaries.append(
                ModelSummary(
                    model_id=model_id,
                    total_runs=total_runs,
                    passed_runs=passed_runs,
                    pass_rate=round(passed_runs / total_runs if total_runs else 0.0, 4),
                    avg_score=round(avg_score, 2),
                    avg_latency_ms=round(avg_latency, 2),
                    total_cost_cents=round(total_cost, 6),
                )
            )

        # Rank summaries by avg_score descending
        model_summaries.sort(key=lambda m: m.avg_score, reverse=True)

        total_elapsed = (time.perf_counter() - start_total) * 1000.0
        total_cost = sum(r.cost_cents for r in ray_results)

        return EvalReport(
            eval_id=eval_id,
            fixture_count=len(fixtures),
            model_summaries=model_summaries,
            ray_results=ray_results,
            eval_scores=eval_scores,
            total_cost_cents=round(total_cost, 6),
            total_elapsed_ms=round(total_elapsed, 2),
        )
