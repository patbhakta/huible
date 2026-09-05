#!/usr/bin/env python3
"""Retrieval head-to-head: PageIndex Flash reasoning retrieval vs flat BM25.

Frame (HU-2723 reframe, founder field notes 2026-09-05): PageIndex Flash is
the reference baseline; the flat leg (BM25 over the same pages -> same
answerer -> same judge) is our system-under-test proxy. Multi-page-span
questions are a first-class slice (PageIndex's documented soft spot: content
spanning multiple pages is left to interpretation).

Corpus: the Flash-indexed 22-page Disney Q1 FY25 earnings PDF (existing store
from run_flash_smoke). Gold set authored by hand from the PDF text itself
(no LLM authored gold), with page citations. 5 single-section + 5
multi-page-span questions.

Arms (identical answer extraction + judge; only the retrieval leg differs):
  pageindex  client.chat() — agent navigates the tree via SDK tools
             (browse/structure/page-content) on glm-5.3-flash, z.ai lane.
  flat_bm25  page-level chunks (no cross-page merging) -> BM25 top-5 ->
             FRAMES-harness answer prompt on glm-5.3-flash.

Judge: glm-5.3, temperature 0, exact FRAMES prompt (BEAM discipline: never
swap the judge). All traffic serialized through one litellm.acompletion
wrapper with deterministic token counting + 429 backoff (z.ai coding lane).

Resumable: per-question/per-arm artifacts are flushed as JSON files; reruns
skip work whose artifact already exists. Run:  python run_retrieval_h2h.py
"""
from __future__ import annotations

import asyncio
import json
import math
import re
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import EVAL_DIR, FLASH_MODEL, set_zai_lane  # noqa: E402

RUN_ID = "h2h-r20260905"
OUT = EVAL_DIR / "outputs" / RUN_ID
STORE = EVAL_DIR / "outputs" / "smoke-flash-q1fy25" / ".pageindex"
DOC_ID = "pi-89de906d08274831a52ae113ff47c6e9"
PDF = Path("/root/repos/pageindex/examples/documents/q1-fy25-earnings.pdf")

JUDGE_MODEL = "openai/glm-5.3"  # BEAM discipline: never swap the judge
ANSWER_MODEL = FLASH_MODEL
TOP_K = 5

ANSWER_SYSTEM = (
    "You are a precise research assistant. Answer strictly from the reference "
    "material provided. Think step by step if needed, then end with exactly one "
    "line: 'Final answer: <short answer>'."
)

# Gold set authored from the PDF text (pages cited). span=multi requires
# combining content from 2+ distinct pages/sections.
GOLD = [
    {"idx": 1, "span": "single", "gold_pages": [1, 3],
     "question": "What were The Walt Disney Company's total revenues in Q1 fiscal 2025, and by what percentage did they change year-over-year?",
     "gold": "$24.7 billion ($24,690 million), up 5% from $23.5 billion",
     "aliases": ["24,690", "5%"]},
    {"idx": 2, "span": "single", "gold_pages": [1, 3],
     "question": "What was Disney's diluted EPS excluding certain items for Q1 fiscal 2025 and its year-over-year change?",
     "gold": "$1.76, up 44% from $1.22",
     "aliases": ["1.76", "44"]},
    {"idx": 3, "span": "single", "gold_pages": [7],
     "question": "What was the average monthly revenue per paid subscriber for Hulu Live TV + SVOD in the quarter ended December 28, 2024, and how did it change from the prior sequential quarter?",
     "gold": "$99.22, up from $95.82 (primarily due to increases in pricing)",
     "aliases": ["99.22", "95.82"]},
    {"idx": 4, "span": "single", "gold_pages": [12],
     "question": "How much did Disney invest in parks, resorts and other property in Q1 fiscal 2025, and what was the main driver of the increase versus the prior-year quarter?",
     "gold": "$2,466 million (~$2.5 billion, up from $1,299 million), driven by higher spend on cruise ship fleet expansion at the Experiences segment",
     "aliases": ["2,466", "cruise"]},
    {"idx": 5, "span": "single", "gold_pages": [11],
     "question": "What was net income attributable to noncontrolling interests in Q1 fiscal 2025, and what drove the year-over-year decrease?",
     "gold": "$(90) million versus $(240) million in the prior-year quarter; the decrease reflects the comparison to accretion of NBC Universal's interest in Hulu in the prior-year quarter",
     "aliases": ["(90)", "Hulu", "NBC"]},
    {"idx": 6, "span": "multi", "gold_pages": [1, 6],
     "question": "How did Disney's Direct-to-Consumer advertising revenue perform in Q1 fiscal 2025?",
     "gold": "Declined 2% year-over-year as reported; excluding the Disney+ Hotstar service in India, Direct-to-Consumer advertising revenue was up 16%",
     "aliases": ["2%", "16%", "Hotstar"]},
    {"idx": 7, "span": "multi", "gold_pages": [1, 6],
     "question": "How many Disney+ subscribers did Disney report and how did the count change versus the prior quarter (Q4 fiscal 2024)?",
     "gold": "125 million Disney+ subscribers, a decrease of 0.7 million versus Q4 fiscal 2024 (56.8 million domestic + 67.8 million international per the key metrics table)",
     "aliases": ["125 million", "0.7 million"]},
    {"idx": 8, "span": "multi", "gold_pages": [1, 9],
     "question": "How did the Experiences segment perform in Q1 fiscal 2025 and what one-time factors affected its results?",
     "gold": "Segment operating income of $3.1 billion, comparable to the prior year; adversely impacted ~$120 million by Hurricanes Milton and Helene (including a Walt Disney World closure and a canceled cruise itinerary) and ~$75 million of Disney Cruise Line pre-opening expenses",
     "aliases": ["3.1 billion", "120", "75", "hurricane"]},
    {"idx": 9, "span": "multi", "gold_pages": [2, 4],
     "question": "How will the India business contribute to Entertainment and Sports segment operating income in fiscal 2025, and how do those contributions compare to the prior year?",
     "gold": "Entertainment: $73 million in fiscal 2025 versus $254 million in the prior year; Sports: $9 million versus a $636 million loss in the prior year, following the Star India deconsolidation into the Reliance joint venture",
     "aliases": ["73", "254", "9", "636"]},
    {"idx": 10, "span": "multi", "gold_pages": [3, 11],
     "question": "What was Disney's free cash flow in Q1 fiscal 2025, how did it change year-over-year, and why?",
     "gold": "$739 million, down 17% from $886 million; free cash flow fell because higher investments in parks, resorts and other property ($2,466 million vs $1,299 million) more than offset a $1.0 billion increase in cash provided by operations",
     "aliases": ["739", "17%", "2,466"]},
]

