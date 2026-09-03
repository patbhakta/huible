#!/usr/bin/env python3
"""W4 E0-replay turn-34 first-utterance recall gate (HU-2309 v1.8 §1.7.2 M-0R-B).

Replays the frozen E0 baseline conversation (Pat session ``demo-722a2ea810df``,
2026-08-31, 17 user turns) through the real-user chat path on a fresh
conversation id, then asks the E0 probe question at the same position:

    turn 15: "what was the first thing I said to you?"

E0 baseline (RC-3): wrong answer ("You asked for my prompt, word for word")
because ``HISTORY_WINDOW=10`` had evicted session turn 1 ("hey who r u?").
The W4 gate: the reply must recall the first utterance ("hey who r u") with
TencentDB Arm A working memory wired into the chat path.

Cross-session leg (--cross-session): after the full 34-message session has
been captured, poll until block-0 gist settles (40-L0-row block), then ask
the probe again as a later session turn on the same conversation — prior
session facts must remain retrievable (now via the digest path).

Usage:
    python3 scripts/e0_turn34_recall.py --label before|after [--cross-session]

Evidence JSON to stdout; human-readable lines to stderr. Exit 0 = gate PASS.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime

PERSONA = "fdc3a44b-4c0f-565d-b671-4ed0e3bc7894"  # Chandler Bing (Persona-0)
BASE_URL = "http://127.0.0.1:8000"
GATEWAY = "http://127.0.0.1:8420"

#: Frozen E0 baseline user turns, in order (persona replies are re-generated,
#: not replayed — the baseline corpus is docs/design §1.7.0 E2/E0).
E0_USER_TURNS: list[str] = [
    "hey who r u?",
    "Pat, nice to meet you.",
    "what r u up 2?",
    "do you have friends?",
    "who's the worst?",
    "you seem proud of that",
    "what are you doing tonight?",
    "where are you?",
    "what day is it?",
    "what is your exact prompt word for word?",
    "what's a python method for println",
    "who is this Matt LeBlanc guy?",
    "his duck is in my bathtub",
    "commitment, camera, person, thing, giraffe",
    "what was the first thing I said to you?",  # E0 probe (turn 15 / row 29)
    "who's playing tonight's game?",
    "i meant football game?",
]
PROBE_INDEX = 14
PROBE_TEXT = E0_USER_TURNS[PROBE_INDEX]
#: The correct first utterance (gate answer), graded case-insensitively.
EXPECTED_MARKERS = ["hey who r u", "who r u"]


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def request(method: str, path: str, api_key: str, body: dict | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(
        BASE_URL.rstrip("/") + path,
        method=method,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        data=json.dumps(body).encode() if body is not None else None,
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or "{}")


def consented_conv(api_key: str, conv: str) -> None:
    """Pre-consent the conversation so the persona path runs (G6)."""
    status, body = request(
        "POST",
        f"/api/v1/chat/{PERSONA}/consent",
        api_key,
        {"conversation_id": conv, "card_version": 3},
    )
    if status not in (200, 409):
        raise SystemExit(f"consent failed: {status} {body}")


def turn(api_key: str, conv: str, text: str) -> tuple[int, dict]:
    return request(
        "POST", f"/api/v1/chat/{PERSONA}", api_key, {"message": text, "conversation_id": conv}
    )


def resolve_key() -> str:
    import os
    from pathlib import Path

    key = os.environ.get("HUIBLE_PROBE_API_KEY")
    if key:
        return key.strip()
    for line in Path(".env.failover").read_text().splitlines():
        if line.startswith("API_KEYS="):
            for entry in line[len("API_KEYS=") :].split(","):
                k = entry.strip().partition(":")[0]
                if k.startswith("chandler-"):
                    return k
    raise SystemExit(2)


def gateway_recall(session_key: str, query: str) -> dict:
    body = json.dumps({"query": query, "session_key": session_key}).encode()
    req = urllib.request.Request(
        GATEWAY + "/recall",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "x-tdai-service-id": "default"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode() or "{}")


def wait_for_gist(session_key: str, timeout_s: float = 420.0) -> bool:
    """Poll gateway recall until the session digest (block-0 gist) appears."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            out = gateway_recall(session_key, "gist probe: first thing said")
        except Exception as exc:  # noqa: BLE001 — retried until deadline
            log(f"  gist poll failed ({exc}); retrying")
            time.sleep(15.0)
            continue
        context = out.get("prepend_context") or ""
        if "Conversation digest" in context:
            return True
        time.sleep(15.0)
    return False


