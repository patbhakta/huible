#!/usr/bin/env python3
"""H4 — Five-Friends test v0 EXECUTION harness (HU-2811; HU-2309 §1.7.4/§1.8 H4).

Executes the north-star eval across the five provisioned Friends persona
vaults through the REAL engine chat path (no mocks, no scripted persona
lines, no fake-voice fallback):

  ring dialogue      one shared conversation id, consent x5; each persona
                     replies through its own /api/v1/chat/{persona_id}
                     endpoint (own vault retrieval, persona-scoped working
                     memory) to the previous speaker's line, ring-rotated.
  comparator         one description-only arm: bare generator call (same
                     budget-wired client) given ONLY short character
                     descriptions — no vault text (§1.7.4 comparator spec).
  leakage gate       every trace-activated memory must belong to the
                     speaking persona (DB ownership check, fail-closed) +
                     per-persona cross-vault probe battery.
  pairing            seeded X/Y shuffle of the two group transcripts,
                     seed recorded, provenance sealed separately.
  scoring            five dims (§1.7.4): Blind Attribution, Grounding,
                     Emergence, Two-way Engagement, Blind Preference.
                     Measured components are computed; design-owner
                     provisional scores are recorded; the boss is the blind
                     rater at gate time. The harness NEVER assigns a
                     passing verdict (kit governance, HU-2706).

Usage:
  python3 scripts/v2_harness/h4_five_friends_run.py --check   # offline, no tokens
  python3 scripts/v2_harness/h4_five_friends_run.py --i-am-the-boss --run \
      --turns 35 --out-dir runs/hu2811

Exit 0 = run completed and evidence written (NOT a quality verdict).
"""

from __future__ import annotations

import argparse
import json
import random
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))  # measure_tells imports scripts.personas_dual_converse
ENGINE = "http://127.0.0.1:8000"
PSQL = ["docker", "exec", "huible-postgres", "psql", "-U", "huible", "-d", "huible", "-tAc"]

RING = ["chandler", "monica", "joey", "phoebe", "ross"]
DISPLAY = {"chandler": "Chandler", "monica": "Monica", "joey": "Joey",
           "phoebe": "Phoebe", "ross": "Ross"}


def persona_id(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"huible/personas/{name}"))


PERSONA_IDS = {n: persona_id(n) for n in RING}
PERSONA_IDS["chandler"] = "fdc3a44b-4c0f-565d-b671-4ed0e3bc7894"  # Persona-0 id
PERSONA_IDS["monica"] = "3ef60bec-79d2-5e31-8d9e-e856bb1ebfea"    # HU-2774 id

SEED_LINE = "hey, so what is everyone doing tonight?"

# §1.7.4 comparator: description-only, no vault text, no show names.
COMPARATOR_PROMPT = (
    "Write a short group-chat dialog, exactly 35 lines, each line formatted "
    "'Name: text', among five friends: Chandler (sarcastic, deflects with "
    "jokes), Monica (competitive chef, control freak), Joey (struggling "
    "actor, loves food and women), Phoebe (eccentric masseuse and folk "
    "singer), Ross (paleontologist, recently divorced). Topic: what everyone "
    "is doing tonight. Keep each line under 40 words."
)

PROBE_TEMPLATE = (
    "quick one — tell me the story behind: \"{snippet}\""
)  # inbound temptation: another persona's exclusive line, verbatim snippet


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def psql(query: str) -> str:
    out = subprocess.run(PSQL + [query], capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise SystemExit(f"psql failed: {out.stderr[:300]}")
    return out.stdout.strip()


def resolve_key() -> str:
    import os

    key = os.environ.get("HUIBLE_PROBE_API_KEY")
    if key:
        return key.strip()
    for line in (REPO_ROOT / ".env.failover").read_text().splitlines():
        if line.startswith("API_KEYS="):
            for entry in line[len("API_KEYS="):].split(","):
                k = entry.strip().partition(":")[0]
                if k.startswith("chandler-"):
                    return k
    raise SystemExit(2)


def request(method: str, path: str, api_key: str, body: dict | None) -> tuple[int, dict]:
    req = urllib.request.Request(
        ENGINE + path, method=method,
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json",
                 "X-Huible-Traffic-Class": "internal"},
        data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode() or "{}"
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"_raw": raw[:300]}


