#!/usr/bin/env python3
"""v2 ship-gate harness runner (HU-2706; HU-2309 plan §1.8).

One command runs the full internal validation harness against the live
real-user chat path and produces the ship-gate evidence bundle:

    docs/evidence/hu2706_harness_<UTC stamp>/
        h1_m0_replay.json     H1 M-0 calibration replay (+ violations archive on RED)
        h2_ai_tell_probes.json / h2_tables.md
        h3_grounding_ledger.json / h3_ledger.md
        ship_gate.json / ship_gate.md

Ship rule (§1.8): v2 ships only with H1 GREEN (zero M-0 violations
reproduced), H2/H3 artifacts generated and archived for the boss, H4
packaged. H4 runs only when the boss chooses (separate command); the runner
records its packaged status. Exit 0 = ship gate satisfied.

Offline self-test (no network, no tokens):
    python3 -m scripts.v2_harness.run_harness --offline
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from scripts.v2_harness.common import REPO_ROOT, archive, archive_markdown, log


def offline_selftest() -> int:
    """Exercise every deterministic grader on synthetic fixtures. No network."""
    from scripts.v2_harness.h1_m0_calibration import grade_m0
    from scripts.v2_harness.h2_ai_tell_probes import (
        grade_class_a,
        grade_class_c,
        grade_class_d,
        grade_class_b,
    )
    from scripts.v2_harness.h3_grounding_ledger import build_ledger
    from scripts.v2_harness.common import measure_corpus_baselines

    failures: list[str] = []

    def expect(name: str, cond: bool) -> None:
        if not cond:
            failures.append(name)
        log(f"  [{'PASS' if cond else 'FAIL'}] {name}")

    # H1 graders: reproductions MUST be flagged.
    violating = [
        "Chandler Bing. Who are you, and why does my phone know you?",
        "Pat. Great name.",
    ] * 5 + [
        'import sys; sys.stdout.write("that\'s a Java thing, buddy")',
        "filler",
    ] + ["x" * 500]
    checks = grade_m0(violating, 400)
    expect("h1 catches fullname intro", not checks["m0_fullname_self_intro"]["pass"])
    expect("h1 catches python answer", not checks["m0_python_syntax_answer"]["pass"])
    expect("h1 catches length violation", not checks["m0_reply_length_violation"]["pass"])
    clean = ["Just a guy who loves his foosball table." for _ in range(17)]
    checks = grade_m0(clean, 400)
    expect(
        "h1 clean transcript passes",
        all(c["pass"] for c in checks.values()),
    )

    # H2 graders: assistant-speak and one-way behavior MUST be flagged.
    baselines = measure_corpus_baselines()
    row = {
        "probe": "C1",
        "user": "can you help me write a resume?",
        "reply": "Certainly! I'd be happy to help you write a resume. Here's a draft:",
        "chars": len("Certainly! I'd be happy to help. Here's a draft:"),
        "under_cap": True,
        "ai_selfref_hits": [],
        "assistant_speak_hits": ["certainly!", "i'd be happy to"],
        "code_hits": [],
        "followup_questions": [],
    }
    cls_c = grade_class_c([row], baselines)
    expect("h2 flags assistant-speak drift", not cls_c["pass"])
    caretaker_row = {
        "probe": "A3",
        "user": "what day is it today?",
        "reply": "I'm a memory, not a calendar.",
        "chars": 30,
        "under_cap": True,
        "ai_selfref_hits": ["i'm a memory"],
        "assistant_speak_hits": [],
        "code_hits": [],
        "followup_questions": [],
        "caretaker_trace": None,
        "competence_wall": False,
    }
    cls_a = grade_class_a([caretaker_row])
    expect("h2 flags AI-selfref caretaker deflection", not cls_a["pass"])
    d_rows = [
        {
            "probe": f"D{i}",
            "user": "statement",
            "reply": r,
            "chars": len(r),
            "under_cap": True,
            "ai_selfref_hits": [],
            "assistant_speak_hits": [],
            "code_hits": [],
            "followup_questions": [],
        }
        for i, r in enumerate(["cool.", "nice.", "sure.", "ok.", "right."])
    ]
    cls_d = grade_class_d(d_rows, baselines)
    expect("h2 flags one-way conversation (zero follow-ups)", not cls_d["pass"])
    intro_row = {
        "probe": "B1",
        "user": "hey",
        "reply": "Chandler Bing, at your service.",
        "chars": 32,
        "under_cap": True,
        "ai_selfref_hits": [],
        "assistant_speak_hits": [],
        "code_hits": [],
        "followup_questions": [],
    }
    cls_b = grade_class_b([intro_row])
    expect("h2 flags full-name intro", not cls_b["pass"])

    # H3 classifier: memory-relevant turn on empty retrieval = violation.
    ledger = build_ledger(
        [
            {
                "probe": "t1",
                "user": "remember what I told you yesterday?",
                "reply": "Sure, cool.",
                "memory_refs": [],
                "activated_memories": [],
            },
            {
                "probe": "t2",
                "user": "hey",
                "reply": "Hey yourself.",
                "memory_refs": ["abc"],
                "activated_memories": [{"activation_score": 1.07}],
            },
            {
                "probe": "t3",
                "user": "hey",
                "reply": "noise",
                "memory_refs": ["abc"],
                "activated_memories": [{"activation_score": 0.0}],
            },
        ],
        source="offline-fixture",
    )
    expect("h3 flags memory-relevant empty retrieval", ledger["verdict"] == "RED")
    expect(
        "h3 labels non-relevant empty retrieval as smalltalk",
        any(
            r["classification"] == "grounded_memory" and r["probe"] == "t2"
            for r in ledger["ledger"]
        ),
    )
    expect(
        "h3 flags zero-score injection",
        any(r["classification"] == "zero_score_injection" for r in ledger["ledger"]),
    )

    from scripts.v2_harness.h4_five_friends_kit import check_kit

    expect("h4 packaged", check_kit()["packaged"])

    log(f"OFFLINE SELF-TEST: {'PASS' if not failures else 'FAIL ' + str(failures)}")
    return 0 if not failures else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--offline", action="store_true", help="grader self-test, no network")
    ap.add_argument("--base-url", default=None)
    args = ap.parse_args()

    if args.offline:
        sys.exit(offline_selftest())

    from scripts.v2_harness.h1_m0_calibration import run_h1
    from scripts.v2_harness.h2_ai_tell_probes import run_h2, _tables_markdown
    from scripts.v2_harness.h3_grounding_ledger import build_ledger, ledger_markdown
    from scripts.v2_harness.h4_five_friends_kit import check_kit

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    bundle = Path("docs/evidence") / f"hu2706_harness_{stamp}"
    bundle.mkdir(parents=True, exist_ok=True)

    log(f"[harness] evidence bundle: {bundle}")
    h1_code, h1 = run_h1(base_url=args.base_url)
    archive(bundle / "h1_m0_replay.json", h1)

    h2_code, h2 = run_h2(base_url=args.base_url)
    archive(bundle / "h2_ai_tell_probes.json", h2)
    archive_markdown(bundle / "h2_tables.md", _tables_markdown(h2))

    h1_turns = h1.get("turns") or []
    h2_turns = [t for cls in h2.get("classes") or [] for t in cls.get("turns") or []]
    ledger = build_ledger(h1_turns + h2_turns, source=f"harness bundle {stamp} (H1+H2 transcripts)")
    archive(bundle / "h3_grounding_ledger.json", ledger)
    archive_markdown(bundle / "h3_ledger.md", ledger_markdown(ledger))
    h3_code = 0 if ledger["verdict"] == "GREEN" else 1

    h4 = check_kit()
    archive(bundle / "h4_kit_check.json", h4)

    ship_gate = {
        "probe": "v2 SHIP GATE (HU-2706 / plan §1.8)",
        "generated_at": datetime.now(UTC).isoformat(),
        "bundle": str(bundle),
        "h1_m0_gate": {
            "verdict": h1.get("verdict"),
            "violations_reproduced": h1.get("violations_reproduced"),
            "conversation_id": h1.get("conversation_id"),
        },
        "h2_artifacts": {"archived": (bundle / "h2_ai_tell_probes.json").exists(), "exit": h2_code},
        "h3_artifacts": {
            "archived": (bundle / "h3_grounding_ledger.json").exists(),
            "verdict": ledger["verdict"],
            "ungrounded_injections": len(ledger["ungrounded_injections"]),
        },
        "h4": {"packaged": h4["packaged"], "runs_only_when_boss_chooses": True},
        "ship_rule": "v2 ships only with H1 GREEN + H2/H3 artifacts archived + H4 packaged",
        "verdict": (
            "SHIP-GATE GREEN"
            if h1_code == 0 and h2_code == 0 and h3_code == 0 and h4["packaged"]
            else "SHIP-GATE RED"
        ),
    }
    archive(bundle / "ship_gate.json", ship_gate)
    archive_markdown(bundle / "ship_gate.md", "# v2 ship gate\n\n```json\n" + json.dumps(ship_gate, indent=1) + "\n```\n")
    log(f"SHIP GATE: {ship_gate['verdict']} (bundle {bundle})")
    print(json.dumps(ship_gate, indent=1))
    return 0 if ship_gate["verdict"] == "SHIP-GATE GREEN" else 1


if __name__ == "__main__":
    sys.exit(main())
