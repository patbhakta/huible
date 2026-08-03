from __future__ import annotations

from huible.ingestion.pipeline import _compute_quarantine_priority
from huible.memory.protocol import QuarantinePriority
from tests.f2.conftest import PERSONA_ID


class TestF2_PipelineIntegration:
    """F2 end-to-end: full five-gate pipeline processes candidates correctly."""

    async def test_all_gates_pass_accepted(self, full_pipeline, clean_candidate):
        result = await full_pipeline.process(clean_candidate, persona_id=PERSONA_ID)
        assert result.accepted is True
        assert result.rejected is False
        assert result.quarantined is False
        assert result.memory is not None
        assert len(result.audit_trail) == 5

    async def test_safety_fail_early_reject(self, full_pipeline, injection_candidate):
        result = await full_pipeline.process(injection_candidate, persona_id=PERSONA_ID)
        assert result.rejected is True
        assert result.gate == "safety"
        assert len(result.audit_trail) == 1

    async def test_audit_trail_records_all_gates(self, full_pipeline):
        result = await full_pipeline.process(
            {"content": "Dad went fishing every summer", "embedding_content": [0.1] * 10},
            persona_id=PERSONA_ID,
        )
        gate_names = [a.gate_name for a in result.audit_trail]
        assert gate_names == ["safety", "deduplication", "novelty", "immutability", "pertinence"]

    async def test_safety_is_critical_priority(self):
        assert _compute_quarantine_priority(["safety"]) == QuarantinePriority.CRITICAL

    async def test_immutability_is_high_priority(self):
        assert _compute_quarantine_priority(["immutability"]) == QuarantinePriority.HIGH

    async def test_novelty_is_medium_priority(self):
        assert _compute_quarantine_priority(["novelty"]) == QuarantinePriority.MEDIUM

    async def test_pertinence_is_low_priority(self):
        assert _compute_quarantine_priority(["pertinence"]) == QuarantinePriority.LOW

    async def test_multiple_gates_takes_highest(self):
        assert _compute_quarantine_priority(["pertinence", "safety"]) == QuarantinePriority.CRITICAL
