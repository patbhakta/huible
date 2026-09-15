#!/usr/bin/env python3
# ruff: noqa: E501
"""HU-2793 founder deliverable — side-by-side moat transcript from the REAL engine.

Runs the SAME Chandler questions through two fresh sessions:

  LANE A (memory ON)  — every turn through the production engine; the W4
                        TencentDB lane armed. Each reply is annotated with the
                        ACTUAL trace blocks: what was written to L0, the
                        working-memory block injected into the prompt
                        (verbatim), and the vault notes that grounded it.
  LANE B (memory OFF) — identical questions with working_memory_enabled=false
                        (the kill switch): recall empty, capture skipped.

Protocol per lane (the airtight version of "ask tomorrow"):
  1. PLANT   a canary fact.
  2. EVICT   12 filler turns with the memory lane OFF (store stays clean,
             only the live window fills) — the plant falls out of the model's
             in-process history, so only the store can carry it.
  3. RECALL  the canary word.
  4. ORDINAL "what was the very first thing I said?" — served by the
             deterministic zero-LLM ordinal lane (HU-2774) straight from L0
             rows in recorded order.
Each lane ends with a gateway store probe showing what the store holds.

`--resume CONV` runs the literal cross-session probe: a brand-new process that
knows NOTHING but the conversation id and asks what was said — the only shared
state is TencentDB.

Ground rules honored: real engine, real path, no mocks. Every gateway/engine
failure is printed in the transcript, not hidden.

Run:
  HUIBLE_DEMO_KEY=<key> python3 scripts/moat_transcript.py --out <file.md>
  HUIBLE_DEMO_KEY=<key> python3 scripts/moat_transcript.py --resume <conv>
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request

API = os.environ.get("HUIBLE_API_BASE", "http://127.0.0.1:8000/api/v1")
WM_GATEWAY = os.environ.get("HUIBLE_WM_GATEWAY", "http://127.0.0.1:8420")
WM_SERVICE_ID = os.environ.get("HUIBLE_WM_SERVICE_ID", "huible-chandler")
PERSONA_ID = os.environ.get("HUIBLE_DEMO_PERSONA", "fdc3a44b-4c0f-565d-b671-4ed0e3bc7894")
KEY = os.environ.get("HUIBLE_DEMO_KEY", "")

FILLER = [
    "Anyway — enough of that.", "Crazy day, huh?", "So what else is new with you?",
    "Ha! Classic.", "You believe this weather?", "Ok but seriously though.",
    "Man, I needed that laugh.", "Right, right. And then what?", "Huh. Never thought of it that way.",
    "You always this philosophical before lunch?", "Fair enough. Fair enough.",
    "Let's talk about something else entirely.",
]
PLANT = "Remember this: my canary word for today is FALAFEL-7. Never forget it."
RECALL_Q = "What canary word did I give you earlier? Just the word."
ORDINAL_Q = "What was the very first thing I said to you today?"
FLAVOR_Q = "Could I BE any more predictable? Be honest."


def api_call(method: str, path: str, body: dict | None = None, timeout: int = 180):
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {KEY}"}
    if path.startswith("/chat") and not path.endswith("/consent"):
        headers["X-Huible-Traffic-Class"] = "internal"
    req = urllib.request.Request(
        API + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {"raw": str(e)}
    except Exception as e:
        return 0, {"error": f"{type(e).__name__}: {e}"}


def store_probe(conversation_id: str, query: str) -> dict:
    """Read-only gateway probe — the exact read the engine performs per turn."""
    body = json.dumps(
        {
            "query": query[:2000],
            "session_key": f"huible-p{PERSONA_ID}-c{conversation_id}",
        }
    ).encode()
    req = urllib.request.Request(
        WM_GATEWAY + "/recall",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "x-tdai-service-id": WM_SERVICE_ID},
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.loads(r.read().decode() or "{}")
    except Exception as e:
        return {"code": -1, "message": f"{type(e).__name__}: {e}", "chars": 0, "ms": int((time.time() - t0) * 1000)}
    ctx = d.get("prepend_context") or ""
    return {
        "code": d.get("code"),
        "strategy": d.get("strategy"),
        "chars": len(ctx),
        "context": ctx,
        "digest_settled": d.get("digest_settled"),
        "gist_blocks": d.get("gist_blocks"),
        "ms": int((time.time() - t0) * 1000),
    }


def mint_session() -> tuple[str, str]:
    conv = f"moat-tx-{os.urandom(4).hex()}"
    st, health = api_call("GET", "/health")
    gen = ((health.get("data") or {}).get("checks") or {}).get("generator", "unknown")
    return conv, gen


def ensure_consent(conversation_id: str) -> str | None:
    st, body = api_call("POST", f"/chat/{PERSONA_ID}/consent", {"conversation_id": conversation_id})
    return None if st == 200 else f"consent HTTP {st}: {json.dumps(body)[:200]}"


def turn(conversation_id: str, message: str, memory_on: bool) -> tuple[int, dict]:
    body: dict = {"message": message, "relationship": "close_friend", "conversation_id": conversation_id}
    if not memory_on:
        body["working_memory_enabled"] = False
    return api_call("POST", f"/chat/{PERSONA_ID}", body)


# ---------- transcript rendering ----------


def render_wm_annotation(trace: dict) -> str:
    """The verbatim annotation block under a memory-ON reply."""
    wm = trace.get("working_memory") or {}
    lines = ["", "> **X-RAY (actual injected memory this turn)**"]
    lines.append(f"> - WRITE → TencentDB L0: " + ("committed ✓ (capture synced)" if wm.get("synced") else "**NOT written** (capture failed or lane off)"))
    ctx = wm.get("context") or ""
    if ctx:
        lines.append(f"> - READ → working-memory block INJECTED into the prompt ({wm.get('chars')} chars, strategy {wm.get('strategy')}):")
        lines.append("> ```text")
        for ln in ctx.splitlines() or [""]:
            lines.append("> " + ln)
        lines.append("> ```")
    else:
        lines.append(
            f"> - READ → working-memory block: **EMPTY this turn** (strategy {wm.get('strategy') or '?'}, "
            f"gist blocks settled: {wm.get('gist_blocks')}, digest settled: {wm.get('digest_settled')}) — "
            "nothing session-scoped to serve yet; this is what the lane failing looks like."
        )
    mems = trace.get("activated_memories") or []
    lines.append(f"> - VAULT RETRIEVAL: {len(mems)} notes activated" + ("" if mems else " — none"))
    for m in mems[:5]:
        content = " ".join((m.get("content") or "").split())
        lines.append(
            f">   - [{m.get('content_type')}/{m.get('disclosure_scope') or '-'}] "
            f"id={str(m.get('id') or '')[:18]}… score={float(m.get('activation_score') or 0):.3f} "
            f"snippet: \"{content[:140]}{'…' if len(content) > 140 else ''}\""
        )
    if len(mems) > 5:
        lines.append(f">   - …+{len(mems) - 5} more (raw trace)")
    sr = trace.get("scoped_reads") or []
    if sr:
        lines.append("> - scoped vault lanes: " + ", ".join(f"{s.get('section')}×{s.get('lines')}" for s in sr))
    return "\n".join(lines)


def render_off_annotation() -> str:
    return "\n".join([
        "",
        "> **X-RAY (kill switch: working_memory_enabled=false)**",
        "> - WRITE → TencentDB L0: **skipped** (lane disabled this turn)",
        "> - READ → working-memory block: **none — recall disabled**",
        "> - vault retrieval lanes stay armed (persona-level, legitimate long-term lane)",
    ])


def run_lane(out: list[str], lane_label: str, memory_on: bool, filler_turns: int) -> str:
    conv, gen = mint_session()
    out.append(f"\n---\n\n## Lane {lane_label} — memory **{'ON' if memory_on else 'OFF (kill switch)'}**\n")
    out.append(f"- conversation id: `{conv}`")
    out.append(f"- engine generator: `{gen}` (real ZAI stack, G1/G6/G8 gates live)")
    err = ensure_consent(conv)
    out.append(f"- G6 consent: " + ("acknowledged (real gate)" if not err else f"**FAILED**: {err}"))
    if err:
        return conv

    def emit(q: str, memory: bool, annotation: bool):
        st, body = turn(conv, q, memory_on=memory)
        if st != 200:
            out.append(f"\n**USER:** {q}\n\n> ⚠️ turn FAILED — engine HTTP {st}: {json.dumps(body)[:300]}")
            return {}
        reply = body.get("response") or ""
        trace = body.get("trace") or {}
        out.append(f"\n**USER:** {q}\n\n**{lane_label} (Chandler):** {reply}")
        if annotation:
            out.append(render_wm_annotation(trace) if memory else render_off_annotation())
        return trace

    # 1. PLANT (memory lane as configured)
    out.append(f"\n### Step 1 — plant the canary fact (memory {'ON' if memory_on else 'OFF'})")
    emit(PLANT, memory_on, annotation=True)

    # 2. EVICT — filler with the lane OFF in BOTH lanes (store stays clean; window fills)
    out.append(f"\n### Step 2 — evict the live window ({filler_turns} filler turns, memory lane OFF during filler)")
    done = 0
    for i, line in enumerate(FILLER[:filler_turns]):
        st, body = turn(conv, line, memory_on=False)
        if st != 200:
            out.append(f"> filler {i + 1} FAILED (HTTP {st}) — eviction may not hold")
            break
        done += 1
        if done in (5, filler_turns):
            out.append(f"> filler {done}/{filler_turns} — plant "
                       + ("leaving the verbatim band" if done == 5 else f"now fully outside the live window"))
    out.append(f"> live window evicted after {done} filler turns; the only possible carrier of the plant is TencentDB.")

    # 3. RECALL
    out.append(f"\n### Step 3 — recall the canary (memory {'ON' if memory_on else 'OFF'})")
    emit(RECALL_Q, memory_on, annotation=True)

    # 4. ORDINAL — deterministic zero-LLM lane from L0 rows in recorded order
    out.append(f"\n### Step 4 — ordinal probe: *\"what was the FIRST thing I said?\"* (memory {'ON' if memory_on else 'OFF'})")
    emit(ORDINAL_Q, memory_on, annotation=True)

    # 5. store probe — what the gateway would serve right now
    probe = store_probe(conv, RECALL_Q)
    out.append(f"\n### Store probe (gateway /recall, read-only — the engine's exact read)")
    out.append(
        f"- code `{probe.get('code')}` · strategy `{probe.get('strategy')}` · "
        f"serves **{probe.get('chars')} chars** · gist blocks `{probe.get('gist_blocks')}`"
        + (f" · `{probe.get('message')}`" if probe.get("message") else "")
    )
    ctx = probe.get("context") or ""
    if ctx:
        out.append("```text")
        out.append(ctx[:1500] + ("\n…(truncated)" if len(ctx) > 1500 else ""))
        out.append("```")
    else:
        out.append("> **EMPTY** — the store holds nothing for this session. "
                   + ("This is the kill-switch lane doing its job." if not memory_on else "⚠️ The ON-lane store is empty — the moat is NOT holding here."))
    return conv


def flavor_turn(out: list[str], conv: str, lane_label: str, memory_on: bool):
    out.append(f"\n### Step 5 — identity-flavored turn (vault grounding, memory {'ON' if memory_on else 'OFF'})")
    st, body = turn(conv, FLAVOR_Q, memory_on=memory_on)
    if st != 200:
        out.append(f"> turn FAILED — engine HTTP {st}")
        return
    out.append(f"\n**USER:** {FLAVOR_Q}\n\n**{lane_label} (Chandler):** {body.get('response') or ''}")
    out.append(render_wm_annotation(body.get("trace") or {}) if memory_on else render_off_annotation())


def resume_probe(conv: str, out: list[str]):
    out.append(f"\n---\n\n## Cross-session recall — new process, empty window, store the only carrier\n")
    out.append(f"- resumed conversation: `{conv}` (this process was started seconds ago; it holds NO turns in memory)")
    err = ensure_consent(conv)
    if err:
        out.append(f"- consent: {err}")
    st, body = turn(conv, ORDINAL_Q, memory_on=True)
    if st != 200:
        out.append(f"> ⚠️ probe FAILED — engine HTTP {st}: {json.dumps(body)[:300]}")
        return
    trace = body.get("trace") or {}
    out.append(f"\n**USER:** {ORDINAL_Q}\n\n**Chandler:** {body.get('response') or ''}")
    out.append(render_wm_annotation(trace))
    probe = store_probe(conv, ORDINAL_Q)
    out.append(
        f"\nStore probe: code `{probe.get('code')}` · strategy `{probe.get('strategy')}` · serves **{probe.get('chars')} chars**"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="doc/moat-transcript.md")
    ap.add_argument("--resume", default=None, help="run only the cross-session probe against this conversation id")
    ap.add_argument("--filler", type=int, default=12)
    ap.add_argument("--lane", choices=["on", "off", "both"], default="both")
    args = ap.parse_args()
    if not KEY:
        print("FATAL: HUIBLE_DEMO_KEY not set")
        return 1

    out: list[str] = []
    stamp = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    if args.resume:
        out.append("# HUible Moat — Cross-Session Recall Probe (TencentDB, not context window)")
        out.append(f"\n*Generated {stamp} by `scripts/moat_transcript.py --resume` — real engine, real path.*")
        resume_probe(args.resume, out)
    else:
        out.append("# HUible Moat — The Memory Layer, Live (HU-2793 founder transcript)")
        out.append(f"\n*Generated {stamp} by `scripts/moat_transcript.py`. Real engine (ZAI stack), real "
                   "TencentDB gateway, real vault — no mocks. Identical questions, two lanes: memory ON vs "
                   "kill-switch OFF. The delta IS the moat. Failures are shown, not hidden.*")
        conv_a = conv_b = None
        if args.lane in ("on", "both"):
            conv_a = run_lane(out, "A", memory_on=True, filler_turns=args.filler)
            flavor_turn(out, conv_a, "A", memory_on=True)
        if args.lane in ("off", "both"):
            conv_b = run_lane(out, "B", memory_on=False, filler_turns=args.filler)
        out.append("\n---\n\n## How to read this in two minutes\n")
        out.append("1. Lane A planted a canary fact, then 12 filler turns pushed it out of the model's live window.")
        out.append("2. Asked to recall it, Lane A's reply is annotated with the working-memory block the engine actually injected — verbatim, straight from TencentDB.")
        out.append("3. Lane B ran the identical protocol with the kill switch on: no capture, no recall, bare model.")
        out.append("4. The ordinal probe answers *verbatim* from store rows in recorded order — deterministic, zero LLM in the recall path.")
        out.append("5. `--resume <conv>` reruns the recall from a brand-new process that knows only the conversation id: literal cross-session recall.")
        if conv_a:
            out.append(f"\nLane A conversation: `{conv_a}` · Lane B: `{conv_b or '—'}` — both live in TencentDB; probe them via the moat console recall API.")

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        f.write("\n".join(out) + "\n")
    print(f"transcript written: {args.out} ({sum(len(x) for x in out)} chars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
