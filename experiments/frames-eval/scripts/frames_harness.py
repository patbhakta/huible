#!/usr/bin/env python3
"""FRAMES eval on the HUible stack (HU-2708) — isolated cleanroom harness.

Boss directive (Pat, 2026-09-05): the FRAMES benchmark run must exercise the
proprietary recipe end-to-end — TencentDB read path (BEAM Arm A), tiered
landing vault, provenance per HU-1383. No generic RAG substitution.

ISOLATION DOCTRINE (JARVIS, 2026-09-05, binding):
1. Own eval vault: everything lands under this experiment's outputs/<run>/
   stage2_landing/ (VaultWriter two-tier layout). The company knowledge vault
   is never touched.
2. Per-stage artifacts: stage0_corpus .. stage5_judge stay separated so a
   weak score attributes to a stage (ingest vs retrieve vs answer).
3. TencentDB scope isolation: team_id="frames-eval",
   agent_id="frames-<runid>", per-question session_id="frames-<runid>-qNNN".
   Distinct from default / beam-* / huible-* scopes (BEAM cleanroom discipline).
4. Teardown: `teardown` subcommand deletes the run's gateway sessions when the
   gateway exposes a delete route; otherwise the run manifest records the
   quarantined scope ids. Only the Librarian outcome doc ever reaches the
   company vault.

Usage (from repo root):
    python experiments/frames-eval/scripts/frames_harness.py download --run r20260905a
    python experiments/frames-eval/scripts/frames_harness.py ingest   --run r20260905a
    python experiments/frames-eval/scripts/frames_harness.py run      --run r20260905a
    python experiments/frames-eval/scripts/frames_harness.py teardown --run r20260905a
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import json
import re
import sys
import time
import traceback
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from huible.llm.client import LLMConfig, ZaiLLMClient  # noqa: E402
from huible.vault_ingest.atoms import Tier, VaultWriter, atom_from  # noqa: E402

FRAMES_URL = "https://huggingface.co/datasets/google/frames-benchmark/resolve/main/test.tsv"
WIKI_API = "https://en.wikipedia.org/w/api.php"
WIKI_UA = "HUible-FRAMES-eval/0.1 (research; contact: rnd@huible.local)"
GATEWAY = "http://127.0.0.1:8420"
GATEWAY_BASE = GATEWAY  # overridable at run time (ablation arms point at a 2nd instance)
GW_CONFIG = Path("/opt/tencentdb-memory/.config/tdai-gateway.yaml")
TEAM_ID = "frames-eval"
SEED = 2708
CHUNK_TARGET = 5500  # chars; gateway hard cap is 8192/message
QUERY_CAP = 2000  # parity with the prod client's query cap

RUN_DIR_TPL = "experiments/frames-eval/outputs/{run}"
STAGES = (
    "stage0_corpus", "stage1_extraction", "stage2_landing",
    "stage3_retrieval", "stage4_answers", "stage5_judge",
)

JUDGE_MODEL = "glm-5.3"  # BEAM discipline: never swap the judge


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def run_dir(run: str) -> Path:
    d = REPO_ROOT / RUN_DIR_TPL.format(run=run)
    for s in STAGES:
        (d / s).mkdir(parents=True, exist_ok=True)
    return d


def load_env_file() -> dict[str, str]:
    env: dict[str, str] = {}
    p = REPO_ROOT / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip().strip('"')
    return env


def gateway_key() -> str | None:
    import os

    key = os.environ.get("TDAI_GATEWAY_API_KEY", "").strip()
    if key:
        return key
    if GW_CONFIG.exists():
        m = re.search(r'apiKey:\s*"([^"]+)"', GW_CONFIG.read_text())
        if m:
            return m.group(1)
    return None


def gw_post(path: str, body: dict, timeout: float = 60.0) -> dict:
    hdrs = {"Content-Type": "application/json", "x-tdai-service-id": "default"}
    key = gateway_key()
    if key:
        hdrs["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(
        GATEWAY_BASE + path, data=json.dumps(body).encode(), headers=hdrs, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        return {"code": -1, "http_status": exc.code, "message": exc.read().decode()[:300]}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"code": -2, "message": f"{type(exc).__name__}: {exc}"[:300]}


# --- corpus ------------------------------------------------------------------


def parse_tsv(path: Path) -> list[dict]:
    import csv

    rows: list[dict] = []
    with open(path, newline="", encoding="utf-8") as f:
        for raw in csv.DictReader(f, delimiter="\t"):
            row = dict(raw)
            for listy in ("wiki_pages", "aliases", "reasoning_types", "events", "entities"):
                if listy in row and isinstance(row[listy], str):
                    with contextlib.suppress(ValueError, SyntaxError):
                        row[listy] = ast.literal_eval(row[listy])
            rows.append(row)
    return rows


def cmd_download(run: str, sample: int) -> None:
    d = run_dir(run)
    tsv = d / "stage0_corpus" / "test.tsv"
    if not tsv.exists():
        log(f"downloading {FRAMES_URL}")
        tsv.write_bytes(urllib.request.urlopen(FRAMES_URL, timeout=120).read())
    rows = parse_tsv(tsv)
    import random

    rng = random.Random(SEED)
    idx = sorted(rng.sample(range(len(rows)), min(sample, len(rows))))
    picked = [{"_idx": i, **rows[i]} for i in idx]
    (d / "stage0_corpus" / "sample.json").write_text(json.dumps(picked, indent=1, default=str))
    (d / "stage0_corpus" / "corpus_manifest.json").write_text(
        json.dumps(
            {
                "source": FRAMES_URL,
                "downloaded_at": datetime.now(UTC).isoformat(),
                "dataset_rows": len(rows),
                "seed": SEED,
                "sample_size": len(picked),
                "sample_indices": idx,
                "columns": sorted(rows[0].keys()),
            },
            indent=1,
        )
    )
    log(f"download ok: dataset={len(rows)} rows, sample={len(picked)} -> {tsv.parent}")
    log(f"columns: {sorted(rows[0].keys())}")


def cmd_fetch(run: str) -> None:
    """Resolve every wiki link in the sample to article plaintext (stage0)."""
    import urllib.parse

    d = run_dir(run)
    sample = json.loads((d / "stage0_corpus" / "sample.json").read_text())
    per_q: list[dict] = []
    unique: dict[str, str] = {}
    for row in sample:
        links = row.get("wiki_links") or []
        if isinstance(links, str):
            with contextlib.suppress(ValueError, SyntaxError):
                links = ast.literal_eval(links)
        titles = []
        for link in links:
            slug = urllib.parse.unquote(link.rstrip("/").rsplit("/", 1)[-1]).replace("_", " ")
            if slug:
                titles.append(slug)
                unique.setdefault(slug, link)
        per_q.append({"question_idx": row["_idx"], "links": links, "titles": titles})
    log(f"unique articles to fetch: {len(unique)}")

    articles: dict[str, dict] = {}
    canonical: dict[str, str] = {}
    titles_list = sorted(unique)
    for i, title in enumerate(titles_list):
        # MediaWiki returns a full-text extract for only ONE page per request;
        # section links ("Title#Section") and highlight URLs resolve to the
        # base article.
        base = title.split("#")[0].split(":~")[0].strip()
        url = (
            f"{WIKI_API}?action=query&prop=extracts&explaintext=1&format=json"
            f"&formatversion=2&redirects=1&titles={urllib.parse.quote(base)}"
        )
        req = urllib.request.Request(url, headers={"User-Agent": WIKI_UA})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode())
            pages = data["query"]["pages"]
            page = pages[0] if pages else {}
        except (urllib.error.URLError, KeyError, IndexError) as exc:
            log(f"  ! {title}: {exc}")
            time.sleep(0.2)
            continue
        body = page.get("extract") or ""
        if body:
            canonical[title] = page["title"]
            articles[page["title"]] = {
                "title": page["title"],
                "url": unique.get(title, ""),
                "pageid": page.get("pageid"),
                "chars": len(body),
                "text": body,
            }
        if (i + 1) % 20 == 0:
            log(f"  {i + 1}/{len(titles_list)} fetched ({len(articles)} with text)")
        time.sleep(0.2)

    resolved = []
    for q in per_q:
        q["resolved"] = sorted({
            canonical[t] for t in q["titles"] if t in canonical and canonical[t] in articles
        })
        resolved.append(q)
    missing = [t for t in titles_list if t not in canonical]
    (d / "stage0_corpus" / "articles.json").write_text(json.dumps(articles, indent=1))
    (d / "stage0_corpus" / "sample_articles.json").write_text(json.dumps(resolved, indent=1))
    (d / "stage0_corpus" / "fetch_manifest.json").write_text(
        json.dumps({
            "api": WIKI_API, "fetched_at": datetime.now(UTC).isoformat(),
            "unique_titles": len(titles_list), "resolved": len(titles_list) - len(missing),
            "missing": missing,
            "license": "Wikipedia text, CC BY-SA (see article URLs)",
        }, indent=1)
    )
    log(f"fetch ok: resolved={len(titles_list) - len(missing)}/{len(titles_list)}")
    if missing:
        log(f"missing titles: {missing}")


# --- ingestion ---------------------------------------------------------------


def normalize(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def chunk_article(text: str) -> list[str]:
    paras, chunks, cur = text.split("\n\n"), [], ""
    for p in paras:
        if len(cur) + len(p) + 2 > CHUNK_TARGET and cur:
            chunks.append(cur.strip())
            cur = ""
        cur += p + "\n\n"
    if cur.strip():
        chunks.append(cur.strip())
    return chunks


def session_id(run: str, q_idx: int) -> str:
    return f"frames-{run}-q{q_idx:03d}"


def cmd_ingest(run: str) -> None:
    d = run_dir(run)
    sample = json.loads((d / "stage0_corpus" / "sample_articles.json").read_text())
    articles = json.loads((d / "stage0_corpus" / "articles.json").read_text())
    writer = VaultWriter(d / "stage2_landing")
    extraction: list[dict] = []
    t0 = time.perf_counter()
    for row in sample:
        qi = row["question_idx"]
        items = [(t, articles[t]["text"]) for t in row["resolved"] if t in articles]
        docs = []
        msgs: list[dict] = []
        base_ts = int(datetime(2026, 9, 5, tzinfo=UTC).timestamp())
        for di, (title, body) in enumerate(items):
            canon = normalize(body)
            if not canon:
                continue
            docs.append({"title": title, "chars": len(canon)})
            writer.write_atom(
                atom_from(
                    "doc_text", Tier.VAULT,
                    {"file": f"frames_q{qi}", "title": title},
                    {
                        "question_idx": qi, "doc_index": di,
                        "tier_rule": "verbatim corpus text = canon (HU-1839)",
                    },
                    {"text": canon},
                ),
                slug=f"q{qi}_d{di}_{re.sub(r'[^A-Za-z0-9]+', '_', title)[:40]}",
            )
            for ci, chunk in enumerate(chunk_article(canon)):
                header = f"[[{title} | q{qi} doc{di + 1}/{len(items)} chunk{ci}]]"
                msgs.append(
                    {
                        "role": "user",
                        "content": f"{header}\n{chunk}"[:8000],
                        "timestamp": datetime.fromtimestamp(
                            base_ts + len(msgs), UTC
                        ).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    }
                )
        # derived-tier atom: the regenerable chunk index (TencentDB tier mirror)
        writer.write_atom(
            atom_from(
                "doc_chunks", Tier.DERIVED,
                {"file": f"frames_q{qi}"},
                {"question_idx": qi, "tier_rule": "derived chunks -> TencentDB tier"},
                {"messages": len(msgs), "docs": docs},
            ),
            slug=f"q{qi}_chunks",
        )
        sid = session_id(run, qi)
        codes = []
        batches = [msgs[i : i + 16] for i in range(0, len(msgs), 16)]
        for batch in batches:
            out = gw_post(
                "/v3/conversation/add",
                {
                    "team_id": TEAM_ID,
                    "agent_id": f"frames-{run}",
                    "user_id": "corpus",
                    "session_id": sid,
                    "messages": batch,
                },
                timeout=120.0,
            )
            codes.append(out.get("code"))
            if out.get("code") != 0:
                log(f"  ! q{qi:03d} add batch failed: {out}")
            time.sleep(0.3)
        extraction.append(
            {
                "question_idx": qi,
                "session_id": sid,
                "docs": docs,
                "messages_pushed": len(msgs),
                "add_batches": len(batches),
                "gateway_codes": codes,
            }
        )
        log(f"q{qi:03d}: docs={len(docs)} chunks={len(msgs)} codes={codes}")
        time.sleep(0.5)
    (d / "stage1_extraction" / "extraction.json").write_text(json.dumps(extraction, indent=1))
    manifest = writer.write_manifest(
        {
            "run": run,
            "isolation": "standalone eval vault (ISOLATION DOCTRINE §1) —"
                         " never the company brain vault",
            "team_id": TEAM_ID,
            "agent_id": f"frames-{run}",
            "sample": len(sample),
            "wall_sec": round(time.perf_counter() - t0, 2),
        }
    )
    log(f"ingest ok: vault manifest={manifest}")


# --- run: recall -> answer -> judge ------------------------------------------


def recall(q: str, sid: str) -> dict:
    return gw_post("/recall", {"query": q[:QUERY_CAP], "session_key": sid}, timeout=30)


def strip_tags(context: str) -> str:
    return re.sub(r"</?relevant-memories>", "", context or "").strip()


def provenance_check(context: str, titles: set[str]) -> tuple[bool, list[str]]:
    """Every [[...]] doc header in the recalled context must belong to the
    question's own corpus (HU-1383 question->doc traceability)."""
    found = re.findall(r"\[\[([^\]|]+)\s*\|", context or "")
    bad = [t for t in found if t.strip() not in titles]
    return (not bad), bad