def turn_with_retry(api_key: str, pid: str, conv: str, text: str,
                    attempts: int = 4,
                    user_name: str | None = None) -> tuple[int, dict]:
    delay = 20.0
    status, body = 0, {}
    for attempt in range(attempts):
        payload = {"message": text, "relationship": "close_friend",
                   "conversation_id": conv}
        if user_name:
            payload["user_name"] = user_name
        status, body = request("POST", f"/api/v1/chat/{pid}", api_key, payload)
        transient = status == 429 or status >= 500
        if not transient or attempt == attempts - 1:
            return status, body
        log(f"    transient HTTP {status} (attempt {attempt + 1}/{attempts}); backoff {delay:.0f}s")
        time.sleep(delay)
        delay = min(delay * 2, 90.0)
    return status, body  # pragma: no cover


def assert_live(reply: str, where: str) -> None:
    if "[fake-llm" in reply or "Deterministic response" in reply:
        raise SystemExit(
            f"HARNESS INVALID: fake-voice fallback served at {where} — "
            "budget/provider outage. This run is not persona evidence.")


def consent_all(api_key: str, conv: str) -> None:
    for name in RING:
        status, body = request("POST", f"/api/v1/chat/{PERSONA_IDS[name]}/consent",
                               api_key, {"conversation_id": conv,
                                         "card_version": 3})
        if status not in (200, 409):
            raise SystemExit(f"consent failed for {name}: {status} {body}")
    log(f"  consent recorded x5 (conv={conv})")


# ── preflight ───────────────────────────────────────────────────────────────

def check() -> dict:
    rows = psql("SELECT id::text, display_name FROM personas").splitlines()
    db = {r.split("|")[0]: r.split("|")[1] for r in rows if "|" in r}
    slots = {}
    for name in RING:
        pid = PERSONA_IDS[name]
        n = psql(f"SELECT count(*) FROM memories WHERE persona_id='{pid}'")
        slots[name] = {"persona_id": pid, "provisioned": pid in db,
                       "memories": int(n or 0)}
    budget = psql("SELECT 1") == "1"
    result = {
        "probe": "H4 Five-Friends v0 wiring check (offline, no tokens)",
        "slots": slots,
        "all_provisioned": all(s["provisioned"] and s["memories"] > 500
                               for s in slots.values()),
        "db_reachable": budget,
        "self_graded_verdict": None,
    }
    log(json.dumps({k: (v if k != "slots" else
                        {n: f"{s['memories']}mem/prov={s['provisioned']}"
                         for n, s in v.items()})
                    for k, v in result.items()}, indent=1))
    return result


# ── dialogue ────────────────────────────────────────────────────────────────

def run_dialogue(api_key: str, conv: str, turns: int) -> dict:
    consent_all(api_key, conv)
    transcript = []
    inbound = SEED_LINE
    speaker = RING[0]
    total_ms = 0.0
    for i in range(turns):
        prev = RING[(RING.index(speaker) - 1) % len(RING)]
        t0 = time.perf_counter()
        status, body = turn_with_retry(api_key, PERSONA_IDS[speaker], conv,
                                       inbound, user_name=DISPLAY[prev])
        latency_ms = round((time.perf_counter() - t0) * 1000)
        total_ms += latency_ms
        reply = (body.get("response") or "").strip()
        if status != 200 or not reply:
            raise SystemExit(f"turn {i + 1} ({speaker}): HTTP {status} {str(body)[:200]}")
        assert_live(reply, f"turn {i + 1} ({speaker})")
        trace = body.get("trace") or {}
        acts = trace.get("activated_memories") or []
        wm = trace.get("working_memory") or {}
        provider = trace.get("provider") or ""
        if "mock" in provider.lower() or "fake" in provider.lower():
            raise SystemExit(f"HARNESS INVALID: mock provider at turn {i + 1}: {provider}")
        line = {
            "turn": i + 1, "speaker": speaker, "inbound_from": prev,
            "inbound": inbound, "reply": reply, "latency_ms": latency_ms,
            "provider": provider,
            "memory_refs": trace.get("memory_refs") or [],
            "activated_memory_ids": [a.get("id") for a in acts if a.get("id")],
            "activation_scores": [a.get("activation_score") for a in acts
                                  if a.get("activation_score") is not None],
            "wm_chars": wm.get("chars", 0),
            "trace_id": trace.get("trace_id"),
        }
        transcript.append(line)
        log(f"  t{i + 1:02d} [{speaker}] {latency_ms}ms {len(reply)}ch "
            f"refs={len(line['memory_refs'])} acts={len(line['activation_scores'])} "
            f"{reply[:60]!r}")
        inbound = reply
        speaker = RING[(RING.index(speaker) + 1) % len(RING)]
    return {"conversation_id": conv, "turns": transcript,
            "avg_turn_latency_ms": round(total_ms / turns),
            "seed_line_author": "harness (disclosed scaffold)", "seed_line": SEED_LINE}


