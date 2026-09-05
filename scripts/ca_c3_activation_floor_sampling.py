#!/usr/bin/env python3
"""CA C3 (HU-2673): activation-score sampling across query classes.

Samples the post-W1 (bge-small-en-v1.5, 384-dim) spreading-activation score
distribution of the Chandler pilot across representative query classes, via
the real-user chat path (same harness pattern as scripts/e0_replay_w6.py).
Each probe runs in its own fresh consented conversation so retrieval_history
(suppression/spreading) cannot contaminate one class's scores with another's.

Query classes (E0 corpus + W3 evidence probes):

  identity          "hey who r u?"                     (E0 turn 1)
  smalltalk         phatic/where-are-you turns          (E0 turns 3,6,7,8)
  episodic_memory   corpus-referencing memory probes    (E0 turns 4,5,13,15,16
                    incl. the W4 turn-34 recall gate     + "days at work")
  ood_assistant_trap world-knowledge / assistant-trap    (W3 OOD1-3 shape +
                    interrogatives                       E0 turns 10,11)
  nonsense          word-salad (E0 turn 14 shape)

C3 doctrine boundary (HU-2469): the floor is an anti-filler inclusion gate,
NOT a domain router — routing is the competence wall's job. This script only
measures; floor derivation + percentiles land in the HU-2673 evidence doc.

Usage:
    python3 scripts/ca_c3_activation_floor_sampling.py \
        > docs/evidence/hu2673_c3_score_sampling_<epoch>.json

Human-readable progress on stderr. Exit 0 = sampling complete.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime

PERSONA = "fdc3a44b-4c0f-565d-b671-4ed0e3bc7894"  # Chandler Bing (Persona-0)
BASE_URL = "http://127.0.0.1:8000"

#: Representative probes per query class. Order-independent: each probe gets
#: a fresh conversation.
QUERY_CLASSES: dict[str, list[str]] = {
    "identity": [
        "hey who r u?",
    ],
    "smalltalk": [
        "what r u up 2?",
        "where are you?",
        "what are you doing tonight?",
        "you seem proud of that",
    ],
    "episodic_memory": [
        "remember those days at work?",
        "who's the worst?",
        "his duck is in my bathtub",
        "what was the first thing I said to you?",
        "who's playing tonight's game?",
    ],
    "ood_assistant_trap": [
        "what's the capital of Australia?",
        "what's a python method for println",
        "can you explain photosynthesis?",
        "how do I file my taxes?",
    ],
    "nonsense": [
        "commitment, camera, person, thing, giraffe",
    ],
}


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


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


def request(method: str, path: str, api_key: str, body: dict | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(
        BASE_URL.rstrip("/") + path,
        method=method,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        data=json.dumps(body).encode() if body is not None else None,
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode() or "{}"
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"_raw": raw[:400]}


def turn_with_retry(api_key: str, conv: str, text: str, attempts: int = 4) -> tuple[int, dict]:
    """POST one chat turn; bounded retry on transient provider failures."""
    delay = 20.0
    for attempt in range(attempts):
        status, body = request(
            "POST", f"/api/v1/chat/{PERSONA}", api_key, {"message": text, "conversation_id": conv}
        )
        transient = status == 429 or status >= 500
        if not transient or attempt == attempts - 1:
            return status, body
        log(f"    transient HTTP {status} (attempt {attempt + 1}/{attempts}); backoff {delay:.0f}s")
        time.sleep(delay)
        delay = min(delay * 2, 90.0)
    return status, body  # pragma: no cover


def consented_conv(api_key: str, conv: str) -> None:
    status, body = request(
        "POST",
        f"/api/v1/chat/{PERSONA}/consent",
        api_key,
        {"conversation_id": conv, "card_version": 3},
    )
    if status not in (200, 409):
        raise SystemExit(f"consent failed: {status} {body}")


def main() -> int:
    api_key = resolve_key()
    records: list[dict] = []
    started = datetime.now(UTC).isoformat()

    for cls, probes in QUERY_CLASSES.items():
        for probe in probes:
            conv = str(uuid.uuid4())
            consented_conv(api_key, conv)
            t0 = time.monotonic()
            status, body = turn_with_retry(api_key, conv, probe)
            latency_ms = int((time.monotonic() - t0) * 1000)
            if status != 200:
                log(f"  [{cls}] HTTP {status} on {probe!r} — recording failure, continuing")
                records.append(
                    {
                        "query_class": cls,
                        "probe": probe,
                        "conversation_id": conv,
                        "http_status": status,
                        "error": str(body)[:300],
                    }
                )
                continue
            trace = body.get("trace") or {}
            acts = trace.get("activated_memories") or []
            scores = sorted(
                (float(a.get("activation_score") or 0.0) for a in acts),
                reverse=True,
            )
            included = trace.get("memory_refs") or []
            record = {
                "query_class": cls,
                "probe": probe,
                "conversation_id": conv,
                "http_status": status,
                "latency_ms": latency_ms,
                "top_score": scores[0] if scores else None,
                "n_included": len(included),
                "scores": scores,
                "competence_wall": bool(trace.get("competence_wall")) or None,
                "caretaker": trace.get("caretaker"),
                "provider": trace.get("provider"),
            }
            records.append(record)
            log(
                f"  [{cls}] {probe!r}: n={len(included)} "
                f"top={scores[0] if scores else float('nan'):.4f} "
                f"wall={bool(trace.get('competence_wall'))} ({latency_ms}ms)"
            )
            time.sleep(1.0)  # gentle pacing; probes share the live lane

    total = sum(1 for r in records if r.get("http_status") == 200)
    failures = len(records) - total
    out = {
        "gate": "CA C3 (HU-2673) activation-score sampling",
        "persona": PERSONA,
        "embeddings": "bge-small-en-v1.5 384-dim (post-W1 cutover HU-2467)",
        "started_at": started,
        "finished_at": datetime.now(UTC).isoformat(),
        "probes_total": len(records),
        "probes_ok": total,
        "probes_failed": failures,
        "query_classes": {k: len(v) for k, v in QUERY_CLASSES.items()},
        "records": records,
    }
    json.dump(out, sys.stdout, indent=1)
    log(f"sampling complete: {total} ok / {failures} failed")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