ANSWER_SYSTEM = (
    "You are a precise research assistant. Answer strictly from the reference "
    "material provided. Think step by step if needed, then end with exactly one "
    "line: 'Final answer: <short answer>'."
)


def cmd_run(run: str, limit: int, gateway: str | None = None, out: str | None = None) -> None:
    """Recall -> answer -> judge.

    Sessions always belong to `run` (they hold the ingested corpus). With
    `--out` (ablation arms), artifacts land in a separate run dir while
    stage0/stage1 inputs are still read from the base run — zero re-ingest.
    """
    global GATEWAY_BASE
    if gateway:
        GATEWAY_BASE = gateway.rstrip("/")
    base = run_dir(run)
    d = run_dir(out) if out else base
    raw = {r["_idx"]: r for r in
           json.loads((base / "stage0_corpus" / "sample.json").read_text())}
    sample = []
    for row in json.loads((base / "stage0_corpus" / "sample_articles.json").read_text()):
        src = raw.get(row["question_idx"], {})
        sample.append({**row,
                       "qw": src.get("Prompt") or src.get("qw") or "",
                       "gold": (src.get("Answer") or src.get("a") or "").strip()})
    env = {**load_env_file(), "LLM_PROVIDER": "zai"}
    cfg = LLMConfig.from_env(env)
    answerer = ZaiLLMClient(cfg)
    judge_env = {**env, "ZAI_MODEL": JUDGE_MODEL, "LLM_MODEL": "", "LLM_MAX_TOKENS": "256"}
    judge_cfg = LLMConfig.from_env(judge_env)
    judge_cfg.temperature = 0.0
    judge = ZaiLLMClient(judge_cfg)

    import asyncio

    rows: list[dict] = []

    async def one(row: dict) -> dict:
        qi = row["question_idx"]
        q = row.get("qw") or ""
        gold = row.get("gold") or ""
        aliases: list = []
        sid = session_id(run, qi)
        titles = {doc["title"] for doc in
                  next(e for e in json.loads(
                      (base / "stage1_extraction" / "extraction.json").read_text()
                  ) if e["question_idx"] == qi)["docs"]}

        rec = recall(q, sid)
        ctx = rec.get("prepend_context") or ""
        trace = {
            "question_idx": qi, "session_id": sid, "query": q[:QUERY_CAP],
            "strategy": rec.get("strategy"), "memory_count": rec.get("memory_count"),
            "context_chars": len(ctx), "gateway_code": rec.get("code"),
            "context": ctx, "full_response_keys": sorted(rec.keys()),
        }
        (d / "stage3_retrieval" / f"q{qi:03d}_trace.json").write_text(json.dumps(trace, indent=1))
        prov_ok, bad = provenance_check(ctx, titles)
        if not ctx:
            return {"question_idx": qi, "question": q, "gold": gold, "error": "empty recall",
                    "strategy": rec.get("strategy"), "memory_count": rec.get("memory_count")}

        answer = (
            await answerer.generate(
                f"Reference material:\n\n{ctx}\n\nQuestion: {q}",
                system_prompt=ANSWER_SYSTEM, temperature=0.2, max_tokens=768,
            )
        ).strip()
        m = re.search(r"final answer:\s*(.+)", answer, re.IGNORECASE | re.DOTALL)
        final = (m.group(1) if m else answer).strip()
        (d / "stage4_answers" / f"q{qi:03d}_answer.json").write_text(
            json.dumps({
                "question_idx": qi, "question": q, "raw": answer, "final_answer": final,
                "context_chars": len(ctx), "strategy": rec.get("strategy"),
                "memory_count": rec.get("memory_count"), "provenance_ok": prov_ok,
                "provenance_bad_titles": bad,
                "usage": answerer.last_usage,
            }, indent=1)
        )

        verdict_raw = (
            await judge.generate(
                f"Question: {q}\nGold answer: {gold}\n"
                f"Gold aliases: {', '.join(map(str, aliases)) if aliases else '(none)'}\n"
                f"Candidate answer: {final}\n\n"
                "Is the candidate answer correct (semantically equivalent to the gold)? "
                "Reply with exactly one line: VERDICT: CORRECT or VERDICT: INCORRECT,\n"
                "then one short reason.",
            )
        ).strip()
        correct = "verdict: correct" in verdict_raw.lower()
        (d / "stage5_judge" / f"q{qi:03d}_judge.json").write_text(
            json.dumps({"question_idx": qi, "gold": gold, "candidate": final,
                        "verdict_raw": verdict_raw, "correct": correct,
                        "usage": judge.last_usage}, indent=1)
        )
        g = re.sub(r"[^a-z0-9]", "", gold.lower())
        c = re.sub(r"[^a-z0-9]", "", final.lower())
        return {
            "question_idx": qi, "question": q, "gold": gold, "answer": final,
            "correct": correct, "em_like": bool(g and (g in c or c in g)),
            "strategy": rec.get("strategy"), "memory_count": rec.get("memory_count"),
            "context_chars": len(ctx), "provenance_ok": prov_ok,
        }

    async def main_async() -> None:
        for row in sample[:limit] if limit else sample:
            t0 = time.perf_counter()
            try:
                r = await one(row)
            except Exception as exc:
                traceback.print_exc()
                r = {"question_idx": row["question_idx"], "error": repr(exc)[:300]}
            r["wall_sec"] = round(time.perf_counter() - t0, 1)
            rows.append(r)
            status = 'OK' if r.get('correct') else r.get('error') or 'WRONG'
            log(f"q{row['question_idx']:03d}: {status} "
                f"({r.get('wall_sec')}s, ctx={r.get('context_chars')})")
            (d / "scores_partial.json").write_text(json.dumps(rows, indent=1))

    asyncio.run(main_async())

    done = [r for r in rows if "correct" in r]
    summary = {
        "run": run, "arm_out": out, "gateway": GATEWAY_BASE,
        "judge_model": JUDGE_MODEL,
        "answerer_model": cfg.zai_model,
        "n_scored": len(done),
        "judge_correct": sum(r["correct"] for r in done),
        "judge_accuracy": round(sum(r["correct"] for r in done) / len(done), 4) if done else None,
        "em_like": sum(r["em_like"] for r in done),
        "provenance_ok": sum(r["provenance_ok"] for r in done),
        "avg_context_chars": (
            round(sum(r["context_chars"] for r in done) / len(done)) if done else 0
        ),
        "tokens_answerer": (answerer.last_usage or {}).get("total_tokens"),
        "rows": rows,
    }
    (d / "scores.json").write_text(json.dumps(summary, indent=1))
    log(f"SCORE [{GATEWAY_BASE}]: {summary['judge_correct']}/{summary['n_scored']} = "
        f"{summary['judge_accuracy']} "
        f"(em_like={summary['em_like']}, provenance_ok={summary['provenance_ok']})")