JUDGE_TMPL = (
    "Question: {q}\nGold answer: {gold}\n"
    "Gold aliases: {aliases}\n"
    "Candidate answer: {ans}\n\n"
    "Is the candidate answer correct (semantically equivalent to the gold)? "
    "Reply with exactly one line: VERDICT: CORRECT or VERDICT: INCORRECT,\n"
    "then one short reason."
)


# --- one serialized, metered LLM lane for every call in this harness --------

class Lane:
    """Serialize all LLM calls (z.ai coding lane) + deterministic usage."""

    def __init__(self):
        self.lock = threading.Lock()
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.by_arm: dict[str, dict] = {}

    def add(self, arm: str, pt: int, ct: int, latency: float):
        self.calls += 1
        self.prompt_tokens += pt
        self.completion_tokens += ct
        b = self.by_arm.setdefault(
            arm, {"llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
                  "latency_s": 0.0})
        b["llm_calls"] += 1
        b["prompt_tokens"] += pt
        b["completion_tokens"] += ct
        b["latency_s"] += latency


LANE = Lane()
CURRENT_ARM = "other"  # attribution for SDK-internal calls (no _arm kwarg)


def install_lane_wrapper() -> None:
    """Wrap litellm.acompletion: global serialization + 429 backoff + usage.

    Covers every lane in this harness (PageIndex agent chat, flat answerer,
    judge) because both the SDK's utils.llm_acompletion and the agents
    LitellmModel resolve litellm.acompletion at call time.
    """
    import litellm

    orig = litellm.acompletion

    async def metered(model, messages=None, **kw):
        prompt = json.dumps(messages or kw.get("messages", ""), default=str)
        arm = kw.pop("_arm", CURRENT_ARM)
        for attempt in range(6):
            with LANE.lock:
                try:
                    t0 = time.time()
                    resp = await orig(model=model, messages=messages, **kw)
                    text = resp.choices[0].message.content or ""
                    pt = litellm.token_counter(model="gpt-4o", text=prompt)
                    ct = litellm.token_counter(model="gpt-4o", text=text)
                    LANE.add(arm, pt, ct, time.time() - t0)
                    return resp
                except Exception as e:  # noqa: BLE001
                    status = getattr(e, "status_code", None)
                    if status != 429 or attempt == 5:
                        raise
            wait = 5 * (2 ** attempt)
            print(f"[lane] 429, backing off {wait}s "
                  f"(attempt {attempt + 1}/6)", flush=True)
            time.sleep(wait)
        raise AssertionError("unreachable")

    litellm.acompletion = metered


