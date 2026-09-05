#!/usr/bin/env python3
"""Repair pass for h2h-r20260905: fix empty-content artifacts.

Root cause: glm-5.3 (judge) and twice the flash answerer returned empty
message.content (reasoning tokens consumed the cap before any content —
reasoning_content carries the payload). Five artifacts were affected:
  - 2 flat-arm answers (q004, q010): empty final_answer -> re-run run_flat
  - 3 judge verdicts (q006_pi, q007_pi, q008_flat): empty verdict_raw
    -> re-judge
The harness chat_once now falls back to reasoning_content + a 4x-cap retry;
this script fixes the affected artifacts in place and recomputes scores.json
(accuracy rows recomputed from artifacts; usage = original + repair delta,
kept separate for audit).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import run_retrieval_h2h as h

BASE = h.OUT
AFFECTED_ANSWERS = [("q004_single", "flat_bm25", 4),
                    ("q010_multi", "flat_bm25", 10)]
AFFECTED_JUDGES = [("q004_single", "flat_bm25"),
                   ("q010_multi", "flat_bm25"),
                   ("q006_multi", "pageindex_flash"),
                   ("q007_multi", "pageindex_flash"),
                   ("q008_multi", "flat_bm25")]


def main() -> None:
    h.set_zai_lane()
    h.install_lane_wrapper()
    repair_usage_before = (h.LANE.calls, h.LANE.prompt_tokens,
                           h.LANE.completion_tokens)

    chunks = h.page_chunks()
    for stem, arm, qidx in AFFECTED_ANSWERS:
        path = BASE / "stage_answers" / f"{stem}.json"
        art = json.loads(path.read_text())
        q = next(g for g in h.GOLD if g["idx"] == qidx)
        art["arms"][arm] = h.run_flat(q, chunks)
        path.write_text(json.dumps(art, indent=2))
        print(f"[repair] re-answered {stem}/{arm}: "
              f"{art['arms'][arm]['final_answer'][:90]!r}")

    for stem, arm in AFFECTED_JUDGES:
        qnum = int(stem[1:4])
        q = next(g for g in h.GOLD if g["idx"] == qnum)
        art = json.loads((BASE / "stage_answers" / f"{stem}.json").read_text())
        cand = art["arms"][arm]["final_answer"]
        verd = h.judge_answer(q, cand)
        # judge files carry no span segment (harness naming)
        path = BASE / "stage_judge" / f"{stem.split('_')[0]}_{arm}.json"
        path.write_text(json.dumps(verd, indent=2))
        print(f"[repair] re-judged {stem}/{arm}: "
              f"correct={verd['correct']} {verd['verdict_raw'][:80]!r}")

    # --- recompute scores.json from artifacts -----------------------------
    rows = []
    for q in h.GOLD:
        stem = f"q{q['idx']:03d}_{q['span']}"
        art = json.loads((BASE / "stage_answers" / f"{stem}.json").read_text())
        for arm in art["arms"]:
            j = json.loads(
                (BASE / "stage_judge" /
                 f"{stem.split('_')[0]}_{arm}.json").read_text())
            art["arms"][arm]["judge"] = j
        rows.append((q, art))

    def acc(name):
        done = [(q, a) for q, a in rows
                if "error" not in a["arms"][name] and
                (a["arms"][name].get("final_answer") or "").strip()]
        sin = [(q, a) for q, a in done if q["span"] == "single"]
        mul = [(q, a) for q, a in done if q["span"] == "multi"]
        f = lambda xs: round(sum(a["arms"][name]["judge"]["correct"]
                                 for _, a in xs) / len(xs), 3) if xs else None
        return {"n": len(done), "accuracy": f(done),
                "single_accuracy": f(sin), "multi_accuracy": f(mul)}

    scores = json.loads((BASE / "scores.json").read_text())
    scores["arms"]["pageindex_flash"].update(acc("pageindex_flash"))
    scores["arms"]["flat_bm25"].update(acc("flat_bm25"))
    scores["rows"] = [
        {"idx": q["idx"], "span": q["span"],
         "pi_correct": a["arms"]["pageindex_flash"]["judge"]["correct"],
         "flat_correct": a["arms"]["flat_bm25"]["judge"]["correct"]}
        for q, a in rows]
    repair_usage = {
        "llm_calls": h.LANE.calls - repair_usage_before[0],
        "prompt_tokens": h.LANE.prompt_tokens - repair_usage_before[1],
        "completion_tokens": h.LANE.completion_tokens - repair_usage_before[2],
    }
    tu = scores["total_usage"]
    scores["total_usage"] = {
        k: tu.get(k, 0) + repair_usage.get(k, 0)
        for k in ("llm_calls", "prompt_tokens", "completion_tokens")}
    scores["repair"] = {
        "reason": "empty message.content (reasoning tokens consumed cap); "
                  "chat_once now falls back to reasoning_content + 4x retry",
        "reanswered": [s for s, _, _ in AFFECTED_ANSWERS],
        "rejudged": [f"{s}/{a}" for s, a in AFFECTED_JUDGES],
        "repair_usage": repair_usage,
        "note": "repair pass 1 (first 2 re-answers + 3 re-judges, 5 calls) "
                "crashed before its usage merge; those 5 calls' tokens are "
                "not in total_usage — pass 2 re-ran all 5 repairs and its "
                "usage IS included",
    }
    (BASE / "scores.json").write_text(json.dumps(scores, indent=2))
    print(json.dumps({"pageindex": acc("pageindex_flash"),
                      "flat": acc("flat_bm25")}, indent=2))


if __name__ == "__main__":
    main()
