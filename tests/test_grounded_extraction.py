"""Tests for the grounded extraction wiring (BHAA-1360).

Covers:
- ``huible.distillation.cli`` — thin CLI wrapper producing L0-L3 Markdown with
  ``EvidenceLink`` citations and a strict gap-safe mode.
- ``modules/onboarding.gaps`` — structured gap list from the memory store.
- ``modules.onboarding.structure`` — grounded memory brief rendering (no network).

These mirror the acceptance criteria of BHAA-1360:
- cli runs against fixtures and produces L0-L3 Markdown with EvidenceLink citations.
- gaps.py emits a structured gap list.
- every L1/L2/L3 record carries an EvidenceLink back to its L0 source.
- --strict mode does not hallucinate (deterministic path; no LLM key in tests).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "onboarding" / "chandler_cleaned.jsonl"


# -- distillation CLI ------------------------------------------------------

@pytest.mark.asyncio
async def test_cli_distills_fixture_to_l0_l3_markdown(tmp_path):
    from huible.distillation import cli as distill_cli

    out_dir = tmp_path / "memory"
    manifest = await distill_cli.run(
        input_path=str(FIXTURE),
        stats_path=None,
        persona="chandler",
        out_dir=str(out_dir),
        strict=True,
        use_llm=False,
        model="google/gemini-3-flash-preview",
        max_records=None,
    )

    # Manifest reflects the run.
    assert manifest["persona"] == "chandler"
    assert manifest["strict"] is True
    assert manifest["used_llm"] is False
    counts = manifest["counts"]
    assert counts["L0_raw"] > 0
    assert counts["L1_facts"] >= 1
    assert counts["L2_scenarios"] >= 1
    assert counts["L3_profiles"] >= 1

    # L0-L3 Markdown tiers were written.
    for sub in ("raw", "facts", "scenarios", "profiles"):
        files = list((out_dir / sub).glob("*.md"))
        assert files, f"no markdown written for tier dir {sub}"


@pytest.mark.asyncio
async def test_cli_every_record_carries_evidence_link(tmp_path):
    from huible.distillation import MarkdownMemoryStore, Tier
    from huible.distillation import cli as distill_cli

    out_dir = tmp_path / "memory"
    await distill_cli.run(
        input_path=str(FIXTURE),
        stats_path=None,
        persona="chandler",
        out_dir=str(out_dir),
        strict=True,
        use_llm=False,
        model="google/gemini-3-flash-preview",
        max_records=None,
    )

    store = MarkdownMemoryStore(out_dir)
    # Every L1/L2/L3 record must have a non-empty `source` evidence pointer back
    # to its L0 raw record.
    for tier in (Tier.L1, Tier.L2, Tier.L3):
        records = store.list_records(tier)
        assert records, f"no {tier.value} records"
        for rec in records:
            source = str(rec.get("source") or rec.get("evidence_sources") or "")
            assert source, f"{tier.value} record missing evidence source: {rec}"


@pytest.mark.asyncio
async def test_cli_strict_with_no_key_is_deterministic_and_gap_safe(tmp_path, monkeypatch):
    from huible.distillation import cli as distill_cli

    # No API key → must fall back to deterministic extractor (never hallucinate).
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    out_dir = tmp_path / "memory"
    manifest = await distill_cli.run(
        input_path=str(FIXTURE),
        stats_path=None,
        persona="chandler",
        out_dir=str(out_dir),
        strict=True,
        use_llm=True,  # requested, but no key → deterministic fallback
        model="google/gemini-3-flash-preview",
        max_records=None,
    )
    assert manifest["used_llm"] is False
    # Deterministic extractor only emits evidenced facts.
    assert manifest["counts"]["L1_facts"] >= 1


@pytest.mark.asyncio
async def test_cli_consumes_stats_anchor(tmp_path):
    from huible.distillation import cli as distill_cli

    stats_path = tmp_path / "stats.json"
    stats_path.write_text(json.dumps({"top_words": [["sarcasm", 3]], "total_lines": 12}))
    out_dir = tmp_path / "memory"
    manifest = await distill_cli.run(
        input_path=str(FIXTURE),
        stats_path=str(stats_path),
        persona="chandler",
        out_dir=str(out_dir),
        strict=True,
        use_llm=False,
        model="google/gemini-3-flash-preview",
        max_records=None,
    )
    assert manifest["stats_anchor_present"] is True
    assert manifest["stats_source"].endswith("stats.json")


@pytest.mark.asyncio
async def test_cli_records_have_stable_deterministic_ids(tmp_path):
    """Re-running on the same corpus yields identical record ids (audit/gap loop)."""
    from huible.distillation import cli as distill_cli

    out1 = tmp_path / "m1"
    out2 = tmp_path / "m2"
    await distill_cli.run(
        input_path=str(FIXTURE), stats_path=None, persona="chandler",
        out_dir=str(out1), strict=True, use_llm=False,
        model="x", max_records=None,
    )
    await distill_cli.run(
        input_path=str(FIXTURE), stats_path=None, persona="chandler",
        out_dir=str(out2), strict=True, use_llm=False,
        model="x", max_records=None,
    )
    ids1 = sorted(p.name for p in (out1 / "raw").glob("*.md"))
    ids2 = sorted(p.name for p in (out2 / "raw").glob("*.md"))
    assert ids1 == ids2 and ids1


# -- gaps.py ---------------------------------------------------------------

def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def gaps_module():
    return _load_module(REPO_ROOT / "modules" / "onboarding" / "gaps.py", "huible_onboarding_gaps")


@pytest.fixture
def structure_module():
    return _load_module(
        REPO_ROOT / "modules" / "onboarding" / "structure.py", "huible_onboarding_structure"
    )


@pytest.mark.asyncio
async def test_gaps_emits_structured_gap_list(tmp_path, gaps_module):
    from huible.distillation import cli as distill_cli

    out_dir = tmp_path / "memory"
    await distill_cli.run(
        input_path=str(FIXTURE), stats_path=None, persona="chandler",
        out_dir=str(out_dir), strict=True, use_llm=False, model="x", max_records=None,
    )

    report = gaps_module.build_gap_report(str(out_dir), "chandler")
    assert report["persona"] == "chandler"
    assert report["total_records"] > 0
    assert isinstance(report["gaps"], list)
    # Every gap entry is well-formed.
    for gap in report["gaps"]:
        assert gap["status"] in ("missing", "weak")
        assert gap["facet"]
        assert gap["suggested_question"]
    # Coverage table covers all facets.
    for facet in gaps_module.FACETS:
        assert facet["id"] in report["coverage"]


@pytest.mark.asyncio
async def test_gaps_flags_missing_facets(tmp_path, gaps_module):
    """An empty-ish memory store (no durable rules for some facets) reports gaps."""
    from huible.distillation import cli as distill_cli

    out_dir = tmp_path / "memory"
    await distill_cli.run(
        input_path=str(FIXTURE), stats_path=None, persona="chandler",
        out_dir=str(out_dir), strict=True, use_llm=False, model="x", max_records=None,
    )

    report = gaps_module.build_gap_report(str(out_dir), "chandler")
    # Chandler corpus has no family/garden episodic detail → at least one gap.
    assert len(report["gaps"]) >= 1
    # Gap report is JSON-serializable for the Kestra output block.
    json.dumps(report)


# -- structure.py grounding (no network) -----------------------------------

@pytest.mark.asyncio
async def test_structure_builds_grounded_brief_from_memory(tmp_path, structure_module):
    from huible.distillation import cli as distill_cli

    out_dir = tmp_path / "memory"
    await distill_cli.run(
        input_path=str(FIXTURE), stats_path=None, persona="chandler",
        out_dir=str(out_dir), strict=True, use_llm=False, model="x", max_records=None,
    )

    memory = structure_module.load_memory_context(str(out_dir))
    assert memory["source"] == str(out_dir)
    assert len(memory["l3_profiles"]) >= 1
    assert len(memory["l2_scenarios"]) >= 1

    brief = structure_module.render_memory_brief(memory, "chandler")
    assert "Grounded memory brief for chandler" in brief
    assert "Durable rules" in brief
    assert "Current states" in brief


def test_structure_load_memory_context_handles_missing_dir(structure_module):
    memory = structure_module.load_memory_context("/does/not/exist")
    assert memory["l3_profiles"] == []
    assert memory["l2_scenarios"] == []
    assert memory["l1_facts"] == []


@pytest.mark.asyncio
async def test_structure_evidence_block_cites_sources(tmp_path, structure_module):
    from huible.distillation import cli as distill_cli

    out_dir = tmp_path / "memory"
    await distill_cli.run(
        input_path=str(FIXTURE), stats_path=None, persona="chandler",
        out_dir=str(out_dir), strict=True, use_llm=False, model="x", max_records=None,
    )
    memory = structure_module.load_memory_context(str(out_dir))
    block = structure_module._evidence_block(memory)
    assert "Grounding & evidence" in block
    assert "Evidence links" in block
