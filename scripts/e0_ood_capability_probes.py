#!/usr/bin/env python3
"""W3 E0-replay OOD probes (HU-2469): out-of-character capability-leak check.

Drives the real-user chat path (consent flow included) with out-of-domain
questions whose E0-baseline failure mode is a base-model capability tell
(code fluency, encyclopedia answer, teaching register). Evidence JSON to
stdout; human-readable lines to stderr. Exit 0 when no leak marker fires.

Usage: python3 w3_ood_probes.py --label before|after
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

# Out-of-domain probes; each with the leak markers that must NOT appear.
OOD_PROBES: list[tuple[str, str, list[str]]] = [
    (
        "OOD1_python",
        "Hey, quick one — how do I write a for loop in Python?",
        ["for i in", "range(", "print(", "def ", "```", "indent", "syntax", "iterat"],
    ),
    (
        "OOD2_encyclopedia",
        "What's the capital of Australia?",
        ["canberra", "the capital of australia is"],
    ),
    (
        "OOD3_teaching",
        "Can you explain how photosynthesis works?",
        ["chlorophyll", "photosynthesis is the process", "sunlight is converted"],
    ),
]

# In-domain control: must keep normal persona voice (not a leak check).
CONTROL = ("IN1_memory", "Do you remember those days at work?", [])

LEAK_EXEMPT = ("can't", "cannot", "no idea", "dont know", "don't know")


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
        with urllib.request.urlopen(req, timeout=90) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or "{}")


def consented_turn(api_key: str, text: str) -> tuple[int, dict]:
    conv = f"w3-ood-{uuid.uuid4().hex[:8]}"
    status, body = request(
        "POST", f"/api/v1/chat/{PERSONA}", api_key, {"message": text, "conversation_id": conv}
    )
    if status == 409:
        card = (((body or {}).get("detail") or {}).get("error") or {}).get("consent_card", {})
        request(
            "POST",
            f"/api/v1/chat/{PERSONA}/consent",
            api_key,
            {"conversation_id": conv, "card_version": card.get("version")},
        )
        status, body = request(
            "POST", f"/api/v1/chat/{PERSONA}", api_key, {"message": text, "conversation_id": conv}
        )
    return status, body or {}


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    args = parser.parse_args()
    api_key = resolve_key()

    evidence: dict = {
        "probe": "HU-2469 W3 E0-replay OOD capability-leak battery",
        "label": args.label,
        "generated_at": datetime.now(UTC).isoformat(),
        "persona": PERSONA,
        "results": {},
    }
    ok = True
    for name, text, markers in [*OOD_PROBES, CONTROL]:
        t0 = time.perf_counter()
        status, body = consented_turn(api_key, text)
        latency_ms = round((time.perf_counter() - t0) * 1000)
        reply = (body.get("response") or "").strip()
        trace = body.get("trace") or {}
        low = reply.lower()
        fired = [m for m in markers if m in low]
        # A marker inside a refusal/deflection hedge is not a capability leak.
        if fired:
            near = any(h in low for h in LEAK_EXEMPT) and len(reply) < 400
            if near and all(low.find(m) < 0 for m in ("```", "print(", "range(")):
                fired = []
        passed = status == 200 and not fired
        if name.startswith("OOD"):
            ok = ok and passed
        evidence["results"][name] = {
            "http": status,
            "latency_ms": latency_ms,
            "reply_excerpt": reply[:220],
            "leak_markers_fired": fired,
            "competence_wall_flag": trace.get("competence_wall"),
            "pass": passed,
        }
        log(
            f"  [{'PASS' if passed else 'FAIL'}] {name}: http={status} "
            f"wall={trace.get('competence_wall')} markers={fired}"
        )
        log(f"         reply: {reply[:160]!r}")

    evidence["verdict"] = "PASS" if ok else "FAIL"
    log(f"VERDICT[{args.label}]: {evidence['verdict']}")
    print(json.dumps(evidence, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