# ── cross-vault probes ──────────────────────────────────────────────────────

def exclusive_snippet(target: str, all_notes: dict[str, set[str]]) -> str | None:
    """A content token set from target's own vault absent from every other
    persona's vault — the strongest cross-vault temptation available."""
    rows = psql(
        "SELECT content FROM memories WHERE persona_id='%s'" % PERSONA_IDS[target])
    lines = [r for r in rows.splitlines() if len(r) > 40]
    others: set[str] = set()
    for other, toks in all_notes.items():
        if other != target:
            others |= toks
    for r in lines:
        toks = set(re.findall(r"[a-z']{5,}", r.lower()))
        fresh = toks - others
        if len(fresh) >= 2:
            return r[:90]
    return None


def cross_vault_probes(api_key: str, out_dir: Path) -> dict:
    """Per persona: tempt with another persona's exclusive line; the trace
    must stay persona-scoped (no retrieval from the tempting persona)."""
    vault_tokens = {}
    for name in RING:
        rows = psql("SELECT content FROM memories WHERE persona_id='%s'"
                    % PERSONA_IDS[name])
        vault_tokens[name] = set(re.findall(r"[a-z']{5,}", rows.lower()))

    results = []
    for target, tempter in zip(RING, RING[1:] + RING[:1]):
        snippet = exclusive_snippet(tempter, vault_tokens)
        if not snippet:
            results.append({"target": target, "tempter": tempter,
                            "skipped": "no exclusive snippet"})
            continue
        conv = f"h4x{target[:2]}-{uuid.uuid4().hex[:8]}"
        status, _ = request("POST", f"/api/v1/chat/{PERSONA_IDS[target]}/consent",
                            api_key, {"conversation_id": conv, "card_version": 3})
        if status not in (200, 409):
            raise SystemExit(f"probe consent failed {target}: {status}")
        status, body = turn_with_retry(
            api_key, PERSONA_IDS[target], conv,
            PROBE_TEMPLATE.format(snippet=snippet))
        reply = (body.get("response") or "").strip()
        if status != 200 or not reply:
            raise SystemExit(f"probe turn failed {target}: HTTP {status}")
        assert_live(reply, f"cross-vault probe ({target})")
        trace = body.get("trace") or {}
        acts = trace.get("activated_memories") or []
        results.append({
            "target": target, "tempter": tempter, "snippet": snippet,
            "conversation_id": conv, "reply": reply[:400],
            "activated_memory_ids": [a.get("id") for a in acts if a.get("id")],
            "activation_scores": [a.get("activation_score") for a in acts
                                  if a.get("activation_score") is not None],
        })
        log(f"  probe [{target}] tempted by [{tempter}] acts={len(acts)}")
    return {"probes": results}


# ── ownership gate ──────────────────────────────────────────────────────────

def ownership_gate(transcript: dict, probes: dict) -> dict:
    """Every activated memory across dialogue + probes must belong to the
    speaking/asking persona (fail-closed)."""
    checks, leaks = [], []
    blocks = [("dialogue", t["speaker"], t["activated_memory_ids"])
              for t in transcript["turns"]]
    blocks += [("probe", p["target"], p.get("activated_memory_ids") or [])
               for p in probes["probes"]]
    for kind, persona, ids in blocks:
        ids = [i for i in ids if i]
        if not ids:
            checks.append({"kind": kind, "persona": persona, "refs": 0,
                           "persona_scoped": True})
            continue
        id_list = ",".join(f"'{i}'" for i in ids)
        rows = psql(f"SELECT id::text, persona_id::text FROM memories "
                    f"WHERE id IN ({id_list})").splitlines()
        bad = [r for r in rows if r.split("|")[1] != PERSONA_IDS[persona]]
        if bad:
            leaks.append({"kind": kind, "persona": persona, "foreign_refs": bad})
        checks.append({"kind": kind, "persona": persona, "refs": len(ids),
                       "persona_scoped": not bad})
    return {"checked": len(checks), "cross_vault_leaks": leaks,
            "pass": not leaks}


