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


# -- BHAA-1364: semantic L3 + identity anchor --------------------------------

_SEMANTIC_DURABLE_PREDICATES = {
    "likes", "avoids", "habit", "tends", "favorite",
}


def test_distill_extract_preference_is_high_precision():
    """The hardened extractor only emits genuine persona-voiced preferences.

    Mid-sentence keyword hits and verbatim fragments must NOT become durable
    preferences (the BHAA-1361 noise: ``general:prefers: "no bunny at all!!!"``).
    """
    from huible.distillation.distill import _extract_preference

    # Genuine persona-voiced preferences → semantic predicate + clean object.
    assert _extract_preference("I love earl grey tea") == ("likes", "earl grey tea")
    assert _extract_preference("I never watch golf") == ("avoids", "watch golf")
    assert _extract_preference("I always use humor as a defense mechanism") == (
        "habit",
        "use humor as a defense mechanism",
    )
    assert _extract_preference("chandler: I prefer sarcasm over sincerity") == (
        "likes",
        "sarcasm over sincerity",
    )
    assert _extract_preference("I hate being left out of things") == (
        "avoids",
        "being left out of things",
    )

    # Noise that previously became verbatim ``prefers`` fragments → now None.
    assert _extract_preference("I've never seen one of his plays before") is None
    assert _extract_preference("could you look down in the shower?") is None
    assert _extract_preference("'would've'") is None
    assert _extract_preference("he likes the new intern") is None  # other-subject
    assert _extract_preference("she never called back") is None


@pytest.mark.asyncio
async def test_distill_l3_durable_rules_are_semantic_not_verbatim(tmp_path):
    """Every durable_rule L3 profile has a semantic predicate and a clean rule."""
    from huible.distillation import MarkdownMemoryStore, Tier
    from huible.distillation import cli as distill_cli

    out_dir = tmp_path / "memory"
    await distill_cli.run(
        input_path=str(FIXTURE), stats_path=None, persona="chandler",
        out_dir=str(out_dir), strict=True, use_llm=False, model="x", max_records=None,
    )

    store = MarkdownMemoryStore(out_dir)
    profiles = store.list_records(Tier.L3)
    durables = [p for p in profiles if p.get("memory_type") == "durable_rule"]
    assert durables, "fixture should yield at least one durable rule"
    for p in durables:
        key = p.get("key", "")
        predicate = key.split(":", 1)[1] if ":" in key else ""
        assert predicate in _SEMANTIC_DURABLE_PREDICATES, (
            f"non-semantic durable predicate: {key}"
        )
        rule = p.get("_body", "").strip()
        # No verbatim quote fragments (the old noise signature).
        assert not (rule.startswith("'") and rule.endswith("'")), f"verbatim fragment: {rule!r}"
        assert not (rule.startswith('"') and rule.endswith('"')), f"verbatim fragment: {rule!r}"
        assert "?" not in rule, f"question fragment promoted to rule: {rule!r}"
        assert len(rule) >= 3


@pytest.mark.asyncio
async def test_distill_evidence_coverage_stays_100_pct(tmp_path):
    """The hardening preserves the gap-safe invariant: every record is cited."""
    from huible.distillation import MarkdownMemoryStore, Tier
    from huible.distillation import cli as distill_cli

    out_dir = tmp_path / "memory"
    manifest = await distill_cli.run(
        input_path=str(FIXTURE), stats_path=None, persona="chandler",
        out_dir=str(out_dir), strict=True, use_llm=False, model="x", max_records=None,
    )
    assert manifest["all_records_have_evidence"] is True
    store = MarkdownMemoryStore(out_dir)
    for tier in (Tier.L1, Tier.L2, Tier.L3):
        for rec in store.list_records(tier):
            source = str(rec.get("source") or rec.get("evidence_sources") or "")
            assert source, f"{tier.value} record missing evidence: {rec}"


@pytest.mark.asyncio
async def test_structure_brief_leads_with_identity_anchor(tmp_path, structure_module):
    """The grounded brief opens with an identity anchor (BHAA-1364 item 2).

    The anchor must name the persona and explicitly mark OTHER mentioned
    characters as not-the-persona so structuring models do not drift.
    """
    from huible.distillation import cli as distill_cli

    out_dir = tmp_path / "memory"
    await distill_cli.run(
        input_path=str(FIXTURE), stats_path=None, persona="chandler",
        out_dir=str(out_dir), strict=True, use_llm=False, model="x", max_records=None,
    )

    memory = structure_module.load_memory_context(str(out_dir))
    brief = structure_module.render_memory_brief(memory, "chandler")

    # The Identity anchor section precedes the Durable rules section.
    assert "## Identity anchor" in brief
    assert "## Durable rules" in brief
    assert brief.index("## Identity anchor") < brief.index("## Durable rules")
    # The persona is foregrounded.
    assert "describes **chandler**" in brief
    # Mentioned characters are surfaced as OTHER entities (counter drift).
    assert "NOT chandler" in brief
    assert "Joey" in brief  # fixture has properly-cased "Joey is my best friend"


def test_structure_identity_anchor_mines_lowercased_corpus(structure_module):
    """The frequency fallback surfaces mentioned names on lowercased corpora."""
    memory = {
        "l1_facts": [
            {"_body": "joey is my best friend and roommate"},
            {"_body": "i told joey about it"},
            {"_body": "i told joey again"},
            {"_body": "monica is my friend who cooks"},
            {"_body": "monica cooked dinner"},
            {"_body": "monica made food"},
            {"_body": "ross and rachel came over"},
            {"_body": "ross stopped by"},
            {"_body": "ross left early"},
            {"_body": "rachel called twice"},
            {"_body": "rachel visited"},
            {"_body": "rachel left"},
            {"_body": "phoebe sang a song"},
            {"_body": "phoebe played guitar"},
            {"_body": "phoebe smiled"},
        ],
        "l2_scenarios": [],
        "l3_profiles": [],
    }
    others = structure_module._mine_other_named_entities(memory, "chandler")
    lowered = [o.lower() for o in others]
    for name in ("joey", "monica", "ross", "rachel", "phoebe"):
        assert name in lowered, f"{name} not surfaced as other entity: {others}"

    anchor = structure_module.build_identity_anchor(memory, "chandler")
    joined = " ".join(anchor).lower()
    assert "chandler" in joined
    assert "not chandler" in joined