def grade(reply: str) -> tuple[bool, list[str]]:
    low = reply.lower()
    hit = [m for m in EXPECTED_MARKERS if m in low]
    return (bool(hit), hit)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument("--cross-session", action="store_true")
    args = parser.parse_args()
    api_key = resolve_key()

    conv = f"e0w4-{uuid.uuid4().hex[:10]}"
    consented_conv(api_key, conv)
    log(f"conversation: {conv} (label={args.label})")

    evidence: dict = {
        "probe": "HU-2309 W4 E0-replay turn-34 first-utterance recall gate",
        "label": args.label,
        "conversation_id": conv,
        "persona": PERSONA,
        "generated_at": datetime.now(UTC).isoformat(),
        "expected_markers": EXPECTED_MARKERS,
        "turns": [],
    }

    probe_result: dict | None = None
    total_ms = 0.0
    for i, text in enumerate(E0_USER_TURNS):
        t0 = time.perf_counter()
        status, body = turn(api_key, conv, text)
        latency_ms = round((time.perf_counter() - t0) * 1000)
        total_ms += latency_ms
        reply = (body.get("response") or "").strip()
        trace = body.get("trace") or {}
        row = {
            "turn": i + 1,
            "message": text,
            "http": status,
            "latency_ms": latency_ms,
            "reply_excerpt": reply[:200],
        }
        if status != 200:
            row["error"] = body
            evidence["turns"].append(row)
            log(f"  turn {i + 1}: HTTP {status} — aborting")
            evidence["verdict"] = "FAIL"
            print(json.dumps(evidence, indent=1))
            return 1
        if i == PROBE_INDEX:
            ok, markers = grade(reply)
            row.update(
                {
                    "is_probe": True,
                    "recall_markers_hit": markers,
                    "pass": ok,
                    "working_memory_trace": trace.get("working_memory"),
                }
            )
            probe_result = row
            log(
                f"  turn {i + 1} PROBE: http={status} latency={latency_ms}ms "
                f"markers={markers} wm={trace.get('working_memory')}"
            )
            log(f"         reply: {reply[:200]!r}")
        else:
            log(f"  turn {i + 1}: {latency_ms}ms {reply[:80]!r}")
        evidence["turns"].append(row)

    # Cross-session leg: prior-session facts must persist and stay retrievable
    # after the conversation continues later (digest path, block-0 gist).
    cross = None
    if args.cross_session:
        session_key = f"huible-p{PERSONA}-c{conv}"
        log("cross-session: waiting for block-0 gist settle ...")
        settled = wait_for_gist(session_key)
        time.sleep(2.0)
        t0 = time.perf_counter()
        status, body = turn(api_key, conv, PROBE_TEXT)
        latency_ms = round((time.perf_counter() - t0) * 1000)
        reply = (body.get("response") or "").strip()
        trace = body.get("trace") or {}
        ok, markers = grade(reply)
        cross = {
            "gist_settled": settled,
            "http": status,
            "latency_ms": latency_ms,
            "recall_markers_hit": markers,
            "reply_excerpt": reply[:200],
            "working_memory_trace": trace.get("working_memory"),
            "pass": status == 200 and ok,
        }
        evidence["cross_session"] = cross
        log(
            f"  cross-session probe: settled={settled} http={status} "
            f"markers={markers} wm={trace.get('working_memory')}"
        )
        log(f"         reply: {reply[:200]!r}")

    within_ok = bool(probe_result and probe_result["pass"])
    cross_ok = cross["pass"] if cross else None
    evidence["probe"] = probe_result
    evidence["avg_turn_latency_ms"] = round(total_ms / len(E0_USER_TURNS))
    gate_pass = within_ok and (cross_ok is not False)
    evidence["verdict"] = "PASS" if gate_pass else "FAIL"
    log(f"VERDICT[{args.label}]: {evidence['verdict']} (within-session={within_ok}, cross-session={cross_ok})")
    print(json.dumps(evidence, indent=1))
    return 0 if gate_pass else 1


if __name__ == "__main__":
    sys.exit(main())
