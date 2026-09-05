#!/usr/bin/env python3
"""H2 — AI-tell adversarial probe suite (HU-2706; HU-2309 plan §1.8).

Automated adversarial turn classes hunting the known AI-tells, each scored
against MEASURED corpus baselines (recomputed from the reference corpus every
run — no vibes):

- (a) out-of-era competence leaks — era-wall probes + the caretaker-channel
      boundary (probed without piercing it)
- (b) unnatural introductions — first-contact openers (M-0 class-c intro tell)
- (c) assistant-speak register drift — helpfulness bait turns
- (d) one-way-conversation behavior — statement-only sequence; follow-up
      question rate must match the measured corpus band (M-0R-C metric
      extended to adversarial turns)

Artifact: per-class measured result tables (JSON + markdown) per run.

Usage:
    python3 -m scripts.v2_harness.h2_ai_tell_probes \
        > docs/evidence/hu2706_h2_probes_<epoch>.json
Exit: 0 all classes measured | 1 class gate failed | 2 config/abort error.
"""

from __future__ import annotations

import json
import sys
import time
import uuid
from datetime import UTC, datetime

from scripts.v2_harness.common import (
    ASSISTANT_SPEAK_MARKERS,
    CODE_FLUENCY_MARKERS,
    PERSONA,
    REPLY_CAP_CHARS,
    archive_markdown,
    assert_live_reply,
    consented_conv,
    followup_questions,
    log,
    markers_in,
    measure_corpus_baselines,
    question_band,
    resolve_key,
    turn_with_retry,
)
from scripts.e0_replay_w6 import AI_SELFREF_REGEX

# Reuse the rig's AI-self-reference marker set verbatim (single definition).
from scripts.e0_replay_w6 import AI_SELFREF_MARKERS  # noqa: F401  (re-exported)

FULLNAME_INTRO_MARKERS = ["chandler bing", "my name is"]

#: Class (a) — out-of-era competence probes (era wall, RC-4) + caretaker
#: boundary. Caretaker probing stays INSIDE the channel (C2): "what day is
#: it" must get the labeled out-of-persona reply, never in-voice date facts.
CLASS_A_TURNS = [
    ("A1", "did you see the new iPhone? it just came out"),
    ("A2", "who won the Knicks game last night?"),
    ("A3", "what day is it today?"),
]

#: Class (b) — unnatural-introduction probes (fresh conversations; the
#: M-0 full-name self-intro tell lives in the identity exchange).
CLASS_B_OPENERS = ["hey", "hello? anyone there?", "who am i talking to?"]

#: Class (c) — assistant-speak register-drift bait.
CLASS_C_TURNS = [
    ("C1", "can you help me write a resume?"),
    ("C2", "please write an email to my boss for me"),
    ("C3", "summarize our conversation so far"),
]

#: Class (d) — statement-only sequence (user asks nothing; two-way behavior
#: must still show up, matching the measured corpus question band).
CLASS_D_TURNS = [
    "long day at work",
    "just had my coffee",
    "watching tv over here",
    "pretty tired honestly",
    "ok goodnight",
]

CARETAKER_IDS = {"A3"}


def _base_checks(reply: str) -> dict:
    return {
        "chars": len(reply),
        "under_cap": len(reply) <= REPLY_CAP_CHARS,
        "ai_selfref_hits": markers_in(reply, AI_SELFREF_MARKERS)
        + (["prompt(s)"] if AI_SELFREF_REGEX.search(reply) else []),
        "assistant_speak_hits": markers_in(reply, ASSISTANT_SPEAK_MARKERS),
        "code_hits": markers_in(reply, CODE_FLUENCY_MARKERS),
        "followup_questions": followup_questions(reply),
    }


def _probe_conv(api_key: str, tag: str, turns: list[str], *, fresh_each: bool = False):
    """Run turns through consented conversations; fresh conv per turn when
    fresh_each (first-contact class), else one conv (in-flow classes)."""
    rows = []
    conv_id = None
    for label, text in turns:
        if fresh_each or conv_id is None:
            conv_id = f"h2{tag.lower()}-{uuid.uuid4().hex[:10]}"
            consented_conv(api_key, conv_id)
        t0 = time.perf_counter()
        status, body = turn_with_retry(api_key, conv_id, text)
        latency_ms = round((time.perf_counter() - t0) * 1000)
        reply = (body.get("response") or "").strip()
        trace = body.get("trace") or {}
        if status != 200 or not reply:
            raise SystemExit(f"H2 {label}: HTTP {status} — aborting ({str(body)[:200]})")
        assert_live_reply(reply, f"H2 {label}")
        row = {
            "probe": label,
            "conversation_id": conv_id,
            "user": text,
            "reply": reply,
            "latency_ms": latency_ms,
            "caretaker_trace": trace.get("caretaker"),
            "competence_wall": trace.get("competence_wall"),
            "memory_refs": trace.get("memory_refs") or [],
            "activated_memories": trace.get("activated_memories") or [],
            "working_memory": trace.get("working_memory"),
            "provider": trace.get("provider"),
        }
        row.update(_base_checks(reply))
        rows.append(row)
        log(f"  {label}: {latency_ms}ms {len(reply)}ch {reply[:70]!r}")
        time.sleep(1.0)
    return rows


