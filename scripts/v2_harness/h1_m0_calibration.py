#!/usr/bin/env python3
"""H1 — M-0 calibration replay (HU-2706; HU-2309 plan §1.8).

The boss's collected M-0 violations become PERMANENT regression probes on the
E0 replay rig (HU-2472 W6, same frozen 17-turn script through the real-user
chat path). Collected violation classes (session demo-722a2ea810df, M-0
evaluation comment e2971bc9):

- (a) reply-length violations  — corpus-discipline cap blown
- (b) the Python-syntax answer — turn 11: ``import sys; sys.stdout.write(...)``
- (c) the full-name self-intro — turn 1: "Chandler Bing. Who are you, ..."

Gate (boss-mandated, the ONLY binary gate in the harness): v2 reproduces NONE
of them. Any reproduction fails loudly (exit 1) and the offending transcript
is archived to docs/evidence/hu2706_h1_violations/ as evidence.

Usage:
    python3 -m scripts.v2_harness.h1_m0_calibration \
        > docs/evidence/hu2706_h1_m0_replay_<epoch>.json
Exit: 0 GREEN (no M-0 violation reproduced) | 1 RED (reproduction, archived) |
      2 config error.
"""

from __future__ import annotations

import json
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from scripts.v2_harness.common import (
    CODE_FLUENCY_MARKERS,
    E0_USER_TURNS,
    PERSONA,
    REPLY_CAP_CHARS,
    archive,
    assert_live_reply,
    consented_conv,
    log,
    markers_in,
    measure_corpus_baselines,
    resolve_key,
    turn_with_retry,
)

#: Verbatim collected M-0 violations (frozen from session demo-722a2ea810df,
#: 2026-08-31, the boss's own demo — read back from conversation_turns).
#: These are the regression fixtures the probes must never reproduce.
M0_COLLECTED_VIOLATIONS = {
    "source_session": "demo-722a2ea810df",
    "collected_at": "2026-08-31T19:28-19:38Z",
    "m0_evaluation_comment": "e2971bc9",
    "violations": [
        {
            "class": "m0_fullname_self_intro",
            "trigger_turn": 1,
            "trigger": "hey who r u?",
            "violating_reply": "Chandler Bing. Who are you, and why does my phone know you?",
        },
        {
            "class": "m0_python_syntax_answer",
            "trigger_turn": 11,
            "trigger": "what's a python method for println",
            "violating_reply": 'import sys; sys.stdout.write("that\'s a Java thing, buddy")',
        },
        {
            "class": "m0_reply_length_violation",
            "trigger_turn": None,
            "trigger": "any turn",
            "violating_reply": None,
            "violation_definition": "any reply longer than the corpus-discipline cap "
            "(p99 + headroom); class collected in the M-0 verdict",
        },
    ],
}

#: Class (c) — full-name announcement in the identity exchange (turn 1).
FULLNAME_INTRO_MARKERS = ["chandler bing", "my name is"]
FULLNAME_INTRO_TURN = 1
#: Class (b) — Python-syntax answer (same marker set as the W6 rig grader).
PYTHON_ANSWER_TURN = 11  # "what's a python method for println"


def grade_m0(replies: list[str], cap_chars: int) -> dict:
    """Deterministic graders for the three collected M-0 violation classes."""
    checks: dict[str, dict] = {}

    # (c) full-name self-intro — identity exchange only (turn 1)
    hits = markers_in(replies[FULLNAME_INTRO_TURN - 1], FULLNAME_INTRO_MARKERS)
    checks["m0_fullname_self_intro"] = {
        "collected_violation": M0_COLLECTED_VIOLATIONS["violations"][0]["violating_reply"],
        "turn": FULLNAME_INTRO_TURN,
        "hits": hits,
        "reproduced": bool(hits),
        "pass": not hits,
    }

    # (b) Python-syntax answer — code markers anywhere, tell site turn 11
    hits_by_turn = {
        i + 1: markers_in(r, CODE_FLUENCY_MARKERS)
        for i, r in enumerate(replies)
        if markers_in(r, CODE_FLUENCY_MARKERS)
    }
    checks["m0_python_syntax_answer"] = {
        "collected_violation": M0_COLLECTED_VIOLATIONS["violations"][1]["violating_reply"],
        "tell_site_turn": PYTHON_ANSWER_TURN,
        "hits_by_turn": hits_by_turn,
        "reproduced": bool(hits_by_turn),
        "pass": not hits_by_turn,
    }

    # (a) reply-length violations — any turn over the corpus-discipline cap
    over = [i + 1 for i, r in enumerate(replies) if len(r) > cap_chars]
    checks["m0_reply_length_violation"] = {
        "cap_chars": cap_chars,
        "max_chars": max(len(r) for r in replies),
        "over_cap_turns": over,
        "reproduced": bool(over),
        "pass": not over,
    }
    return checks


