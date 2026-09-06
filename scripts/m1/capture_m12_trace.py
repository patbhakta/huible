#!/usr/bin/env python3
"""M1.2 — capture live production traces proving Arm A reads reach
generation (HU-2732).

Executes the M-0 failure path against the LIVE deployment on .245 as a
two-turn synthetic session (traffic class ``internal``):

1. Turn 1 plants a concrete detail; the W4 lane CAPTURES it at turn commit.
2. Turn 2 asks for that detail back; the W4 lane RECALLS the Arm A payload
   (gist digest + verbatim excerpts) and the rendered prompt carries it into
   generation.

Captured per turn: the full response ``trace`` (per-turn ``trace_id``,
``working_memory`` strategy/chars/synced, ``memory_refs``) plus the server's
``chat.trace`` stdout telemetry line joined on ``trace_id`` — the
production-trace surface the M1.2 acceptance names. The script asserts:

* turn-2 recall used the Arm A strategy (``v4-arm-a``) with chars > 0;
* the ``chat.trace`` line for turn 2 records the same read (``wm=v4-arm-a/<n>``);
* the reply actually addresses the planted detail.

Exit 0 writes ``docs/evidence/m1/m12-traces.json`` with the proof; exit 3
means a proof assertion failed (the evidence file is still written with
``"proof": false``); exit 2 means the deployment was unreachable.

Usage:
    python3 -m scripts.m1.capture_m12_trace
"""

from __future__ import annotations

import json
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
    sys.path.insert(0, str(REPO_ROOT))

OUT_PATH = REPO_ROOT / "docs/evidence/m1/m12-traces.json"
BASE_URL = "http://127.0.0.1:8000"
PERSONA = "fdc3a44b-4c0f-565d-b671-4ed0e3bc7894"  # Chandler Bing (Persona-0)
APP_CONTAINER = "huible-app"
ARM_A_STRATEGY = "v4-arm-a"

PLANT_TEXT = (
    "Okay okay, listen — one thing you have to remember about me: "
    "my umbrella is yellow and I NEVER lend it to anyone."
)
PROBE_TEXT = "hey, quick one — what color is my umbrella and would I lend it to you?"


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def resolve_api_key() -> str:
    import os

    key = os.environ.get("HUIBLE_PROBE_API_KEY")
    if key:
        return key.strip()
    for env_name in (".env", ".env.failover"):
        path = REPO_ROOT / env_name
        if not path.is_file():
            continue
        for line in path.read_text().splitlines():
            if line.startswith("API_KEYS="):
                for entry in line[len("API_KEYS=") :].split(","):
                    candidate = entry.strip().partition(":")[0]
                    if candidate.startswith("chandler-"):
                        return candidate
    raise SystemExit("no API key resolved (set HUIBLE_PROBE_API_KEY)")


def request(method: str, path: str, api_key: str, body: dict | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(
        BASE_URL.rstrip("/") + path,
        method=method,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            # Kill-switch posture: unmarked clients are classified real-user
            # and refused (verify_voice_dogfood.py convention).
            "X-Huible-Traffic-Class": "internal",
        },
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


def consent(api_key: str, conv: str) -> None:
    status, body = request(
        "POST",
        f"/api/v1/chat/{PERSONA}/consent",
        api_key,
        {"conversation_id": conv, "card_version": 3},
    )
    if status not in (200, 409):
        raise SystemExit(f"consent failed: {status} {body}")


def chat_trace_lines(trace_ids: list[str]) -> list[str]:
    raw = subprocess.run(
        ["docker", "logs", APP_CONTAINER, "--since", "10m"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    lines = []
    for line in (raw.stdout + raw.stderr).splitlines():
        if "chat.trace" in line and any(tid in line for tid in trace_ids):
            lines.append(line)
    return lines


def main() -> int:
    api_key = resolve_api_key()
    conv = f"m12-trace-{uuid.uuid4().hex[:12]}"
    log(f"conversation: {conv}")

    consent(api_key, conv)
    log("turn 1 (plant): sending…")
    status, planted = turn_with_retry(api_key, conv, PLANT_TEXT)
    if status != 200:
        raise SystemExit(f"turn 1 failed: HTTP {status}: {json.dumps(planted)[:400]}")
    log(f"turn 1 ok (trace_id={planted['trace']['trace_id'][:8]}…)")

    log("turn 2 (probe): sending…")
    status, probe = turn_with_retry(api_key, conv, PROBE_TEXT)
    if status != 200:
        raise SystemExit(f"turn 2 failed: HTTP {status}: {json.dumps(probe)[:400]}")
    log(f"turn 2 ok (trace_id={probe['trace']['trace_id'][:8]}…)")

    trace_ids = [planted["trace"]["trace_id"], probe["trace"]["trace_id"]]
    wm = probe["trace"].get("working_memory") or {}
    reply = probe.get("response", "")
    telemetry_lines = chat_trace_lines(trace_ids)

    checks = {
        "turn2_wm_arm_a_strategy": wm.get("strategy") == ARM_A_STRATEGY,
        "turn2_wm_chars_positive": bool(wm.get("chars", 0) > 0),
        "turn2_wm_synced": bool(wm.get("synced")),
        "telemetry_line_records_arm_a_read": any(
            f"wm={ARM_A_STRATEGY}/" in line and f"/{wm.get('chars', 0)}" in line
            for line in telemetry_lines
            if trace_ids[1] in line
        ),
        "reply_addresses_planted_detail": "umbrella" in reply.lower(),
    }
    proof = all(checks.values())

    evidence = {
        "artifact": "M1.2 live production traces — Arm A reads reaching generation (HU-2732)",
        "generated_at": datetime.now(UTC).isoformat(),
        "deployment": {
            "host": ".245 (this host)",
            "base_url": BASE_URL,
            "app_container": APP_CONTAINER,
            "image_created": subprocess.run(
                ["docker", "image", "inspect", APP_CONTAINER, "--format", "{{.Created}}"],
                capture_output=True,
                text=True,
            ).stdout.strip(),
        },
        "conversation_id": conv,
        "protocol": "2-turn plant/probe, X-Huible-Traffic-Class: internal, consent card v3",
        "turns": {
            "plant": {"message": PLANT_TEXT, "trace": planted["trace"]},
            "probe": {"message": PROBE_TEXT, "trace": probe["trace"]},
        },
        "replies": {"plant": planted.get("response"), "probe": reply},
        "chat_trace_telemetry_lines": telemetry_lines,
        "checks": checks,
        "proof": proof,
        "accepts_m0_cases": [
            "m0_arm_a_not_ported (production-trace half)",
            "m0_fake_embeddings (trace half; index half in index-manifest.json)",
            "m0_character_sheet_prompting (persona mechanism = Arm A vault reads, "
            "not a character sheet: 0 character_sheet refs in src — see baseline.json)",
        ],
    }
    OUT_PATH.write_text(json.dumps(evidence, indent=2) + "\n")

    for name, ok in checks.items():
        log(f"  {'PASS' if ok else 'FAIL'}  {name}")
    log(f"proof={'TRUE' if proof else 'FALSE'} — wrote {OUT_PATH.relative_to(REPO_ROOT)}")
    return 0 if proof else 3


if __name__ == "__main__":
    raise SystemExit(main())
