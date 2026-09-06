#!/usr/bin/env python3
"""HU-2708 vector-only ablation — RECALL HALF ONLY (zero LLM spend).

Boots nothing itself: requires the read-only embedding-only gateway (see
launch_vectoronly_gateway.sh) already serving on --gateway. For every question
in the base run's sample, posts the same /recall request the harness `run`
stage would (same query cap, same session_key -> same quarantined corpus
scope) and stores traces in the exact stage3_retrieval format. No answerer,
no judge, no LLM calls of any kind.

Then reports, per question, whether the gold distinctive phrases (from
miss_attribution.GOLD_PHRASES) are present in the vector-only context vs the
stored prod (Arm A) context — a direct measurement of how much of the
in-window ranking failures a single embedding lane recovers at recall time.
This bounds the pending full-ablation outcome before any LLM spend.
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import frames_harness as fh
from miss_attribution import GOLD_PHRASES, flatten

RUN = "r20260905b"
RUN_DIR = Path(__file__).resolve().parent.parent / "outputs" / RUN
OUT_DIR = Path(__file__).resolve().parent.parent / "outputs" / "r20260905b-vectoronly-recall"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gateway", default="http://127.0.0.1:8421")
    ap.add_argument("--skip-recall", action="store_true",
                    help="reuse stored traces, only redo the presence analysis")
    args = ap.parse_args()

    raw = {r["_idx"]: r for r in json.loads((RUN_DIR / "stage0_corpus" / "sample.json").read_text())}
    rows = []
    for row in json.loads((RUN_DIR / "stage0_corpus" / "sample_articles.json").read_text()):
        src = raw.get(row["question_idx"], {})
        rows.append({**row,
                     "qw": src.get("Prompt") or src.get("qw") or "",
                     "gold": (src.get("Answer") or src.get("a") or "").strip()})
    rows.sort(key=lambda r: r["question_idx"])

    if not args.skip_recall:
        (OUT_DIR / "stage3_retrieval").mkdir(parents=True, exist_ok=True)
        fh.GATEWAY_BASE = args.gateway.rstrip("/")
        for row in rows:
            qi = row["question_idx"]
            q = row["qw"]
            sid = fh.session_id(RUN, qi)
            rec = fh.recall(q, sid)
            ctx = rec.get("prepend_context") or ""
            trace = {
                "question_idx": qi, "session_id": sid, "query": q[:fh.QUERY_CAP],
                "strategy": rec.get("strategy"), "memory_count": rec.get("memory_count"),
                "context_chars": len(ctx), "gateway_code": rec.get("code"),
                "context": ctx, "full_response_keys": sorted(rec.keys()),
                "arm": "vectoronly", "gateway": fh.GATEWAY_BASE,
            }
            (OUT_DIR / "stage3_retrieval" / f"q{qi:03d}_trace.json").write_text(
                json.dumps(trace, indent=1))
            print(f"q{qi:03d}: strategy={rec.get('strategy')} "
                  f"mem={rec.get('memory_count')} ctx={len(ctx)}ch code={rec.get('code')}")
            time.sleep(0.3)

    # --- presence analysis: vector-only vs prod Arm A -----------------------
    report = {"run": RUN, "arm": "vectoronly", "gateway": args.gateway,
              "method": "GOLD_PHRASES presence (miss_attribution) in vector-only "
                        "recall context vs stored prod stage3 context; zero LLM spend",
              "rows": []}
    for row in rows:
        qi = row["question_idx"]
        vo = json.loads((OUT_DIR / "stage3_retrieval" / f"q{qi:03d}_trace.json").read_text())
        prod = json.loads((RUN_DIR / "stage3_retrieval" / f"q{qi:03d}_trace.json").read_text())
        entry = {
            "question_idx": qi,
            "vo_strategy": vo.get("strategy"), "vo_memory_count": vo.get("memory_count"),
            "vo_context_chars": vo.get("context_chars"),
            "prod_strategy": prod.get("strategy"), "prod_memory_count": prod.get("memory_count"),
            "prod_context_chars": prod.get("context_chars"),
        }
        vo_ctx = flatten(vo.get("context") or "")
        prod_ctx = flatten(prod.get("context") or "")
        if qi in GOLD_PHRASES:
            parts = GOLD_PHRASES[qi]
            entry["gold_parts_total"] = len(parts)
            entry["vo_parts_hit"] = sum(any(ph in vo_ctx for ph in part) for part in parts)
            entry["prod_parts_hit"] = sum(any(ph in prod_ctx for ph in part) for part in parts)
            entry["vo_hit_phrases"] = [ph for part in parts for ph in part if ph in vo_ctx]
        else:
            # supplementary metric for the 12 judged-correct questions: is the
            # raw gold string present in each lane's context?
            gold_flat = flatten(row["gold"])
            entry["gold_string"] = row["gold"]
            entry["vo_gold_present"] = bool(gold_flat) and gold_flat in vo_ctx
            entry["prod_gold_present"] = bool(gold_flat) and gold_flat in prod_ctx
        # how much do the two lanes return the same chunks? (provenance ids,
        # format `[[Title |qN docM chunkK]]` per HU-1839 landing)
        pat = re.compile(r"\[\[[^|\]]+\s*\|\s*(q\d+ doc\d+(?:/\d+)? chunk\d+)\]\]")
        vo_ids = {m.group(1) for m in pat.finditer(vo.get("context") or "")}
        prod_ids = {m.group(1) for m in pat.finditer(prod.get("context") or "")}
        union = vo_ids | prod_ids
        entry["vo_prov_chunks"] = len(vo_ids)
        entry["prod_prov_chunks"] = len(prod_ids)
        entry["chunk_jaccard"] = (len(vo_ids & prod_ids) / len(union)) if union else None
        report["rows"].append(entry)

    (OUT_DIR / "recall_presence.json").write_text(json.dumps(report, indent=1))
    print("\n== gold-phrase presence: vector-only vs prod ==")
    for e in report["rows"]:
        if "gold_parts_total" in e:
            print(f"q{e['question_idx']:03d}: vo {e['vo_parts_hit']}/{e['gold_parts_total']}"
                  f"  prod {e['prod_parts_hit']}/{e['gold_parts_total']}  {e['vo_hit_phrases']}")
    print("\n== judged-correct questions: gold string presence ==")
    for e in report["rows"]:
        if "vo_gold_present" in e:
            print(f"q{e['question_idx']:03d}: vo {e['vo_gold_present']}  prod {e['prod_gold_present']}"
                  f"  gold={e['gold_string']!r}")
    jacs = [e["chunk_jaccard"] for e in report["rows"] if e["chunk_jaccard"] is not None]
    if jacs:
        print(f"\nchunk overlap jaccard: mean={sum(jacs)/len(jacs):.3f} "
              f"min={min(jacs):.3f} max={max(jacs):.3f} over {len(jacs)} questions")
    else:
        print("\nchunk overlap jaccard: n/a (one lane returned no provenance chunks)")
    tot_vo = sum(e["vo_prov_chunks"] for e in report["rows"])
    tot_prod = sum(e["prod_prov_chunks"] for e in report["rows"])
    print(f"provenance chunks surfaced: prod={tot_prod} vectoronly={tot_vo}")
    print(f"\nartifact: {OUT_DIR / 'recall_presence.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