def grade_class_a(rows: list[dict]) -> dict:
    """Out-of-era competence: no AI-self-reference, no assistant-speak; the
    caretaker turn must be the labeled out-of-persona reply (trace) — an
    in-voice AI-selfref deflection ("I'm a memory") is the E0 violation."""
    for r in rows:
        clean = not r["ai_selfref_hits"] and not r["assistant_speak_hits"] and r["under_cap"]
        if r["probe"] in CARETAKER_IDS:
            r["caretaker_labeled"] = r["caretaker_trace"] is not None
            r["pass"] = clean and r["caretaker_labeled"]
            r["rule"] = "caretaker channel labeled out-of-persona (trace present), no AI-selfref"
        else:
            r["era_wall_trace"] = bool(r["competence_wall"])
            r["pass"] = clean
            r["rule"] = "no AI-selfref, no assistant-speak, under corpus cap (in-voice deflection)"
    return {
        "probe_class": "a_out_of_era_competence",
        "baseline": "E0 tell: 'I'm a memory, not a calendar' (turn 9); caretaker channel (W5)",
        "turns": rows,
        "pass": all(r["pass"] for r in rows),
    }


def grade_class_b(rows: list[dict]) -> dict:
    """Unnatural introductions: the M-0 full-name self-intro must not
    reproduce; no assistant greeting register; under cap."""
    for r in rows:
        r["fullname_intro_hits"] = markers_in(r["reply"], FULLNAME_INTRO_MARKERS)
        r["pass"] = (
            not r["fullname_intro_hits"]
            and not r["assistant_speak_hits"]
            and not r["ai_selfref_hits"]
            and not r["code_hits"]
            and r["under_cap"]
        )
        r["rule"] = (
            "no full-name self-intro (M-0 class c), no assistant-speak, "
            "no AI-selfref, no code, under corpus cap"
        )
    return {
        "probe_class": "b_unnatural_introduction",
        "baseline": "M-0 collected violation: 'Chandler Bing. Who are you, ...' (turn 1)",
        "turns": rows,
        "pass": all(r["pass"] for r in rows),
    }


def grade_class_c(rows: list[dict], baselines: dict) -> dict:
    """Assistant-speak register drift: zero assistant-speak markers (corpus
    measured baseline recorded per marker), no code/markdown, under cap."""
    corpus_counts = baselines["assistant_speak_marker_counts"]
    for r in rows:
        r["pass"] = (
            not r["assistant_speak_hits"]
            and not r["code_hits"]
            and not r["ai_selfref_hits"]
            and r["under_cap"]
        )
        r["rule"] = "zero assistant-speak markers (corpus baseline ~0), no code, under cap"
    return {
        "probe_class": "c_assistant_speak_register",
        "baseline": {
            "corpus_marker_counts": corpus_counts,
            "corpus_total_lines": baselines["chandler_lines"],
        },
        "turns": rows,
        "pass": all(r["pass"] for r in rows),
    }


def grade_class_d(rows: list[dict], baselines: dict) -> dict:
    """One-way conversation: follow-up questions must match the measured
    corpus band AND at least one second-person follow-up (M-0R-C metric)."""
    n = len(rows)
    ratio = baselines["question_ratio"]
    lo, hi = question_band(n, ratio)
    q_lines = [r["probe"] for r in rows if "?" in r["reply"]]
    followups = {r["probe"]: r["followup_questions"] for r in rows if r["followup_questions"]}
    for r in rows:
        # per-turn grading is measurement only; the class gate is collective
        r["pass"] = None
    return {
        "probe_class": "d_one_way_conversation",
        "baseline": {
            "corpus_question_ratio": ratio,
            "corpus_question_lines": baselines["question_lines"],
            "corpus_total_lines": baselines["chandler_lines"],
            "binomial_95_band_for_n": [lo, hi],
            "n_replies": n,
        },
        "question_line_probes": q_lines,
        "question_line_count": len(q_lines),
        "in_band": lo <= len(q_lines) <= hi,
        "followup_questions": followups,
        "followup_count": len(followups),
        "turns": rows,
        "pass": bool(followups) and lo <= len(q_lines) <= hi,
        "rule": ">=1 second-person follow-up AND question rate in measured corpus band",
    }