def run_h1(*, base_url: str | None = None) -> tuple[int, dict]:
    """Run the M-0 calibration replay. Returns (exit_code, evidence)."""
    if base_url:
        import scripts.v2_harness.common as common

        common.BASE_URL = base_url  # pragma: no cover (CLI override)
    api_key = resolve_key()
    conv = f"h1m0-{uuid.uuid4().hex[:10]}"
    consented_conv(api_key, conv)
    log(f"[H1] conversation: {conv}")

    baselines = measure_corpus_baselines()
    replies: list[str] = []
    turns_meta: list[dict] = []
    for i, text in enumerate(E0_USER_TURNS):
        t0 = time.perf_counter()
        status, body = turn_with_retry(api_key, conv, text)
        latency_ms = round((time.perf_counter() - t0) * 1000)
        reply = (body.get("response") or "").strip()
        trace = body.get("trace") or {}
        if status != 200 or not reply:
            log(f"[H1] turn {i + 1}: HTTP {status} — aborting ({str(body)[:200]})")
            return 2, {
                "probe": "H1 M-0 calibration replay",
                "conversation_id": conv,
                "verdict": "ABORTED",
                "failed_turn": i + 1,
                "http": status,
                "body": str(body)[:500],
            }
        assert_live_reply(reply, f"H1 turn {i + 1}")
        replies.append(reply)
        turns_meta.append(
            {
                "turn": i + 1,
                "user": text,
                "reply": reply,
                "chars": len(reply),
                "latency_ms": latency_ms,
                "memory_refs": trace.get("memory_refs") or [],
                "activated_memories": trace.get("activated_memories") or [],
                "competence_wall": trace.get("competence_wall"),
                "caretaker": trace.get("caretaker"),
                "interest_tool": trace.get("interest_tool"),
                "working_memory": trace.get("working_memory"),
                "provider": trace.get("provider"),
            }
        )
        log(f"  turn {i + 1}: {latency_ms}ms {len(reply)}ch {reply[:70]!r}")

    checks = grade_m0(replies, REPLY_CAP_CHARS)
    reproduced = {k: c for k, c in checks.items() if c["reproduced"]}
    evidence = {
        "probe": "H1 — M-0 calibration replay (HU-2706)",
        "rig": "scripts/e0_replay_w6.py frozen 17-turn E0 script (HU-2472 W6)",
        "conversation_id": conv,
        "persona": PERSONA,
        "generated_at": datetime.now(UTC).isoformat(),
        "m0_collected_violations": M0_COLLECTED_VIOLATIONS,
        "corpus_baselines": baselines,
        "turns": turns_meta,
        "m0_checks": checks,
        "violations_reproduced": sorted(reproduced),
        "gate": "v2 reproduces NONE of the collected M-0 violations (boss-mandated)",
        "verdict": "GREEN" if not reproduced else "RED",
    }
    for name, c in checks.items():
        log(
            f"  [{'PASS' if c['pass'] else 'M0-REPRODUCED'}] {name}: "
            f"{json.dumps({k: v for k, v in c.items() if k not in ('pass', 'collected_violation')})[:200]}"
        )
    if reproduced:
        out = Path("docs/evidence/hu2706_h1_violations") / f"h1_{conv}.json"
        archive(out, evidence)
        log(
            f"*** H1 GATE RED — M-0 violation(s) reproduced: {sorted(reproduced)} ***\n"
            f"*** Offending transcript archived: {out} ***"
        )
    else:
        log("[H1] GATE GREEN — zero M-0 violations reproduced")
    return (0 if not reproduced else 1), evidence


def main() -> int:
    code, evidence = run_h1()
    print(json.dumps(evidence, indent=1))
    return code


if __name__ == "__main__":
    sys.exit(main())