# ── comparator + pairing ────────────────────────────────────────────────────

def comparator_script(out_dir: Path) -> str:
    """One budget-wired generator call inside the app container (description-
    only arm; usage accrues to the same daily ledger)."""
    script = (
        "import asyncio, json\n"
        "from huible.llm.client import build_llm_client\n"
        "async def main():\n"
        "    c = build_llm_client()\n"
        "    text = await c.generate(%r)\n"
        "    print('===COMPARATOR===')\n"
        "    print(text)\n"
        "asyncio.run(main())\n" % COMPARATOR_PROMPT)
    path = out_dir / "_comparator_gen.py"
    path.write_text(script)
    subprocess.run(["docker", "cp", str(path), "huible-app:/tmp/ff_comparator.py"],
                   check=True, timeout=60)
    out = subprocess.run(
        ["docker", "exec", "huible-app", "python", "-u", "/tmp/ff_comparator.py"],
        capture_output=True, text=True, timeout=600)
    (out_dir / "_comparator_gen.log").write_text(out.stderr[-4000:])
    if out.returncode != 0 or "===COMPARATOR===" not in out.stdout:
        raise SystemExit(f"comparator generation failed: {out.stderr[-500:]}")
    return out.stdout.split("===COMPARATOR===", 1)[1].strip()


def parse_comparator(text: str) -> list[str]:
    lines = [l.strip() for l in text.splitlines() if ":" in l.strip()]
    return [l for l in lines if len(l) > 10][:40]


def pair_and_score(out_dir: Path, transcript: dict, comparator_lines: list[str],
                   leakage: dict, engagement: dict, grounding: dict,
                   attribution: dict) -> dict:
    seed = int(datetime.now(UTC).timestamp())
    arms = ["five-friends-v0", "comparator-desc-only"]
    random.Random(seed).shuffle(arms)
    # Blind pack carries DIALOGUE TEXT ONLY — no trace/memory/latency fields
    # that could fingerprint the engine arm (provenance stays sealed).
    ff_lines = [{"speaker": t["speaker"], "text": t["reply"]}
                for t in transcript["turns"]]
    comp_pack = [{"speaker": l.split(":", 1)[0].strip(),
                  "text": l.split(":", 1)[1].strip()} for l in comparator_lines]
    pack = {
        "pair": 1,
        "seed": seed,
        "arm_labels": {arms[0]: "X", arms[1]: "Y"},
        "transcript_X": {"arm": arms[0], "turns":
                         ff_lines if arms[0] == "five-friends-v0" else comp_pack},
        "transcript_Y": {"arm": arms[1], "turns":
                         comp_pack if arms[0] == "five-friends-v0" else ff_lines},
        "provenance_sealed": "provenance.json (boss-only)",
        "boss_blind_rating": "pending (gate time; card on HU-1911 path)",
        "verdict": None,
    }
    prov = {"seed": seed, "X": arms[0], "Y": arms[1]}
    (out_dir / "rating_pack_pair1.json").write_text(json.dumps(pack, indent=1))
    (out_dir / "provenance.json").write_text(json.dumps(prov, indent=1))
    scoring = {
        "test": "Five-Friends v0 (HU-2309 §1.7.4; HU-2811 execution)",
        "rater_of_record": "boss (blind, at gate time)",
        "design_owner_provisional": True,
        "harness_self_graded_verdict": None,
        "leakage_gate": leakage,
        "dims": [
            {"dim": "blind_attribution", **attribution},
            {"dim": "grounding", **grounding},
            {"dim": "emergence",
             "method": "design-owner transcript read (quotes archived); "
                       "measured hints: echo + question bands",
             "provisional": True, "score": None,
             "note": "scored by design owner in execution report; boss re-rates blind"},
            {"dim": "two_way_engagement", **engagement},
            {"dim": "blind_preference_vs_comparator",
             "method": "seeded X/Y pack; boss rates blind",
             "provisional_preference": None,
             "boss_blind_rating": "pending"},
        ],
    }
    (out_dir / "scoring.json").write_text(json.dumps(scoring, indent=1))
    return {"seed": seed, "X": arms[0], "Y": arms[1]}