def _content(resp) -> str:
    """Content with reasoning-model fallback.

    glm-5.3 (non-flash judge) and occasionally the flash answerer emit
    reasoning tokens; at a tight max_tokens cap `message.content` can come
    back empty while the payload sits in `message.reasoning_content`.
    """
    msg = resp.choices[0].message
    return (getattr(msg, "content", None) or
            getattr(msg, "reasoning_content", None) or "")


def chat_once(arm: str, model: str, system: str, user: str,
              temperature: float, max_tokens: int) -> str:
    import litellm

    async def _go(cap: int):
        resp = await litellm.acompletion(
            model=model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            temperature=temperature, max_tokens=cap,
            drop_params=True, max_retries=0, _arm=arm)
        return _content(resp).strip()

    body = asyncio.run(_go(max_tokens))
    if not body:  # token cap hit before any content: retry with headroom
        body = asyncio.run(_go(max_tokens * 4)).strip()
    return body


def judge_answer(q: dict, candidate: str) -> dict:
    body = chat_once(
        "judge", JUDGE_MODEL, "",
        JUDGE_TMPL.format(q=q["question"], gold=q["gold"],
                          aliases=", ".join(q["aliases"]) or "(none)",
                          ans=candidate),
        temperature=0.0, max_tokens=256)
    return {"verdict_raw": body,
            "correct": "verdict: correct" in body.lower()}


# --- flat arm: page-level chunks -> BM25 top-k ------------------------------

def page_chunks() -> list[dict]:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(PDF))
    return [{"page": i + 1,
             "text": pdf[i].get_textpage().get_text_range()}
            for i in range(len(pdf))]


def bm25_scores(query: str, chunks: list[dict]) -> list[float]:
    tok = lambda s: re.findall(r"[a-z0-9]+", s.lower())  # noqa: E731
    q = tok(query)
    docs = [tok(c["text"]) for c in chunks]
    n = len(docs)
    avgdl = sum(len(d) for d in docs) / n
    df: dict[str, int] = {}
    for d in docs:
        for term in set(d):
            df[term] = df.get(term, 0) + 1
    k1, b = 1.5, 0.75
    scores = []
    for d in docs:
        tf: dict[str, int] = {}
        for term in d:
            tf[term] = tf.get(term, 0) + 1
        s = 0.0
        for term in q:
            if term not in df:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            s += idf * (tf.get(term, 0) * (k1 + 1)) / (
                tf.get(term, 0) + k1 * (1 - b + b * len(d) / avgdl))
        scores.append(s)
    return scores


def run_flat(q: dict, chunks: list[dict]) -> dict:
    scores = bm25_scores(q["question"], chunks)
    top = sorted(range(len(chunks)), key=lambda i: scores[i],
                 reverse=True)[:TOP_K]
    ctx = "\n\n".join(
        f"[page {chunks[i]['page']}]\n{chunks[i]['text']}" for i in top)
    raw = chat_once("flat", ANSWER_MODEL, ANSWER_SYSTEM,
                    f"Reference material:\n\n{ctx}\n\nQuestion: "
                    f"{q['question']}", temperature=0.2, max_tokens=768)
    m = re.search(r"final answer:\s*(.+)", raw, re.IGNORECASE | re.DOTALL)
    final = (m.group(1) if m else raw).strip()
    cited = sorted({chunks[i]["page"] for i in top})
    return {"arm": "flat_bm25", "final_answer": final, "raw": raw,
            "retrieved_pages": cited,
            "hit_gold_pages": bool(set(cited) & set(q["gold_pages"]))}


# --- pageindex arm: SDK reasoning retrieval ---------------------------------

def run_pageindex(client, q: dict) -> dict:
    global CURRENT_ARM
    t0 = time.time()
    CURRENT_ARM = "pageindex_flash"
    try:
        ans = client.chat(q["question"], doc_id=DOC_ID, max_turns=8)
    finally:
        CURRENT_ARM = "other"
    return {"arm": "pageindex_flash", "final_answer": (ans or "").strip(),
            "wall_s": round(time.time() - t0, 1)}


# --- driver -----------------------------------------------------------------

