#!/usr/bin/env python3
"""HU-2153 — deterministic chat→vault write-back.

After dogfood sessions, run the same deterministic stats as the onboarding
pipeline (``modules/onboarding/stats.py``) over the persona's own chat turns,
DIFF against the vault baseline (source-corpus stats), and append a gated
proposal to ``<vault>/observed-updates/``. The Librarian owns merging
proposals into ``persona-profile.md`` — this tool never edits vault truth,
it only appends proposal/log/state files under ``observed-updates/``.

Deterministic: no LLM, fixed thresholds, idempotent via a turn-id watermark.

Inputs:
  --export file.jsonl   turns as JSON rows {id, conversation_id, content, created_at}
                        (prod export: ssh .245 'docker exec huible-postgres psql -U huible -d huible
                         -tAc "SELECT json_agg(row_to_json(t) ORDER BY id) FROM (SELECT id, conversation_id,
                         content, created_at FROM conversation_turns WHERE speaker='"'"'persona'"'"' ORDER BY id) t"'
                         > turns.arr  # single JSON array; convert to JSONL locally)
  --database-url URL    live read instead of an export (psycopg, read-only SELECT)

  [fake-llm:...] marker turns are filtered automatically — they are provider
  smoke markers, not persona voice.

Baseline:
  --build-baseline      (re)compute observed-updates/baseline.json from
                        --corpus CSV (person,line) + --persona NAME, then exit

Thresholds (fixed, review by changing the constants below — no runtime knobs,
no vibes):
  MIN_LINES             below this many new persona lines a session is logged
                        as noise, never proposed
  NEW_WORD_MIN_COUNT    a word absent from the baseline top-200 must appear at
                        least this often to be proposed
  NEW_BIGRAM_MIN_COUNT  same rule for bigrams vs baseline top-30
  REGISTER_DELTA_PCT    median/p90 char-length shift (% of baseline) that counts
  AFFECT_DELTA_PP       exclamation/question-ratio shift (percentage points)

Outputs (all under <vault>/observed-updates/):
  baseline.json            corpus stats the diffs are taken against
  <date>-<conv8>.md        one proposal per drifting conversation (OKF two-field
                           frontmatter; Librarian merge surface)
  log.jsonl                every decision, applied or noise, with reasons
  state.json               {last_turn_id} watermark for idempotent re-runs

Usage:
  python3 scripts/vault_writeback.py --vault-dir /root/repos/personas/chandler-bing \
      --export turns.jsonl
  python3 scripts/vault_writeback.py --vault-dir ... --build-baseline \
      --corpus "onboarding/Chandler Bing - FRIENDS sitcom/friends-v2.csv" --persona chandler
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "modules" / "onboarding"))
from stats import compute_stats  # noqa: E402  (same tokenizer as onboarding)

MIN_LINES = 15
NEW_WORD_MIN_COUNT = 5
NEW_BIGRAM_MIN_COUNT = 4
REGISTER_DELTA_PCT = 30
AFFECT_DELTA_PP = 15
BASELINE_WORD_WINDOW = 200
BASELINE_BIGRAM_WINDOW = 30

# Scripted non-persona responses (platform voice). Turns opening with these
# are safety-layer templates, not the persona's own speech — they must never
# count as observed persona drift. Sources:
#   - src/huible/safety/crisis.py  build_crisis_response (988 handoff card)
#   - src/huible/safety/risk.py    PAUSE_SESSION_RESPONSE
#   - src/huible/safety/risk.py    PROXY_USER_PAUSE_RESPONSE
#   - src/huible/safety/risk.py    REFUSE_TOPIC_FALLBACK_RESPONSE
# Keep in sync when those templates change (grep PLATFORM_VOICE_OPENERS).
PLATFORM_VOICE_OPENERS = (
    "I want to pause for a moment, because what you're saying matters",
    "I think it's worth pausing for a moment",
    "Before we go further, I want to make sure I'm speaking with the right person",
    "I want to be gentle here, but that isn't something I can speak to",
)


def load_turns(export_path: str | None, database_url: str | None) -> list[dict]:
    rows: list[dict] = []
    if export_path:
        with open(export_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rows.append(json.loads(line))
    elif database_url:
        import psycopg  # repo venv / huible-app container

        with psycopg.connect(database_url) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, conversation_id, content, created_at "
                "FROM conversation_turns WHERE speaker = 'persona' ORDER BY id"
            )
            for tid, conv, content, created_at in cur.fetchall():
                rows.append(
                    {
                        "id": tid,
                        "conversation_id": conv,
                        "content": content,
                        "created_at": created_at.isoformat()
                        if hasattr(created_at, "isoformat")
                        else str(created_at),
                    }
                )
    else:
        raise SystemExit("need --export or --database-url (or --build-baseline)")

    # Fake-provider turns are infrastructure markers, not persona voice —
    # they must never count as observed drift (pre-flip LLM_PROVIDER=fake era).
    real = [r for r in rows if not r["content"].startswith("[fake-llm:")]
    if len(real) < len(rows):
        print(f"filtered {len(rows) - len(real)} [fake-llm:] marker turn(s)")
    # Scripted safety-layer templates are platform voice, not persona voice.
    kept = [
        r for r in real
        if not any(r["content"].lstrip().startswith(op)
                   for op in PLATFORM_VOICE_OPENERS)
    ]
    if len(kept) < len(real):
        print(f"filtered {len(real) - len(kept)} platform-voice template turn(s) "
              f"(crisis/pause/refuse cards)")
    return kept


def corpus_persona_lines(corpus_path: str, persona: str) -> list[str]:
    texts = []
    with open(corpus_path, encoding="utf-8", errors="replace", newline="") as f:
        for row in csv.reader(f):
            if len(row) >= 2 and row[0].strip().lower() == persona.lower():
                texts.append(row[1].strip())
    return texts


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def drift_report(baseline: dict, session: dict) -> dict:
    """Fixed-threshold diff of session stats vs baseline. Returns the
    decision payload: {'drift': [...], 'noise': [...]} where every entry
    carries its rule so the log is auditable."""
    drift, noise = [], []

    base_words = {w for w, _ in baseline.get("top_words_full", [])}
    base_bigrams = {w for w, _ in baseline.get("top_bigrams_full", [])}

    new_words = [
        (w, c)
        for w, c in session["top_words"]
        if w not in base_words and c >= NEW_WORD_MIN_COUNT
    ]
    if new_words:
        drift.append(
            {
                "rule": f"new frequent word: >={NEW_WORD_MIN_COUNT} uses, absent from baseline top-{BASELINE_WORD_WINDOW}",
                "items": new_words,
            }
        )

    new_bigrams = [
        (w, c)
        for w, c in session["top_bigrams"]
        if w not in base_bigrams and c >= NEW_BIGRAM_MIN_COUNT
    ]
    if new_bigrams:
        drift.append(
            {
                "rule": f"new bigram: >={NEW_BIGRAM_MIN_COUNT} uses, absent from baseline top-{BASELINE_BIGRAM_WINDOW}",
                "items": new_bigrams,
            }
        )

    b_cl = baseline["char_length"]
    s_cl = session["char_length"]
    for key in ("median_chars", "p90_chars"):
        b_v, s_v = b_cl.get(key), s_cl.get(key)
        if b_v and s_v is not None:
            delta_pct = abs(s_v - b_v) * 100.0 / b_v
            if delta_pct >= REGISTER_DELTA_PCT:
                drift.append(
                    {
                        "rule": f"register shift: {key} {b_v}→{s_v} chars ({delta_pct:.0f}% >= {REGISTER_DELTA_PCT}%)",
                        "items": [[key, f"{b_v}→{s_v}"]],
                    }
                )
            else:
                noise.append(
                    {
                        "rule": f"register {key}: {delta_pct:.0f}% < {REGISTER_DELTA_PCT}% threshold",
                        "items": [[key, f"{b_v}→{s_v}"]],
                    }
                )

    for key in ("exclamation_ratio", "question_ratio"):
        delta_pp = abs(session[key] - baseline[key])
        if delta_pp >= AFFECT_DELTA_PP:
            drift.append(
                {
                    "rule": f"affect shift: {key} {baseline[key]}→{session[key]} pp ({delta_pp:.0f} >= {AFFECT_DELTA_PP})",
                    "items": [[key, f"{baseline[key]}→{session[key]}"]],
                }
            )
        else:
            noise.append(
                {
                    "rule": f"affect {key}: {delta_pp:.0f}pp < {AFFECT_DELTA_PP} threshold",
                    "items": [[key, f"{baseline[key]}→{session[key]}"]],
                }
            )

    return {"drift": drift, "noise": noise}


def write_proposal(vault_dir: Path, conv_id: str, turns: list[dict],
                   session: dict, report: dict, baseline_src: str,
                   window: str = "conversation") -> Path:
    obs = vault_dir / "observed-updates"
    obs.mkdir(exist_ok=True)
    if window == "day":
        day = conv_id.split(":", 1)[1].split("+", 1)[0]
        slug = f"{day}-day"
    else:
        day = turns[-1]["created_at"][:10]
        slug = f"{day}-{conv_id.replace('-', '')[:8]}"
    path = obs / f"{slug}.md"

    tool_commit = os.popen("git rev-parse --short HEAD").read().strip() or "unknown"
    lo, hi = turns[0]["id"], turns[-1]["id"]
    export_sha = sha256_file(str(vault_dir / "observed-updates" / ".last-export")) \
        if (vault_dir / "observed-updates" / ".last-export").exists() else "n/a (db mode)"

    lines = [
        "---",
        "tags: [huible, persona, observed-update]",
        f"updated: {day}",
        "---",
        "",
        f"# Observed Update — {slug} ({day})",
        "",
        "> Proposal only — the Librarian merges accepted changes into",
        "> `persona-profile.md`. This file is deterministic tool output",
        "> (scripts/vault_writeback.py, HU-2153); it is not vault truth.",
        "",
        "## Session",
        "",
        f"- Window: `{conv_id}`",
        f"- Persona turns: {len(turns)} (turn ids {lo}..{hi})",
        f"- Window: {turns[0]['created_at']} → {turns[-1]['created_at']}",
        f"- Register: median {session['char_length'].get('median_chars')} ch, "
        f"p90 {session['char_length'].get('p90_chars')} ch, "
        f"avg {session['avg_words_per_line']} words/line",
        f"- Affect: exclamation {session['exclamation_ratio']}%, "
        f"question {session['question_ratio']}%",
        "",
        "## Proposed changes",
        "",
    ]
    if report["drift"]:
        for d in report["drift"]:
            lines.append(f"### {d['rule']}")
            lines.append("")
            for item, count in d["items"]:
                lines.append(f"- `{item}` — {count}")
            lines.append("")
    else:
        lines.append("- None above threshold (see log.jsonl for the noise record).")
        lines.append("")

    lines += [
        "## Rejected as noise (below threshold, logged not applied)",
        "",
    ]
    if report["noise"]:
        for n in report["noise"]:
            for item, detail in n["items"]:
                lines.append(f"- {item}: {detail} — {n['rule']}")
    else:
        lines.append("- None.")

    lines += [
        "",
        "## Provenance",
        "",
        f"- Source: `conversation_turns` rows {lo}..{hi} (speaker=persona), "
        f"export sha256 `{export_sha}`",
        f"- Baseline: `observed-updates/baseline.json` ({baseline_src})",
        f"- Tool: `scripts/vault_writeback.py` @ huible@{tool_commit} — "
        "fixed thresholds, no LLM",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Deterministic chat→vault write-back (HU-2153); see module docstring"
    )
    ap.add_argument("--vault-dir", required=True, help="persona vault directory")
    ap.add_argument("--export", help="JSONL turns export")
    ap.add_argument("--database-url", help="live Postgres read (psycopg)")
    ap.add_argument("--build-baseline", action="store_true",
                    help="recompute baseline.json from --corpus/--persona and exit")
    ap.add_argument("--corpus", help="source corpus CSV (person,line)")
    ap.add_argument("--persona", help="persona name in the corpus CSV")
    ap.add_argument("--window", choices=["conversation", "day"], default="day",
                    help="observation unit (default: day — dogfood sessions are "
                         "1-8 turns, a single conversation never clears MIN_LINES)")
    ap.add_argument("--conv-regex", default=None,
                    help="only conversation_ids matching this regex are observed "
                         "(e.g. '^[0-9a-f]{8}-' selects client UUID sessions and "
                         "excludes infra probe/smoke/verify traffic)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print decisions, write nothing")
    args = ap.parse_args()

    vault = Path(args.vault_dir)
    obs = vault / "observed-updates"
    obs.mkdir(exist_ok=True)
    baseline_path = obs / "baseline.json"

    if args.build_baseline:
        if not (args.corpus and args.persona):
            raise SystemExit("--build-baseline needs --corpus and --persona")
        texts = corpus_persona_lines(args.corpus, args.persona)
        if not texts:
            raise SystemExit(f"no lines found for persona {args.persona!r} in corpus")
        raw = compute_stats(texts)
        # Keep the full windows the diff rules compare against (top_words in
        # the onboarding output is only top-20; the gate needs top-200/30).
        from collections import Counter
        from stats import STOPWORDS
        wf, bf = Counter(), Counter()
        for t in texts:
            ws = [w.strip('.,!?";\'()[]{}').lower() for w in t.split()]
            for w in ws:
                if w and w not in STOPWORDS and len(w) > 2:
                    wf[w] += 1
            for i in range(len(ws) - 1):
                if ws[i] not in STOPWORDS and ws[i + 1] not in STOPWORDS \
                        and len(ws[i]) > 2 and len(ws[i + 1]) > 2:
                    bf[f"{ws[i]} {ws[i+1]}"] += 1
        raw["top_words_full"] = wf.most_common(BASELINE_WORD_WINDOW)
        raw["top_bigrams_full"] = bf.most_common(BASELINE_BIGRAM_WINDOW)
        raw["baseline_source"] = {
            "corpus": os.path.abspath(args.corpus),
            "persona": args.persona,
            "corpus_sha256": sha256_file(args.corpus),
        }
        baseline_path.write_text(json.dumps(raw, indent=2), encoding="utf-8")
        print(f"baseline: {len(texts)} lines → {baseline_path}")
        return 0

    if not baseline_path.exists():
        raise SystemExit(
            f"no baseline at {baseline_path} — run with --build-baseline first"
        )
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))

    turns = load_turns(args.export, args.database_url)
    if args.export:
        (obs / ".last-export").write_bytes(Path(args.export).read_bytes())
    state_path = obs / "state.json"
    state = (
        json.loads(state_path.read_text(encoding="utf-8"))
        if state_path.exists() else {"last_turn_id": 0}
    )
    new = [t for t in turns if t["id"] > state["last_turn_id"]]
    if args.conv_regex:
        import re
        pat = re.compile(args.conv_regex)
        kept = [t for t in new if pat.match(t["conversation_id"])]
        print(f"turns: {len(turns)} total, {len(new)} new, "
              f"{len(kept)} after --conv-regex (watermark {state['last_turn_id']})")
        new = kept
    else:
        print(f"turns: {len(turns)} total, {len(new)} new (watermark {state['last_turn_id']})")

    if args.window == "day":
        groups: dict[str, list[dict]] = {}
        for t in new:
            groups.setdefault(t["created_at"][:10], []).append(t)
    else:
        groups = {}
        for t in new:
            groups.setdefault(t["conversation_id"], []).append(t)

    log_path = obs / "log.jsonl"
    applied, noise_sessions = 0, 0
    with open(log_path, "a", encoding="utf-8") as log:
        for key, wturns in sorted(groups.items()):
            conv_id = key if args.window == "conversation" else \
                f"day:{key}+{len({t['conversation_id'] for t in wturns})}conv"
            texts = [t["content"] for t in wturns]
            entry = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "window": args.window,
                "conversation_id": conv_id,
                "turn_ids": [wturns[0]["id"], wturns[-1]["id"]],
                "persona_turns": len(wturns),
                "source_conversations": sorted(
                    {t["conversation_id"] for t in wturns}
                )[:10],
            }
            if len(wturns) < MIN_LINES:
                entry["decision"] = "noise"
                entry["reasons"] = [
                    f"window has {len(wturns)} persona lines < MIN_LINES={MIN_LINES}"
                ]
                noise_sessions += 1
                log.write(json.dumps(entry) + "\n")
                print(f"  noise  {conv_id[:20]} — {len(wturns)} lines < {MIN_LINES}")
                continue

            session = compute_stats(texts)
            report = drift_report(baseline, session)
            entry["stats"] = {
                k: session[k] for k in
                ("total_lines", "avg_words_per_line", "exclamation_ratio",
                 "question_ratio")
            }
            entry["char_length"] = session["char_length"]
            if report["drift"]:
                entry["decision"] = "applied"
                entry["reasons"] = [d["rule"] for d in report["drift"]]
                if not args.dry_run:
                    p = write_proposal(
                        vault, conv_id, wturns, session, report,
                        baseline.get("baseline_source", {}).get("corpus", "?"),
                        window=args.window,
                    )
                    entry["proposal"] = str(p.relative_to(vault))
                applied += 1
                print(f"  APPLY {conv_id[:20]} — {len(report['drift'])} drift rule(s)")
            else:
                entry["decision"] = "noise"
                entry["reasons"] = [n["rule"] for n in report["noise"]] or [
                    "no rule fired"
                ]
                noise_sessions += 1
                print(f"  noise  {conv_id[:20]} — below all thresholds")
            log.write(json.dumps(entry) + "\n")

    if not args.dry_run and new:
        state["last_turn_id"] = max(t["id"] for t in new)
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    print(
        f"done: {applied} proposal(s), {noise_sessions} noise session(s)"
        f"{' (dry-run, nothing written)' if args.dry_run else ''}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
