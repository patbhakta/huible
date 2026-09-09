#!/usr/bin/env python3
"""HU-1911 sprint 1: Chandler transcript corpus assembly + LLM eval (quality & quantity).

Assembles real persona-chat replies from committed evidence files, computes
quantity stats vs the friends-v2.csv canon baseline, and scores each reply
with glm-5.3-flash as judge (same-judge rule) on coherence + in-character.

Usage:
  python scripts/hu1911_sprint1_eval.py [--skip-judge]

Reads GLM_API_KEY from the environment (judge model glm-5.3-flash).
Outputs:
  docs/evidence/hu1911_sprint1_transcripts.json   assembled corpus
  docs/evidence/hu1911_sprint1_eval_results.json  quantity stats + per-reply scores
  docs/evidence/hu1911_sprint1_scorecard.md       human-readable scorecard
"""

import argparse
import csv
import json
import os
import statistics
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs" / "evidence"
CORPUS_CSV = ROOT / "onboarding" / "Chandler Bing - FRIENDS sitcom" / "friends-v2.csv"

JUDGE_MODEL = "glm-5.3-flash"
JUDGE_BASE_URL = os.environ.get("GLM_BASE_URL", "https://api.z.ai/api/coding/paas/v4")

SOURCES = [
    ("stagea_dogfood_20260831", EVIDENCE / "hu1911_stagea_dogfood_epoch40dda40cfcca_20260831T1438Z.json"),
    ("h1_m0_replay_20260907", EVIDENCE / "m1" / "h1-results.json"),
    ("h2_ai_tell_probes_20260907", EVIDENCE / "m1" / "h2-results.jsonl"),
]


def pct(values, p):
    if not values:
        return None
    values = sorted(values)
    k = (len(values) - 1) * p / 100.0
    f = int(k)
    c = min(f + 1, len(values) - 1)
    return round(values[f] + (values[c] - values[f]) * (k - f), 1)


def extract_replies():
    """Pull (source, user, reply) tuples from committed evidence files."""
    turns = []

    # stagea dogfood: persona_turns[].user / .response
    stagea = json.loads(SOURCES[0][1].read_text())
    for t in stagea.get("persona_turns", []):
        if t.get("response") and t.get("user"):
            turns.append({"source": SOURCES[0][0], "user": t["user"].strip(),
                          "reply": t["response"].strip(), "probe": t.get("probe")})

    # h1: turns[].user / .reply
    h1 = json.loads(SOURCES[1][1].read_text())
    for t in h1.get("turns", []):
        if t.get("reply") and t.get("user"):
            turns.append({"source": SOURCES[1][0], "user": t["user"].strip(),
                          "reply": t["reply"].strip(), "probe": "turn{}".format(t.get("turn"))})

    # h2: concatenated JSON objects; walk for dicts with user+reply
    dec = json.JSONDecoder()
    raw = SOURCES[2][1].read_text()
    idx, recs = 0, []
    while idx < len(raw):
        while idx < len(raw) and raw[idx] in " \n\r\t":
            idx += 1
        if idx >= len(raw):
            break
        obj, idx = dec.raw_decode(raw, idx)
        recs.append(obj)
    for rec in recs:
        stack = [rec]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                if node.get("reply") and node.get("user"):
                    turns.append({"source": SOURCES[2][0], "user": node["user"].strip(),
                                  "reply": node["reply"].strip(), "probe": node.get("probe")})
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
    return turns


def canon_baseline():
    """Stats over Chandler's own lines in friends-v2.csv."""
    lengths = []
    questions = 0
    total = 0
    with CORPUS_CSV.open(newline="") as f:
        for row in csv.DictReader(f):
            if (row.get("person") or "").strip().lower() != "chandler":
                continue
            line = (row.get("line") or "").strip()
            if not line:
                continue
            total += 1
            lengths.append(len(line))
            if line.endswith("?"):
                questions += 1
    return {
        "chandler_lines": total,
        "mean_chars": round(statistics.mean(lengths), 1),
        "median_chars": statistics.median(lengths),
        "p95_chars": pct(lengths, 95),
        "p99_chars": pct(lengths, 99),
        "question_ratio": round(questions / total, 4),
    }


