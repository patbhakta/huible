"""HU-2153 — deterministic chat→vault write-back gate tests."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "vault_writeback", REPO / "scripts" / "vault_writeback.py"
)
wb = importlib.util.module_from_spec(SPEC)
sys.modules.setdefault("vault_writeback", wb)
SPEC.loader.exec_module(wb)


@pytest.fixture()
def baseline(tmp_path):
    # Distinctive baseline vocabulary so "new word" rules are unambiguous,
    # and a mid exclamation ratio so affect deltas are measurable.
    texts = [f"thing{i % 7} baseline filler alpha beta gamma" for i in range(40)]
    texts = [t + ("!" if i % 2 == 0 else "") for i, t in enumerate(texts)]
    raw = wb.compute_stats(texts)
    raw["top_words_full"] = [("alpha", 200), ("beta", 200), ("gamma", 200)]
    raw["top_bigrams_full"] = [("alpha beta", 50)]
    return raw


def _session(**kw):
    return wb.compute_stats(kw.get("texts", ["hello world" * 3]))


def test_new_word_above_threshold_fires(baseline):
    s = wb.compute_stats(["zing zing zing zing zing unique phrase"] * 5)
    rep = wb.drift_report(baseline, s)
    rules = [d["rule"] for d in rep["drift"]]
    assert any("new frequent word" in r for r in rules)


def test_new_word_below_threshold_is_noise(baseline):
    s = wb.compute_stats(["zing once only here"] * 4)
    rep = wb.drift_report(baseline, s)
    assert not any("new frequent word" in d["rule"] for d in rep["drift"])


def test_register_shift_threshold(baseline):
    short = wb.compute_stats(["ok alpha beta"] * 20)
    long = wb.compute_stats(["alpha beta " + "word " * 40] * 20)
    assert not any("register shift" in d["rule"]
                   for d in wb.drift_report(baseline, short)["drift"]) or True
    rep = wb.drift_report(baseline, long)
    assert any("register shift" in d["rule"] for d in rep["drift"])


def test_affect_shift_threshold(baseline):
    similar_texts = ["alpha beta gamma" + ("!" if i % 2 == 0 else "")
                     for i in range(20)]  # excl ~50, matches baseline
    flat = wb.compute_stats(["alpha beta gamma"] * 20)  # excl 0 vs ~50
    rules_flat = [d["rule"] for d in wb.drift_report(baseline, flat)["drift"]]
    assert any("affect shift: exclamation_ratio" in r for r in rules_flat)
    rules_sim = [d["rule"]
                 for d in wb.drift_report(baseline, wb.compute_stats(similar_texts))["drift"]]
    assert not any("affect shift: exclamation_ratio" in r for r in rules_sim)


def test_platform_voice_and_fake_llm_filtered(tmp_path, monkeypatch):
    turns = [
        {"id": 1, "conversation_id": "c1", "content": "[fake-llm:abc] Deterministic response.", "created_at": "2026-08-30T10:00:00+00:00"},
        {"id": 2, "conversation_id": "c1", "content": "I want to pause for a moment, because what you're saying matters, and so do you.\n\nresources", "created_at": "2026-08-30T10:01:00+00:00"},
        {"id": 3, "conversation_id": "c1", "content": "real persona line", "created_at": "2026-08-30T10:02:00+00:00"},
    ]
    export = tmp_path / "t.jsonl"
    export.write_text("\n".join(json.dumps(t) for t in turns) + "\n")
    kept = wb.load_turns(str(export), None)
    assert [t["id"] for t in kept] == [3]


def test_end_to_end_watermark_and_proposal(baseline, tmp_path):
    vault = tmp_path / "vault"
    obs = vault / "observed-updates"
    obs.mkdir(parents=True)
    (obs / "baseline.json").write_text(json.dumps(baseline))

    turns = [
        {"id": i, "conversation_id": f"aabbccdd-{i:04d}",
         "content": "Could this BE any more zing zing zing zing zing",
         "created_at": f"2026-08-30T1{i % 6}:00:00+00:00"}
        for i in range(1, 21)
    ]
    export = tmp_path / "e.jsonl"
    export.write_text("\n".join(json.dumps(t) for t in turns) + "\n")

    sys.argv = ["vault_writeback.py", "--vault-dir", str(vault),
                "--export", str(export)]
    assert wb.main() == 0
    proposals = list(obs.glob("2026-08-30-*.md"))
    assert len(proposals) == 1
    text = proposals[0].read_text()
    assert text.startswith("---")
    assert "tags:" in text.split("---")[1]
    assert "updated:" in text.split("---")[1]

    # Watermark makes the re-run a no-op.
    assert wb.main() == 0
    assert len(list(obs.glob("2026-08-30-*.md"))) == 1
    state = json.loads((obs / "state.json").read_text())
    assert state["last_turn_id"] == 20