def main() -> None:
    set_zai_lane()
    install_lane_wrapper()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "stage_answers").mkdir(exist_ok=True)
    (OUT / "stage_judge").mkdir(exist_ok=True)
    (OUT / "gold_questions.json").write_text(json.dumps(GOLD, indent=2))

    chunks = page_chunks()
    from pageindex import PageIndexLocalClient

    client = PageIndexLocalClient(storage_path=str(STORE),
                                  chat_model=ANSWER_MODEL)

    rows = []
    for q in GOLD:
        row = {"idx": q["idx"], "span": q["span"],
               "question": q["question"], "gold": q["gold"]}
        a_path = OUT / "stage_answers" / f"q{q['idx']:03d}_{q['span']}.json"
        if a_path.exists():
            a = json.loads(a_path.read_text())
        else:
            arms = {}
            try:
                arms["pageindex_flash"] = run_pageindex(client, q)
            except Exception as e:  # noqa: BLE001
                arms["pageindex_flash"] = {"arm": "pageindex_flash",
                                           "error": repr(e)[:300]}
            try:
                arms["flat_bm25"] = run_flat(q, chunks)
            except Exception as e:  # noqa: BLE001
                arms["flat_bm25"] = {"arm": "flat_bm25",
                                     "error": repr(e)[:300]}
            a = {"idx": q["idx"], "span": q["span"], "arms": arms}
            a_path.write_text(json.dumps(a, indent=2))
        row["arms"] = a["arms"]

        for arm_name, arm in a["arms"].items():
            j_path = OUT / "stage_judge" / (
                f"q{q['idx']:03d}_{arm_name}.json")
            if "error" in arm:
                arm["judge"] = {"correct": False, "error": "arm failed"}
                continue
            if j_path.exists():
                arm["judge"] = json.loads(j_path.read_text())
            else:
                arm["judge"] = judge_answer(q, arm["final_answer"])
                j_path.write_text(json.dumps(arm["judge"], indent=2))
        rows.append(row)
        print(f"[q{q['idx']:02d} {q['span']}] "
              f"pi={'OK' if 'error' not in row['arms']['pageindex_flash'] else 'ERR'}"
              f"/flat={'OK' if 'error' not in row['arms']['flat_bm25'] else 'ERR'} "
              f"judge pi={row['arms']['pageindex_flash']['judge']['correct']} "
              f"flat={row['arms']['flat_bm25']['judge']['correct']}",
              flush=True)

    def arm_summary(name: str) -> dict:
        done = [r for r in rows if "error" not in r["arms"][name]]
        forsl = [r for r in done if r["span"] == "single"]
        formu = [r for r in done if r["span"] == "multi"]
        usage_key = {"pageindex_flash": "pageindex_flash",
                     "flat_bm25": "flat"}[name]
        usage = dict(LANE.by_arm.get(usage_key, {}))
        usage["tokens"] = usage.get("prompt_tokens", 0) + \
            usage.get("completion_tokens", 0)
        return {
            "n": len(done),
            "correct": sum(r["arms"][name]["judge"]["correct"] for r in done),
            "accuracy": round(sum(r["arms"][name]["judge"]["correct"]
                                  for r in done) / len(done), 3) if done else None,
            "single_accuracy": round(sum(r["arms"][name]["judge"]["correct"]
                                         for r in forsl) / len(forsl), 3)
                             if forsl else None,
            "multi_accuracy": round(sum(r["arms"][name]["judge"]["correct"]
                                        for r in formu) / len(formu), 3)
                              if formu else None,
            "usage": usage,
        }

    summary = {
        "run_id": RUN_ID,
        "corpus": {"pdf": PDF.name, "pages": len(chunks),
                   "doc_id": DOC_ID,
                   "index_run": "smoke-flash-q1fy25"},
        "arms": {"pageindex_flash": arm_summary("pageindex_flash"),
                 "flat_bm25": arm_summary("flat_bm25")},
        "flat_config": {"chunking": "page-level (no cross-page merging)",
                        "retriever": "bm25 (k1=1.5 b=0.75), top-5",
                        "answer_model": ANSWER_MODEL,
                        "answer_temperature": 0.2},
        "pageindex_config": {"chat_model": ANSWER_MODEL, "max_turns": 8,
                             "retrieval": "SDK agent tree navigation"},
        "judge": {"model": JUDGE_MODEL, "temperature": 0.0,
                  "prompt": "FRAMES harness parity"},
        "gold_set": {"n": len(GOLD),
                     "single": sum(1 for g in GOLD if g["span"] == "single"),
                     "multi": sum(1 for g in GOLD if g["span"] == "multi"),
                     "authored_by": "hand, from PDF text (no LLM)"},
        "total_usage": {"llm_calls": LANE.calls,
                        "prompt_tokens": LANE.prompt_tokens,
                        "completion_tokens": LANE.completion_tokens},
        "rows": [{"idx": r["idx"], "span": r["span"],
                  "pi_correct": r["arms"]["pageindex_flash"]["judge"]["correct"],
                  "flat_correct": r["arms"]["flat_bm25"]["judge"]["correct"]}
                 for r in rows],
    }
    (OUT / "scores.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps({k: summary[k] for k in
                      ("arms", "total_usage")}, indent=2))


if __name__ == "__main__":
    main()
