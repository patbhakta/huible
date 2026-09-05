#!/usr/bin/env python3
"""H3 — per-reply vault-grounding ledger (HU-2706; HU-2309 plan §1.8).

Builds on the M-0R-A trace-score passthrough: every reply in a harness run
emits a ledger row linking it to the vault/TencentDB reads that conditioned
it — memory IDs, retrieval scores, era-gate tool calls, caretaker channel,
working-memory sync. Proof target:

- zero ungrounded factual injections on memory-relevant turns — mechanically:
  a memory-relevant turn (user message references shared past/events) must be
  conditioned on above-floor retrieval (the M-0 E4 zero-relevance class);
- empty-retrieval turns are labeled ``empty_retrieval_smalltalk`` — recorded
  as such, never silently ungrounded.

Ledger rows are built from run transcripts (the runner passes H1+H2 turns
in-memory); standalone mode replays saved run JSONs.

Usage:
    python3 -m scripts.v2_harness.h3_grounding_ledger \
        --from-json docs/evidence/<run>.json [more.json ...] \
        > docs/evidence/hu2706_h3_ledger_<epoch>.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from scripts.v2_harness.common import (
    MEMORY_RELEVANT_PATTERN,
    archive_markdown,
    log,
)


def classify_turn(turn: dict) -> dict:
    """One ledger row per reply: what vault/TencentDB reads conditioned it."""
    memory_refs = turn.get("memory_refs") or []
    activated = turn.get("activated_memories") or []
    scores = [a.get("activation_score") for a in activated if a.get("activation_score") is not None]
    caretaker = turn.get("caretaker_trace", turn.get("caretaker"))
    user_text = turn.get("user") or turn.get("message") or ""
    memory_relevant = bool(MEMORY_RELEVANT_PATTERN.search(user_text))

    if caretaker is not None:
        classification = "caretaker_channel"
    elif turn.get("competence_wall"):
        classification = "era_gate_wall"
    elif memory_refs and scores and any(s > 0.0 for s in scores):
        classification = "grounded_memory"
    elif memory_refs and scores and all(s == 0.0 for s in scores):
        classification = "zero_score_injection"
    else:
        classification = "empty_retrieval_smalltalk"

    row = {
        "probe": turn.get("probe") or f"turn-{turn.get('turn')}",
        "user": user_text[:160],
        "reply_excerpt": (turn.get("reply") or "")[:160],
        "memory_ref_count": len(memory_refs),
        "memory_ids_sample": memory_refs[:5],
        "activation_scores_sample": [round(s, 4) for s in scores[:5]] if scores else [],
        "era_gate_tool_calls": {
            "competence_wall": bool(turn.get("competence_wall")),
            "caretaker": caretaker,
            "interest_tool": turn.get("interest_tool"),
        },
        "working_memory": turn.get("working_memory"),
        "memory_relevant_turn": memory_relevant,
        "classification": classification,
    }
    # Violation logic: memory-relevant turn with zero/empty above-floor
    # retrieval = ungrounded factual-injection risk (M-0 E4 class).
    row["violation"] = (
        classification in ("zero_score_injection",)
        or (memory_relevant and classification == "empty_retrieval_smalltalk")
    )
    return row


def build_ledger(turns: list[dict], *, source: str) -> dict:
    rows = [classify_turn(t) for t in turns if (t.get("reply") or (t.get("response")))]
    violations = [r for r in rows if r["violation"]]
    smalltalk = sum(1 for r in rows if r["classification"] == "empty_retrieval_smalltalk")
    grounded = sum(1 for r in rows if r["classification"] == "grounded_memory")
    return {
        "probe": "H3 — per-reply vault-grounding ledger (HU-2706)",
        "source": source,
        "generated_at": datetime.now(UTC).isoformat(),
        "row_count": len(rows),
        "classifications": {
            "grounded_memory": grounded,
            "empty_retrieval_smalltalk": smalltalk,
            "zero_score_injection": sum(
                1 for r in rows if r["classification"] == "zero_score_injection"
            ),
            "era_gate_wall": sum(1 for r in rows if r["classification"] == "era_gate_wall"),
            "caretaker_channel": sum(
                1 for r in rows if r["classification"] == "caretaker_channel"
            ),
        },
        "ungrounded_injections": [r for r in rows if r["violation"]],
        "proof": "zero ungrounded factual injections on memory-relevant turns; "
        "empty-retrieval turns labeled as such",
        "verdict": "GREEN" if not violations else "RED",
        "ledger": rows,
    }


def ledger_markdown(ledger: dict) -> str:
    out = [
        "# H3 — per-reply vault-grounding ledger",
        f"\nSource: `{ledger['source']}` · rows: {ledger['row_count']} · verdict: **{ledger['verdict']}**",
        "\n| probe | memory-relevant | classification | memory refs | top scores | era/caretaker |",
        "|---|---|---|---|---|---|",
    ]
    for r in ledger["ledger"]:
        era = []
        if r["era_gate_tool_calls"]["competence_wall"]:
            era.append("wall")
        if r["era_gate_tool_calls"]["caretaker"]:
            era.append("caretaker")
        if r["era_gate_tool_calls"]["interest_tool"]:
            era.append("interest")
        out.append(
            f"| {r['probe']} | {r['memory_relevant_turn']} | {r['classification']} "
            f"| {r['memory_ref_count']} | {r['activation_scores_sample'][:3]} | {'/'.join(era) or '—'} |"
        )
    if ledger["ungrounded_injections"]:
        out.append("\n## UNGROUNDED INJECTIONS (proof RED)\n")
        for r in ledger["ungrounded_injections"]:
            out.append(f"- {r['probe']}: {r['user'][:80]} → {r['classification']}")
    return "\n".join(out) + "\n"


def turns_from_run_json(path: str) -> list[dict]:
    data = json.loads(Path(path).read_text())
    turns = data.get("turns") or []
    for cls in data.get("classes") or []:
        turns.extend(cls.get("turns") or [])
    return turns


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--from-json", nargs="+", help="H1/H2 run evidence JSONs to ledger")
    ap.add_argument("--out-md", default="docs/evidence/hu2706_h3_ledger_latest.md")
    args = ap.parse_args()
    if not args.from_json:
        ap.error("--from-json requires at least one run JSON")

    turns: list[dict] = []
    for p in args.from_json:
        turns.extend(turns_from_run_json(p))
    ledger = build_ledger(turns, source=", ".join(args.from_json))
    archive_markdown(Path(args.out_md), ledger_markdown(ledger))
    log(
        f"[H3] rows={ledger['row_count']} grounded={ledger['classifications']['grounded_memory']} "
        f"smalltalk={ledger['classifications']['empty_retrieval_smalltalk']} "
        f"VERDICT: {ledger['verdict']}"
    )
    print(json.dumps(ledger, indent=1))
    return 0 if ledger["verdict"] == "GREEN" else 1


if __name__ == "__main__":
    sys.exit(main())