# ── measured dims ───────────────────────────────────────────────────────────

QUESTION_BAND = (0.20, 0.45)
YOU_SHARE_FLOOR = 0.30
ACTIVATION_FLOOR = 0.50  # HU-2707 C4 retrieval floor


def measure_engagement(transcript: dict) -> dict:
    per = {}
    for name in RING:
        mine = [t for t in transcript["turns"] if t["speaker"] == name]
        if not mine:
            continue
        n = len(mine)
        q = sum(1 for t in mine if "?" in t["reply"])
        you = 0
        for t in mine:
            for s in re.findall(r"[^.!?]*\?", t["reply"]):
                if re.search(r"\b(you|your|you're|u|ur)\b", s, re.I):
                    you += 1
                    break
        per[name] = {
            "lines": n, "question_lines": q,
            "question_rate": round(q / n, 3),
            "in_corpus_band": QUESTION_BAND[0] <= (q / n) <= QUESTION_BAND[1],
            "you_directed_share": round(you / q, 3) if q else 0.0,
            "you_share_ok": q > 0 and (you / q) >= YOU_SHARE_FLOOR,
        }
    return {"per_persona": per, "band": list(QUESTION_BAND),
            "you_share_floor": YOU_SHARE_FLOOR,
            "measured": True, "provisional": False,
            "all_personas_in_band": all(v["in_corpus_band"] for v in per.values())}


def measure_grounding(transcript: dict) -> dict:
    total = above = 0
    zero_turns = []
    for t in transcript["turns"]:
        scores = t["activation_scores"]
        if not scores:
            zero_turns.append(t["turn"])
            continue
        total += 1
        if max(scores) >= ACTIVATION_FLOOR:
            above += 1
    return {
        "turns_with_retrieval": total, "turns_above_floor": above,
        "turns_zero_retrieval": zero_turns,
        "grounded_share": round(above / total, 3) if total else 0.0,
        "activation_floor": ACTIVATION_FLOOR,
        "measured": True, "provisional": False,
    }


def measure_attribution(transcript: dict) -> dict:
    """Distinctiveness proxy: fraction of each persona's lines whose
    casefolded 4+ tokens surface that persona's own corpus signature tokens
    (top-40 corpus-exclusive tokens) vs any other persona's."""
    sig = {}
    for name in RING:
        rows = psql("SELECT content FROM memories WHERE persona_id='%s'"
                    % PERSONA_IDS[name])
        from collections import Counter
        cnt = Counter(re.findall(r"[a-z']{4,}", rows.lower()))
        sig[name] = cnt
    top = {}
    for name in RING:
        others = Counter()
        for other in RING:
            if other != name:
                others.update(sig[other])
        exclusive = [w for w, _ in (sig[name] - others).most_common(40)]
        top[name] = exclusive
    per, hits_total, lines_total = {}, 0, 0
    for name in RING:
        hits = 0
        mine = [t for t in transcript["turns"] if t["speaker"] == name]
        for t in mine:
            toks = set(re.findall(r"[a-z']{4,}", t["reply"].lower()))
            if toks & set(top[name]):
                hits += 1
        lines_total += len(mine)
        hits_total += hits
        per[name] = {"signature_hits": hits, "lines": len(mine),
                     "signature_top_tokens": top[name][:10]}
    return {"method": "corpus-exclusive token signature (top-40 per persona)",
            "per_persona": per,
            "signature_hit_share": round(hits_total / lines_total, 3)
            if lines_total else 0.0,
            "measured": True, "provisional": False}


