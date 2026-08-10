"""Tests for the multimodal audio ingestion module (BHAA-1375).

Covers:
- ``modules/onboarding/audio.py`` — MELD CSV + generic JSONL ingestion,
  persona-level vocal profile aggregation, cleaned-dialog augmentation.
- ``huible.distillation.cli._parse_record_line`` — per-utterance acoustic
  features are carried into ``L0Record.metadata['acoustic']`` (backward
  compatible: text-only entries keep the original metadata shape).
- ``huible.distillation.markdown.render_l0`` — compact acoustic summary is
  serialized into the L0 frontmatter when present, and omitted otherwise.
- ``modules/onboarding/structure`` — vocal/prosody facet rendering against the
  audio_features.json artifact (no network).

These mirror the acceptance criteria of BHAA-1375:
- a new onboarding stage ingests MELD audio feature vectors per utterance;
- L0Record metadata carries per-utterance acoustic features;
- structure.py OKF schema gains a vocal_patterns / prosody section;
- validated against a Chandler-filtered MELD subset fixture.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "onboarding"
MELD_CSV = FIXTURE_DIR / "chandler_meld_audio.csv"
CLEANED = FIXTURE_DIR / "chandler_cleaned.jsonl"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def audio_module():
    return _load_module(
        REPO_ROOT / "modules" / "onboarding" / "audio.py", "huible_onboarding_audio"
    )


@pytest.fixture
def structure_module():
    return _load_module(
        REPO_ROOT / "modules" / "onboarding" / "structure.py", "huible_onboarding_structure"
    )


# -- audio.py: MELD CSV ingestion -----------------------------------------

def test_meld_csv_persona_filter(audio_module):
    records = audio_module.ingest_meld_csv(MELD_CSV, "chandler")
    # 14 rows in fixture, 2 non-Chandler (Ross + Monica) → 12 Chandler rows.
    assert len(records) == 12
    for rec in records:
        assert rec["speaker"] == "chandler"
        assert isinstance(rec["acoustic"], dict)


def test_meld_csv_extracts_pitch_intensity_mfcc(audio_module):
    records = audio_module.ingest_meld_csv(MELD_CSV, "chandler")
    rec = records[0]
    # F0mean/F0std/F0range → pitch summary with mean/std/min/max.
    assert "pitch" in rec["acoustic"]
    assert rec["acoustic"]["pitch"]["mean"] == pytest.approx(142.3)
    # Intensitymean → intensity summary.
    assert "intensity" in rec["acoustic"]
    # mfcc_* → averaged mfcc_mean scalar.
    assert "mfcc_mean" in rec["acoustic"]
    # Emotion label captured when present.
    assert rec["emotion"] == "neutral"
    # Utterance id synthesized from Dialogue_ID + Utterance_ID.
    assert rec["utt_id"] == "dia1_utt0"


def test_meld_csv_carries_text_column(audio_module):
    records = audio_module.ingest_meld_csv(MELD_CSV, "chandler")
    assert records[0]["text"] == "Could that report BE any later?"


# -- audio.py: persona-level aggregation ----------------------------------

def test_aggregate_persona_profile(audio_module):
    records = audio_module.ingest_meld_csv(MELD_CSV, "chandler")
    profile = audio_module.aggregate_persona_profile(records, "chandler")
    assert profile["available"] is True
    assert profile["persona"] == "chandler"
    assert profile["utterance_count"] == 12
    assert profile["pitch"]["mean"] > 0
    valid_emotions = {
        "neutral", "joy", "non-neutral", "fear", "disgust", "sarcastic",
    }
    assert profile["dominant_emotion"] in valid_emotions
    # Emotion distribution sums to ~1.0 over labeled utterances.
    total = sum(profile["emotion_distribution"].values())
    assert total == pytest.approx(1.0, abs=0.01)
    assert "mean pitch" in profile["prosody_summary"]


def test_aggregate_persona_profile_empty(audio_module):
    profile = audio_module.aggregate_persona_profile([], "chandler")
    assert profile["available"] is False
    assert profile["utterance_count"] == 0
    assert profile["prosody_summary"] == "No acoustic data available."


# -- audio.py: full run() + cleaned augmentation --------------------------

def test_run_writes_audio_features_json(audio_module, tmp_path):
    out = tmp_path / "audio_features.json"
    artifact = audio_module.run(str(MELD_CSV), "chandler", str(CLEANED), str(out))
    assert out.exists()
    assert artifact["source_kind"] == "meld_csv"
    assert artifact["utterance_count"] == 12
    loaded = json.loads(out.read_text())
    assert loaded["persona_profile"]["available"] is True
    assert len(loaded["per_utterance"]) == 12


def test_run_augments_cleaned_jsonl(audio_module, tmp_path):
    out = tmp_path / "audio_features.json"
    aug = tmp_path / "cleaned_with_audio.jsonl"
    artifact = audio_module.run(
        str(MELD_CSV), "chandler", str(CLEANED), str(out), augment_output=str(aug)
    )
    # All 12 cleaned lines match Chandler utterances by text → 12 augmented.
    assert artifact["augmented_matched"] == 12
    assert aug.exists()
    aug_lines = [json.loads(line) for line in aug.read_text().splitlines() if line.strip()]
    assert len(aug_lines) == 12
    # Every augmented line carries an acoustic payload with pitch evidence.
    for entry in aug_lines:
        assert "acoustic" in entry
        assert "pitch" in entry["acoustic"]


def test_run_handles_missing_features(audio_module, tmp_path):
    out = tmp_path / "audio_features.json"
    with pytest.raises(FileNotFoundError):
        audio_module.run("/does/not/exist.csv", "chandler", None, str(out))


# -- audio.py: generic per-utterance JSONL ingestion -----------------------

def test_ingest_generic_audio_jsonl(audio_module, tmp_path):
    jsonl = tmp_path / "audio.jsonl"
    jsonl.write_text(
        json.dumps({"speaker": "Chandler", "text": "Hello there.", "acoustic": {
            "pitch": 140.0, "intensity": 0.4, "emotion": "neutral",
            "mfcc": [1.0, 2.0, 3.0],
        }}) + "\n"
        + json.dumps({"speaker": "Ross", "text": "Hi.", "acoustic": {"pitch": 110.0}}) + "\n"
    )
    records = audio_module.ingest_audio_jsonl(jsonl, "chandler")
    assert len(records) == 1
    rec = records[0]
    assert rec["speaker"] == "chandler"
    assert rec["acoustic"]["pitch"]["mean"] == 140.0
    assert rec["acoustic"]["mfcc_mean"] == pytest.approx(2.0)


# -- distillation CLI: L0Record acoustic metadata carry-through ------------

def test_parse_record_line_carries_acoustic():
    from huible.distillation import cli as distill_cli

    entry = {
        "text": "Could that report BE any later?",
        "speaker": "chandler",
        "source": "friends.csv",
        "acoustic": {"pitch": {"mean": 142.3}, "emotion": "neutral"},
    }
    record = distill_cli._parse_record_line(entry, "chandler", 0)
    assert record is not None
    assert record.metadata["acoustic"] == {"pitch": {"mean": 142.3}, "emotion": "neutral"}
    # Standard metadata keys still present.
    assert record.metadata["speaker"] == "chandler"
    assert record.metadata["emotion"] is None


def test_parse_record_line_backward_compatible_without_acoustic():
    """Text-only entries keep the original metadata shape (no acoustic key)."""
    from huible.distillation import cli as distill_cli

    entry = {"text": "Hello.", "speaker": "chandler", "source": "friends.csv"}
    record = distill_cli._parse_record_line(entry, "chandler", 0)
    assert "acoustic" not in record.metadata
    assert record.metadata["speaker"] == "chandler"


# -- distillation markdown: L0 acoustic frontmatter summary ---------------

def test_render_l0_emits_acoustic_summary_when_present():
    from huible.distillation import L0Record, parse_frontmatter, render_l0

    record = L0Record(
        id="x",
        kind="conversation",
        content="chandler: Hi.",
        metadata={"acoustic": {"pitch": {"mean": 142.3}, "emotion": "neutral"}},
    )
    text = render_l0(record)
    fm, _ = parse_frontmatter(text)
    assert "acoustic" in fm
    assert "F0=142.3" in fm["acoustic"]
    assert "emo=neutral" in fm["acoustic"]


def test_render_l0_omits_acoustic_for_text_only():
    """Text-only L0 records render exactly as before (no acoustic field)."""
    from huible.distillation import L0Record, parse_frontmatter, render_l0

    record = L0Record(id="x", kind="conversation", content="chandler: Hi.", metadata={})
    text = render_l0(record)
    fm, _ = parse_frontmatter(text)
    assert "acoustic" not in fm


# -- distillation end-to-end: acoustic survives into the L0 store ----------

@pytest.mark.asyncio
async def test_acoustic_flows_through_distillation(audio_module, tmp_path):
    """Augmented cleaned.jsonl → distillation → L0 raw records carry acoustic summary."""
    from huible.distillation import MarkdownMemoryStore
    from huible.distillation import cli as distill_cli

    out = tmp_path / "audio_features.json"
    aug = tmp_path / "cleaned_with_audio.jsonl"
    audio_module.run(str(MELD_CSV), "chandler", str(CLEANED), str(out), augment_output=str(aug))

    mem_dir = tmp_path / "memory"
    await distill_cli.run(
        input_path=str(aug),
        stats_path=None,
        persona="chandler",
        out_dir=str(mem_dir),
        strict=True,
        use_llm=False,
        model="x",
        max_records=None,
    )
    store = MarkdownMemoryStore(mem_dir)
    raw_records = store.list_records(__import__("huible.distillation", fromlist=["Tier"]).Tier.L0)
    # At least one L0 raw record carries an acoustic frontmatter summary.
    acoustic_count = sum(1 for r in raw_records if r.get("acoustic"))
    assert acoustic_count > 0, "no L0 raw record carried an acoustic summary"


# -- structure.py: vocal/prosody facet rendering (no network) --------------

def test_structure_load_audio_profile(audio_module, structure_module, tmp_path):
    out = tmp_path / "audio_features.json"
    audio_module.run(str(MELD_CSV), "chandler", str(CLEANED), str(out))
    profile = structure_module.load_audio_profile(str(out))
    assert profile is not None
    assert profile["available"] is True
    assert profile["utterance_count"] == 12
    assert "mean pitch" in profile["prosody_summary"]


def test_structure_load_audio_profile_missing(structure_module):
    assert structure_module.load_audio_profile(None) is None
    assert structure_module.load_audio_profile("/does/not/exist.json") is None


def test_structure_vocal_section_in_prompt(structure_module, tmp_path):
    """The grounded extraction prompt references the vocal/prosody facet."""
    prompt = structure_module.build_grounded_extraction_prompt(
        "chandler", "# brief", "", 12,
        audio_profile={"available": True, "utterance_count": 12,
                       "prosody_summary": "mean pitch 142.0 Hz.",
                       "pitch": {"mean": 142.0, "std": 15.0},
                       "intensity": {"mean": 0.4, "std": 0.1},
                       "emotion_distribution": {"neutral": 0.5}},
    )
    assert "Acoustic / prosodic grounding" in prompt
    assert "vocal_patterns" in prompt
    assert "mean pitch 142.0 Hz" in prompt


def test_structure_vocal_section_in_prompt_text_only(structure_module):
    """Text-only onboarding still includes the vocal_patterns schema slot."""
    prompt = structure_module.build_grounded_extraction_prompt(
        "chandler", "# brief", "", 12, audio_profile=None
    )
    assert "vocal_patterns" in prompt
    assert "No acoustic data available" in prompt


def test_structure_writes_vocal_patterns_section(structure_module, tmp_path):
    """The persona-profile.md gains a Vocal Patterns & Prosody section."""
    extraction = {
        "identity": {"communication_style": "sarcastic", "core_traits": ["witty"]},
        "speech_patterns": {},
        "vocal_patterns": {
            "prosody": "rising inflection, sarcastic drawl",
            "pitch_tendency": "high variability",
            "dominant_vocal_emotion": "non-neutral",
            "vocal_markers": ["stress on BE", "trailing rise"],
        },
        "relationships": {},
        "memories": {},
    }
    audio_profile = {
        "available": True, "utterance_count": 12,
        "prosody_summary": "mean pitch 142.0 Hz.",
        "pitch": {"mean": 142.0}, "intensity": {"mean": 0.4},
        "emotion_distribution": {"neutral": 0.5}, "dominant_emotion": "neutral",
        "source": "chandler_meld_audio.csv",
    }
    docs = structure_module.write_okf_docs(
        str(tmp_path), "chandler", extraction, 12, {"l3_profiles": [], "l2_scenarios": [],
                                                    "l1_facts": [], "source": "mem"},
        audio_profile,
    )
    assert docs
    profile_md = (tmp_path / "persona-profile.md").read_text()
    assert "## Vocal Patterns & Prosody" in profile_md
    assert "sarcastic drawl" in profile_md
    assert "stress on BE" in profile_md
    # Multimodal source cited in frontmatter.
    assert "acoustic-features" in profile_md


def test_structure_writes_vocal_patterns_section_text_only(structure_module, tmp_path):
    """Text-only onboarding still renders the vocal section as a gap."""
    extraction = {
        "identity": {}, "speech_patterns": {},
        "vocal_patterns": {},
        "relationships": {}, "memories": {},
    }
    structure_module.write_okf_docs(
        str(tmp_path), "chandler", extraction, 12,
        {"l3_profiles": [], "l2_scenarios": [], "l1_facts": [], "source": "mem"},
        audio_profile=None,
    )
    profile_md = (tmp_path / "persona-profile.md").read_text()
    assert "## Vocal Patterns & Prosody" in profile_md
    assert "Not enough data to determine." in profile_md
    # No multimodal source cited when no audio.
    assert "acoustic-features" not in profile_md