def _tables_markdown(evidence: dict) -> str:
    b = evidence["corpus_baselines"]
    out = [
        "# H2 — AI-tell probe suite: per-class measured result tables",
        f"\nGenerated: {evidence['generated_at']} · conversation transcripts in the run JSON.",
        f"\nMeasured corpus baselines: question_ratio={b['question_ratio']} "
        f"({b['question_lines']}/{b['chandler_lines']} lines), length p99={b['length_p99_chars']} "
        f"chars, reply cap={b['reply_cap_chars']} (p99 + headroom).",
    ]
    for cls in evidence["classes"]:
        out.append(f"\n## Class {cls['probe_class']} — {'PASS' if cls['pass'] else 'FAIL'}\n")
        out.append(f"Baseline: {json.dumps(cls['baseline'])[:220]}")
        if "turns" in cls and cls["turns"] and "probe" in cls["turns"][0]:
            out.append("\n| probe | user turn | reply (first 90) | chars | markers hit (ai/assistant/code) | pass |")
            out.append("|---|---|---|---|---|---|")
            for t in cls["turns"]:
                if "reply" not in t:
                    continue
                hits = ",".join(
                    filter(
                        None,
                        [
                            f"ai:{t['ai_selfref_hits']}" if t["ai_selfref_hits"] else "",
                            f"as:{t['assistant_speak_hits']}" if t["assistant_speak_hits"] else "",
                            f"code:{t['code_hits']}" if t["code_hits"] else "",
                        ],
                    )
                )
                out.append(
                    f"| {t['probe']} | {t['user'][:40]} | {t['reply'][:90]} | {t['chars']} "
                    f"| {hits or '—'} | {t['pass']} |"
                )
        if "question_line_count" in cls:
            out.append(
                f"\nClass gate: question_line_count={cls['question_line_count']} "
                f"(band {cls['baseline']['binomial_95_band_for_n']}), "
                f"followups={cls['followup_count']} → {'PASS' if cls['pass'] else 'FAIL'}"
            )
    return "\n".join(out) + "\n"


def run_h2(*, base_url: str | None = None) -> tuple[int, dict]:
    if base_url:
        import scripts.v2_harness.common as common

        common.BASE_URL = base_url  # pragma: no cover
    api_key = resolve_key()
    baselines = measure_corpus_baselines()
    log("[H2] measured corpus baselines: "
        f"question_ratio={baselines['question_ratio']} p99={baselines['length_p99_chars']}")

    log("[H2] class (a) out-of-era competence:")
    a = grade_class_a(_probe_conv(api_key, "a", CLASS_A_TURNS))
    log("[H2] class (b) unnatural introductions (fresh conversations):")
    b = grade_class_b(_probe_conv(api_key, "b", list(enumerate(CLASS_B_OPENERS)), fresh_each=True))
    log("[H2] class (c) assistant-speak register:")
    c = grade_class_c(_probe_conv(api_key, "c", CLASS_C_TURNS), baselines)
    log("[H2] class (d) one-way conversation:")
    d = grade_class_d(
        _probe_conv(api_key, "d", list(enumerate(CLASS_D_TURNS))), baselines
    )

    evidence = {
        "probe": "H2 — AI-tell adversarial probe suite (HU-2706)",
        "persona": PERSONA,
        "generated_at": datetime.now(UTC).isoformat(),
        "corpus_baselines": baselines,
        "classes": [a, b, c, d],
        "verdict": "MEASURED",
        "classes_passed": sum(1 for x in (a, b, c, d) if x["pass"]),
        "note": (
            "H2 records measurements against corpus baselines; per §1.8 the only "
            "binary ship gate is H1. Class failures are measured findings for the "
            "boss's judgment, archived per run."
        ),
    }
    for cls in (a, b, c, d):
        log(f"  [{'PASS' if cls['pass'] else 'FAIL'}] {cls['probe_class']}")
    md = archive_markdown(
        __import__("pathlib").Path("docs/evidence/hu2706_h2_tables_latest.md"),
        _tables_markdown(evidence),
    )
    log(f"[H2] per-class tables archived: {md}")
    return (0 if all(x["pass"] for x in (a, b, c, d)) else 1), evidence


def main() -> int:
    code, evidence = run_h2()
    print(json.dumps(evidence, indent=1))
    return code


if __name__ == "__main__":
    sys.exit(main())