def judge_reply(api_key, user, reply):
    """One glm-5.3-flash judge call. Returns (coherence, in_character, note)."""
    system = (
        "You are an impartial eval judge. You score replies from a chatbot whose persona is "
        "Chandler Bing from the sitcom Friends: sarcastic wit, humor as deflection, "
        "self-deprecating, avoids earnest assistant-speak, references his canon life "
        "(friends group, Monica, his jobs, Tulsa) when relevant. "
        "Score ONLY, never rewrite. Reply with strict JSON, no other text: "
        '{"coherence": <int 1-10>, "in_character": <int 1-10>, "note": "<=15 words"} '
        "where coherence = the reply makes sense as an answer to the user's message, and "
        "in_character = it plausibly sounds like Chandler Bing (not a generic AI assistant, "
        "not another character)."
    )
    body = {
        "model": JUDGE_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": f"USER: {user}\n\nPERSONA REPLY: {reply}"},
        ],
        "temperature": 0,
    }
    req = urllib.request.Request(
        JUDGE_BASE_URL.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    last_err = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = json.loads(resp.read())
            content = data["choices"][0]["message"]["content"].strip()
            if content.startswith("```"):
                content = content.strip("`")
                if content.startswith("json"):
                    content = content[4:]
            verdict = json.loads(content)
            return {
                "coherence": int(verdict["coherence"]),
                "in_character": int(verdict["in_character"]),
                "note": str(verdict.get("note", ""))[:120],
            }
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            time.sleep(2 * (attempt + 1))
    return {"coherence": None, "in_character": None, "note": f"judge_error: {last_err}"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-judge", action="store_true")
    args = ap.parse_args()

    turns = extract_replies()
    print(f"assembled {len(turns)} real persona replies from {len(SOURCES)} evidence sources")

    corpus = {
        "assembled_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "purpose": "HU-1911 sprint 1 transcript corpus (real Chandler persona chat logs)",
        "persona_id": "fdc3a44b-4c0f-565d-b671-4ed0e3bc7894",
        "canon_reference_csv": str(CORPUS_CSV.relative_to(ROOT)),
        "sources": {name: str(path.relative_to(ROOT)) for name, path in SOURCES},
        "excluded": [
            "voice_dogfood_chandler_20260819T090208Z.json (reply_previews truncated, not full text)"
        ],
        "turns": turns,
    }
    corpus_path = EVIDENCE / "hu1911_sprint1_transcripts.json"
    corpus_path.write_text(json.dumps(corpus, indent=1))
    print(f"wrote {corpus_path}")

    base = canon_baseline()
    lengths = [len(t["reply"]) for t in turns]
    per_source = {}
    for name, _ in SOURCES:
        src = [t for t in turns if t["source"] == name]
        per_source[name] = {
            "turns": len(src),
            "mean_chars": round(statistics.mean([len(t["reply"]) for t in src]), 1) if src else 0,
        }
    quantity = {
        "canon_baseline_friends_v2": base,
        "real_transcripts": {
            "total_turns": len(turns),
            "sessions": len(set(t["source"] for t in turns)),
            "per_source": per_source,
            "mean_chars": round(statistics.mean(lengths), 1),
            "median_chars": statistics.median(lengths),
            "p95_chars": pct(lengths, 95),
            "p99_chars": pct(lengths, 99),
            "question_ratio": round(sum(1 for t in turns if t["reply"].rstrip().endswith("?")) / len(turns), 4),
        },
    }
    print(json.dumps(quantity, indent=1))

    results = {"judged_at": None, "judge_model": JUDGE_MODEL, "quantity": quantity, "scores": []}
    if not args.skip_judge:
        api_key = os.environ.get("GLM_API_KEY")
        if not api_key:
            sys.exit("ERROR: GLM_API_KEY not set in environment")
        scores = [None] * len(turns)

        def work(i):
            t = turns[i]
            return i, judge_reply(api_key, t["user"], t["reply"])

        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(work, i) for i in range(len(turns))]
            done = 0
            for fut in as_completed(futures):
                i, verdict = fut.result()
                scores[i] = verdict
                done += 1
                if done % 10 == 0:
                    print(f"  judged {done}/{len(turns)}")
        results["scores"] = scores
        results["judged_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        coh = [s["coherence"] for s in scores if s["coherence"] is not None]
        ico = [s["in_character"] for s in scores if s["in_character"] is not None]
        results["summary"] = {
            "judged": len(coh),
            "judge_errors": len(scores) - len(coh),
            "coherence_mean": round(statistics.mean(coh), 2) if coh else None,
            "in_character_mean": round(statistics.mean(ico), 2) if ico else None,
            "coherence_min": min(coh) if coh else None,
            "in_character_min": min(ico) if ico else None,
        }
        print(json.dumps(results["summary"], indent=1))

    results_path = EVIDENCE / "hu1911_sprint1_eval_results.json"
    results_path.write_text(json.dumps(results, indent=1))
    print(f"wrote {results_path}")


if __name__ == "__main__":
    main()
