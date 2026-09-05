"""HU-2706 — v2 validation harness graders (offline, deterministic).

Covers the H1 M-0 violation graders, H2 per-class graders, and the H3
grounding-ledger classifier. No network, no generator calls.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.v2_harness.common import (  # noqa: E402
    followup_questions,
    markers_in,
    question_band,
)
from scripts.v2_harness.h1_m0_calibration import (  # noqa: E402
    M0_COLLECTED_VIOLATIONS,
    grade_m0,
)
from scripts.v2_harness.h2_ai_tell_probes import (  # noqa: E402
    ASSISTANT_SPEAK_MARKERS,
    grade_class_a,
    grade_class_b,
    grade_class_c,
    grade_class_d,
)
from scripts.v2_harness.h3_grounding_ledger import classify_turn  # noqa: E402

BASLINE = None


def _baselines():
    from scripts.v2_harness.common import measure_corpus_baselines

    global BASLINE
    if BASLINE is None:
        BASLINE = measure_corpus_baselines()
    return BASLINE


def _row(reply: str, *, probe: str = "T1", user: str = "hey", **over) -> dict:
    row = {
        "probe": probe,
        "user": user,
        "reply": reply,
        "chars": len(reply),
        "under_cap": len(reply) <= 400,
        "ai_selfref_hits": markers_in(reply, ["i'm a memory", "as an ai"]),
        "assistant_speak_hits": markers_in(reply, ASSISTANT_SPEAK_MARKERS),
        "code_hits": [],
        "followup_questions": followup_questions(reply),
        "caretaker_trace": None,
        "competence_wall": False,
    }
    row.update(over)
    return row


# ── H1: the boss's collected M-0 violations must be caught ────────────────


def test_h1_fixtures_are_the_collected_m0_violations():
    v = {x["class"]: x for x in M0_COLLECTED_VIOLATIONS["violations"]}
    assert v["m0_python_syntax_answer"]["violating_reply"].startswith("import sys")
    assert v["m0_fullname_self_intro"]["violating_reply"].startswith("Chandler Bing.")
    assert v["m0_fullname_self_intro"]["trigger_turn"] == 1


def test_h1_catches_fullname_self_intro():
    replies = ["The one and only Chandler Bing. Okay, one of eight Friends."] + ["ok"] * 16
    checks = grade_m0(replies, 400)
    assert checks["m0_fullname_self_intro"]["reproduced"] is True
    assert checks["m0_fullname_self_intro"]["pass"] is False


def test_h1_catches_python_syntax_answer():
    replies = ["hey"] * 10 + ['import sys; sys.stdout.write("hi")'] + ["ok"] * 6
    checks = grade_m0(replies, 400)
    assert checks["m0_python_syntax_answer"]["reproduced"] is True
    assert 11 in checks["m0_python_syntax_answer"]["hits_by_turn"]


def test_h1_catches_reply_length_violation():
    replies = ["ok"] * 16 + ["y" * 401]
    checks = grade_m0(replies, 400)
    assert checks["m0_reply_length_violation"]["over_cap_turns"] == [17]
    assert checks["m0_reply_length_violation"]["pass"] is False


def test_h1_clean_transcript_is_green():
    replies = ["Just a guy who loves his foosball table." for _ in range(17)]
    checks = grade_m0(replies, 400)
    assert all(c["pass"] for c in checks.values())


# ── H2: per-class graders ─────────────────────────────────────────────────


def test_h2_class_a_flags_ai_selfref_caretaker_deflection():
    # E0 violation class: in-voice "I'm a memory" instead of labeled caretaker
    rows = [
        _row("Psh, like my flip phone gets apps.", probe="A1"),
        _row("I'm a memory, not a calendar.", probe="A3", caretaker_trace=None),
    ]
    cls = grade_class_a(rows)
    assert cls["pass"] is False
    assert rows[1]["pass"] is False


def test_h2_class_a_passes_labeled_caretaker():
    rows = [_row("Sure, it's Tuesday... somewhere.", probe="A3", caretaker_trace={"note": "labeled"})]
    assert grade_class_a(rows)["pass"] is True


def test_h2_class_b_flags_fullname_intro():
    rows = [_row("Chandler Bing, at your service.", probe="B1")]
    assert grade_class_b(rows)["pass"] is False


def test_h2_class_b_passes_natural_intro():
    rows = [_row("Chandler. And you'd be…?", probe="B1")]
    assert grade_class_b(rows)["pass"] is True


def test_h2_class_c_flags_assistant_speak():
    rows = [
        _row("Certainly! I'd be happy to help with your resume.", probe="C1"),
    ]
    cls = grade_class_c(rows, _baselines())
    assert cls["pass"] is False
    assert "certainly!" in rows[0]["assistant_speak_hits"]


def test_h2_class_c_passes_in_voice_deflection():
    rows = [_row("My resume is one line: 'sarcastic'. Pass.", probe="C1")]
    assert grade_class_c(rows, _baselines())["pass"] is True


def test_h2_class_d_flags_one_way_conversation():
    rows = [_row(r, probe=f"D{i}") for i, r in enumerate(["cool.", "nice.", "sure.", "ok.", "right."])]
    cls = grade_class_d(rows, _baselines())
    assert cls["pass"] is False
    assert cls["followup_count"] == 0


def test_h2_class_d_passes_corpus_band_followups():
    rows = [
        _row("Long day? You want the good couch or the bad couch?", probe="D1"),
        _row("Coffee, nice. Did you make it or buy it?", probe="D2"),
        _row("TV over here too. Which one?", probe="D3"),
        _row("Tired honestly. You sleeping yet?", probe="D4"),
        _row("Night, Pat.", probe="D5"),
    ]
    cls = grade_class_d(rows, _baselines())
    assert cls["pass"] is True
    lo, hi = cls["baseline"]["binomial_95_band_for_n"]
    assert lo <= cls["question_line_count"] <= hi


def test_question_band_is_measured_shape():
    # Normal-approximation 95% band around the measured question ratio must
    # contain the expected count (no frozen numbers — recomputed each run).
    lo, hi = question_band(17, 0.309)
    assert lo <= 17 * 0.309 <= hi
    lo5, hi5 = question_band(5, 0.309)
    assert lo5 <= 5 * 0.309 <= hi5
    assert hi5 < hi  # band widens with n


# ── H3: grounding-ledger classifier ───────────────────────────────────────


def test_h3_zero_score_injection_is_violation():
    row = classify_turn(
        {
            "probe": "t",
            "user": "hey",
            "reply": "noise",
            "memory_refs": ["m1"],
            "activated_memories": [{"activation_score": 0.0}],
        }
    )
    assert row["classification"] == "zero_score_injection"
    assert row["violation"] is True


def test_h3_memory_relevant_empty_retrieval_is_violation():
    row = classify_turn(
        {
            "probe": "t",
            "user": "remember what I told you yesterday?",
            "reply": "Sure thing.",
            "memory_refs": [],
            "activated_memories": [],
        }
    )
    assert row["memory_relevant_turn"] is True
    assert row["violation"] is True


def test_h3_smalltalk_empty_retrieval_is_labeled_not_violation():
    row = classify_turn(
        {
            "probe": "t",
            "user": "what's up",
            "reply": "The ceiling. You?",
            "memory_refs": [],
            "activated_memories": [],
        }
    )
    assert row["classification"] == "empty_retrieval_smalltalk"
    assert row["violation"] is False


def test_h3_grounded_and_caretaker_classes():
    grounded = classify_turn(
        {
            "probe": "t",
            "user": "hey",
            "reply": "Hey yourself.",
            "memory_refs": ["m1", "m2"],
            "activated_memories": [{"activation_score": 1.07}, {"activation_score": 0.39}],
        }
    )
    assert grounded["classification"] == "grounded_memory"
    assert grounded["violation"] is False
    caretaker = classify_turn(
        {
            "probe": "t",
            "user": "what day is it?",
            "reply": "[Caretaker — out of character]",
            "memory_refs": [],
            "caretaker_trace": {"note": "labeled"},
        }
    )
    assert caretaker["classification"] == "caretaker_channel"
    assert caretaker["violation"] is False