# --- teardown ----------------------------------------------------------------


def cmd_teardown(run: str) -> None:
    d = run_dir(run)
    sample = json.loads((d / "stage0_corpus" / "sample_articles.json").read_text())
    sids = [session_id(run, r["question_idx"]) for r in sample]
    results = {}
    for sid in sids:
        for path in ("/v3/conversation/delete", "/v3/session/delete"):
            out = gw_post(
                path,
                {"session_id": sid, "team_id": TEAM_ID, "agent_id": f"frames-{run}"},
                timeout=20,
            )
            if out.get("code", -1) not in (-1, 404):
                results[sid] = {"path": path, "code": out.get("code")}
                break
        else:
            results[sid] = {
                "path": None,
                "note": "no delete route — scope remains namespaced/quarantined",
            }
            break
    (d / "teardown.json").write_text(json.dumps({
        "run": run, "scope": {"team_id": TEAM_ID, "agent_id": f"frames-{run}"},
        "sessions": [*sids[:5], "..."],
        "attempted": results,
        "at": datetime.now(UTC).isoformat(),
    }, indent=1))
    log(f"teardown attempted; first result: {results.get(sids[0]) if sids else 'no sessions'}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["download", "fetch", "ingest", "run", "teardown"])
    ap.add_argument("--run", required=True)
    ap.add_argument("--sample", type=int, default=20)
    ap.add_argument("--limit", type=int, default=0, help="cap questions in `run` (0 = all)")
    ap.add_argument("--gateway", help="`run` only: gateway base URL override (ablation arms)")
    ap.add_argument("--out", help="`run` only: artifact dir suffix; sessions still come from --run")
    args = ap.parse_args()
    {"download": lambda: cmd_download(args.run, args.sample),
     "fetch": lambda: cmd_fetch(args.run),
     "ingest": lambda: cmd_ingest(args.run),
     "run": lambda: cmd_run(args.run, args.limit, args.gateway, args.out),
     "teardown": lambda: cmd_teardown(args.run)}[args.cmd]()
    return 0


if __name__ == "__main__":
    sys.exit(main())
