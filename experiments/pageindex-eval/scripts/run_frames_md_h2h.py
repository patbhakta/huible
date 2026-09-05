#!/usr/bin/env python3
"""FRAMES corpus pass: PageIndex agent retrieval over the wiki corpus.

Closes the "measured FRAMES numbers vs baseline" deliverable (HU-2723):
r20260905b's flat pipeline scored 12/20 = 0.60 on this exact sample and
corpus. This run gives PageIndex Flash the same 20 questions over the same
79-article corpus and judges with the same glm-5.3 judge.

Corpus ingestion: each wiki article becomes a markdown doc (harness-side;
plaintext has no native structure). md_to_tree builds the tree with node
summaries ON (glm-5.3-flash — Flash product behavior), LLM cost metered as
arm "pi_frames_index". Markdown line ranges map to pseudo-pages (60
lines/page) so the SDK's page-based agent tools work unmodified; docs are
inserted via LocalStore.save_document (public API, no fork). Doc
descriptions are set by hand ("Wikipedia article: <title>") — one fewer
LLM call per doc, recorded as a deviation from the earnings-PDF run.

Retrieval: client.chat() per question (agent navigates docs/structures/
pages), glm-5.3-flash on the z.ai lane, max 8 turns. Judge: glm-5.3, temp
0, exact FRAMES prompt, same as both prior arms.

Resumable: per-doc index artifacts + per-question artifacts flush as JSON;
reruns skip completed units. Run:
    python run_frames_md_h2h.py          # index (idempotent) + retrieve + judge
    python run_frames_md_h2h.py --index-only
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import run_retrieval_h2h as h

RUN_ID = "frames-md-r20260905"
OUT = h.EVAL_DIR / "outputs" / RUN_ID
STORE = OUT / "store"
CORPUS = (h.EVAL_DIR.parent / "frames-eval" / "outputs" / "r20260905b"
          / "stage0_corpus")
BASELINE = (h.EVAL_DIR.parent / "frames-eval" / "outputs" / "r20260905b"
            / "scores.json")
LINES_PER_PAGE = 60
MAX_TURNS = int(os.environ.get("PI_MAX_TURNS", "8"))


def title_from_url(url: str) -> str:
    seg = urllib.parse.unquote(url.rsplit("/wiki/", 1)[-1])
    return seg.replace("_", " ")


def chunk_pages(md_lines: list[str]) -> list[dict]:
    pages = []
    for start in range(0, len(md_lines), LINES_PER_PAGE):
        pages.append({
            "page_index": start // LINES_PER_PAGE + 1,
            "markdown": "\n".join(md_lines[start:start + LINES_PER_PAGE]),
        })
    return pages


def line_to_page(line_num, n_pages: int) -> int:
    if line_num is None:
        return n_pages
    return min(max(1, int(line_num)) // LINES_PER_PAGE + 1, n_pages)


def set_spans(nodes: list, n_pages: int) -> None:
    """Map markdown line_num spans onto pseudo-page indices, DFS order."""
    ordered = []

    def walk(ns):
        for n in ns:
            walk(n.get("nodes") or [])
            ordered.append(n)

    walk(nodes)
    for i, n in enumerate(ordered):
        start = line_to_page(n.get("line_num"), n_pages)
        if i + 1 < len(ordered):
            nxt = ordered[i + 1].get("line_num")
            end = max(start, line_to_page(nxt, n_pages)) \
                if nxt is not None else start
        else:
            end = n_pages
        n["start_index"] = start
        n["end_index"] = end
        for k in ("line_num", "prefix_summary", "text"):
            n.pop(k, None)


def index_article(store, title: str, art: dict) -> dict:
    import uuid

    from pageindex.local_store import DocStore as LocalStore
    from pageindex.page_index_md import md_to_tree

    md_path = OUT / "md" / f"{art['pageid']}.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_text = f"# {title}\n\n{art['text']}\n"
    md_path.write_text(md_text)
    md_lines = md_text.splitlines()
    n_pages = (len(md_lines) + LINES_PER_PAGE - 1) // LINES_PER_PAGE

    t0 = time.time()
    prev = h.CURRENT_ARM
    h.CURRENT_ARM = "pi_frames_index"
    try:
        tree = asyncio.run(md_to_tree(
            md_path=str(md_path),
            if_add_node_summary="yes", summary_token_threshold=200,
            summary_model=h.ANSWER_MODEL,
            if_add_doc_description="no", if_add_node_text="no",
            if_add_node_id="yes"))
    finally:
        h.CURRENT_ARM = prev

    structure = tree["structure"]
    set_spans(structure, n_pages)
    doc_id = "pi-" + uuid.uuid4().hex
    meta = {
        "id": doc_id, "name": title,
        "description": f"Wikipedia article: {title}",
        "status": "completed", "createdAt": "",
        "pageNum": n_pages, "folderId": None, "metadata": None,
        "mode": "flash-md",
    }
    with store.lock():
        store.save_document(doc_id, meta, structure,
                            chunk_pages(md_lines))
    return {"doc_id": doc_id, "pages": n_pages,
            "wall_s": round(time.time() - t0, 1)}


def main() -> None:
    index_only = "--index-only" in sys.argv
    h.set_zai_lane()
    h.install_lane_wrapper()
    for sub in ("stage_answers", "stage_judge", "md"):
        (OUT / sub).mkdir(parents=True, exist_ok=True)

    articles = json.loads((CORPUS / "articles.json").read_text())
    sample = json.loads((CORPUS / "sample.json").read_text())

    from pageindex.local_store import DocStore as LocalStore

    store = LocalStore(str(STORE))
    known = store._read_manifest()
    by_name = {m["name"]: d for d, m in known.items()}

    index_meta = {"docs": {}, "started_with": len(known)}
    for title, art in articles.items():
        if title in by_name:
            continue
        info = index_article(store, title, art)
        index_meta["docs"][title] = info
        print(f"[index] {title[:40]} pages={info['pages']} "
              f"wall={info['wall_s']}s", flush=True)
    (OUT / "index_meta.json").write_text(json.dumps(index_meta, indent=2))
    if index_only:
        return

    from pageindex import PageIndexLocalClient

    client = PageIndexLocalClient(storage_path=str(STORE),
                                  chat_model=h.ANSWER_MODEL)

    rows = []
    for q in sample:
        qidx = q["_idx"]
        stem = f"q{qidx:03d}"
        gold_titles = sorted({title_from_url(u) for u in
                              (q.get("wiki_links") or []) if u})
        a_path = OUT / "stage_answers" / f"{stem}.json"
        if a_path.exists():
            a = json.loads(a_path.read_text())
        else:
            t0 = time.time()
            h.CURRENT_ARM = "pageindex_frames"
            try:
                ans = client.chat(q["Prompt"], max_turns=MAX_TURNS)
                arm = {"arm": "pageindex_frames",
                       "final_answer": (ans or "").strip(),
                       "wall_s": round(time.time() - t0, 1)}
            except Exception as e:  # noqa: BLE001
                arm = {"arm": "pageindex_frames", "error": repr(e)[:300]}
            finally:
                h.CURRENT_ARM = "other"
            a = {"idx": qidx, "question": q["Prompt"], "gold": q["Answer"],
                 "gold_titles": gold_titles, "arms": {"pageindex_frames": arm}}
            a_path.write_text(json.dumps(a, indent=2))

        arm = a["arms"]["pageindex_frames"]
        j_path = OUT / "stage_judge" / f"{stem}.json"
        if "error" in arm:
            arm["judge"] = {"correct": False, "error": "arm failed"}
        elif j_path.exists():
            arm["judge"] = json.loads(j_path.read_text())
        else:
            arm["judge"] = h.judge_answer(
                {"question": q["Prompt"], "gold": q["Answer"], "aliases": []},
                arm["final_answer"])
            j_path.write_text(json.dumps(arm["judge"], indent=2))
        rows.append(a)
        print(f"[q{qidx:03d}] correct={arm['judge'].get('correct')} "
              f"wall={arm.get('wall_s', 'ERR')}", flush=True)

    baseline = json.loads(BASELINE.read_text())
    base_rows = {r["question_idx"]: r for r in baseline["rows"]} \
        if baseline.get("rows") else {}
    done = [a for a in rows if "error" not in a["arms"]["pageindex_frames"]]
    correct = sum(a["arms"]["pageindex_frames"]["judge"]["correct"]
                  for a in done)

    summary = {
        "run_id": RUN_ID,
        "question": "PageIndex Flash agent retrieval vs flat pipeline "
                    "(r20260905b) on the same 20-question FRAMES sample",
        "corpus": {"articles": len(articles), "store": str(STORE),
                   "pseudo_pages": LINES_PER_PAGE},
        "pageindex_frames": {
            "n": len(done), "correct": correct,
            "accuracy": round(correct / len(done), 3) if done else None,
            "usage": h.LANE.by_arm.get("pageindex_frames", {}),
            "index_usage": h.LANE.by_arm.get("pi_frames_index", {}),
        },
        "baseline_flat_r20260905b": {
            "judge_correct": baseline.get("judge_correct"),
            "judge_accuracy": baseline.get("judge_accuracy"),
            "provenance_ok": baseline.get("provenance_ok"),
            "avg_context_chars": baseline.get("avg_context_chars"),
        },
        "judge": {"model": h.JUDGE_MODEL, "temperature": 0.0,
                  "prompt": "FRAMES harness parity"},
        "rows": [{"idx": a["idx"],
                  "pi_correct": a["arms"]["pageindex_frames"]["judge"]["correct"],
                  "baseline_correct": base_rows.get(a["idx"], {}).get("correct")}
                 for a in rows],
    }
    (OUT / "scores.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary["pageindex_frames"], indent=2))


if __name__ == "__main__":
    main()