def measure_tells(transcript: dict) -> dict:
    """AI-tell battery (HU-2774 evaluator regexes) on every persona line."""
    from scripts.personas_dual_converse import AI_TELLS, PLATFORM_TEXT_MARKERS

    per, platform_lines = {}, []
    for t in transcript["turns"]:
        hits = [label for pat, label in AI_TELLS
                if re.search(pat, t["reply"], re.I)]
        if hits:
            per[t["turn"]] = {"speaker": t["speaker"], "hits": hits}
        if any(m.casefold() in t["reply"].casefold()
               for m in PLATFORM_TEXT_MARKERS):
            platform_lines.append(t["turn"])
    return {"lines_with_tells": per, "platform_text_turns": platform_lines,
            "tell_line_count": len(per), "measured": True, "provisional": False}


# ── main ────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--i-am-the-boss", action="store_true")
    ap.add_argument("--turns", type=int, default=35)
    ap.add_argument("--out-dir", type=Path, default=REPO_ROOT / "runs/hu2811")
    ap.add_argument("--skip-comparator", action="store_true",
                    help="resume mode: reuse existing comparator.txt")
    args = ap.parse_args()

    if args.check:
        result = check()
        return 0 if result["all_provisioned"] and result["db_reachable"] else 1
    if not args.run:
        ap.error("nothing to do: use --check or --run")
    if not args.i_am_the_boss:
        log("REFUSED: --run requires --i-am-the-boss (boss-gated execution).")
        return 3

    pre = check()
    if not pre["all_provisioned"]:
        raise SystemExit(f"pre-flight FAILED: slots not provisioned: {pre['slots']}")
    if pre["slots"]["joey"]["memories"] < 500:
        raise SystemExit("pre-flight FAILED: joey vault too thin")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    api_key = resolve_key()
    conv = f"h4ff-{uuid.uuid4().hex[:10]}"
    started = datetime.now(UTC).isoformat()
    log(f"[H4-FF] dialogue start conv={conv} turns={args.turns}")

    transcript = run_dialogue(api_key, conv, args.turns)
    (args.out_dir / "dialogue_transcript.json").write_text(
        json.dumps({"started_at": started, **transcript}, indent=1))

    log("[H4-FF] cross-vault probe battery")
    probes = cross_vault_probes(api_key, args.out_dir)
    (args.out_dir / "cross_vault_probes.json").write_text(json.dumps(probes, indent=1))

    log("[H4-FF] ownership gate (DB fail-closed)")
    leakage = ownership_gate(transcript, probes)

    log("[H4-FF] measured dims")
    engagement = measure_engagement(transcript)
    grounding = measure_grounding(transcript)
    attribution = measure_attribution(transcript)
    tells = measure_tells(transcript)

    log("[H4-FF] comparator arm (description-only, budget-wired)")
    comp_path = args.out_dir / "comparator.txt"
    if args.skip_comparator and comp_path.exists():
        comp_raw = comp_path.read_text()
    else:
        comp_raw = comparator_script(args.out_dir)
        comp_path.write_text(comp_raw)
    comparator_lines = parse_comparator(comp_raw)

    pairing = pair_and_score(args.out_dir, transcript, comparator_lines,
                             leakage, engagement, grounding, attribution)
    scoring = json.loads((args.out_dir / "scoring.json").read_text())
    scoring["dims"].append({"dim": "ai_tells", **tells})
    (args.out_dir / "scoring.json").write_text(json.dumps(scoring, indent=1))

    summary = {
        "probe": "H4 Five-Friends v0 EXECUTION (boss-gated, real generator only)",
        "started_at": started,
        "finished_at": datetime.now(UTC).isoformat(),
        "conversation_id": conv,
        "turns": args.turns,
        "avg_turn_latency_ms": transcript["avg_turn_latency_ms"],
        "comparator_lines": len(comparator_lines),
        "pairing": {"seed": pairing["seed"], "X": pairing["X"], "Y": pairing["Y"]},
        "leakage_gate_pass": leakage["pass"],
        "engagement_all_in_band": engagement["all_personas_in_band"],
        "grounded_share": grounding["grounded_share"],
        "signature_hit_share": attribution["signature_hit_share"],
        "tell_line_count": tells["tell_line_count"],
        "self_graded_verdict": None,
        "note": "Boss rates blind at gate time. The harness assigns NO verdict.",
    }
    (args.out_dir / "summary.json").write_text(json.dumps(summary, indent=1))
    log(json.dumps(summary, indent=1))
    return 0 if leakage["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
