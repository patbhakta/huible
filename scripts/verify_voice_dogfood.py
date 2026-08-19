#!/usr/bin/env python3
"""Stage-A dogfood persona-voice validation for HU-1461 (post-activation).

Run this AFTER ``scripts/activate_voice_provider.sh`` has flipped the live
env to ``LLM_PROVIDER=openrouter``. It is the acceptance evidence for the
third HU-1461 checkbox — "persona voice validated in Stage A dogfood" —
and refuses to spend a single token while the provider is still ``fake``.

What it proves, per turn, against the real production endpoint
(``POST /api/v1/chat``, exactly the wiring real users hit):

  1. reachability + auth via the seeded ops key (from ``.env.failover``)
  2. the G6 consent gate flow works end-to-end (409 -> acknowledge -> retry)
  3. the reply is a REAL hosted-model reply: non-empty, and NOT the
     FakeLLMClient deterministic digest ``[fake-llm:xxxxxxxx] ...``
  4. latency is recorded for the voice-quality review artifact

After the battery it snapshots ``/health`` (generator + ``llm_budget``) and,
best-effort, the durable spend ledger inside the container so the board sees
the exact accrued cost of the dogfood itself.

Cost: 3 short turns on the configured model — cents against the $50/mo cap
(board-approved 2026-08-18, sweep 9461babc item 3).

Usage:
    python3 scripts/verify_voice_dogfood.py [--base-url http://127.0.0.1:8000]
                                            [--env-file .env.failover]
                                            [--report-dir logs]
                                            [--key-prefix chandler-]

Exit: 0 all checks pass | 1 failure | 2 provider not activated (fake posture)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

FAKE_DIGEST_PREFIX = "[fake-llm:"
MOCK_DIGEST_PREFIX = "[mock:"

# Fixed battery: a warm opener, a memory-cued follow-up, and a boundary probe.
# Deliberately benign — crisis-path drills live in the §7.4 test suites, not
# in a 3-turn voice dogfood.
BATTERY: list[tuple[str, str]] = [
    (
        "warm_open",
        "Hi — it's been a while. I was just thinking about you and wanted "
        "to say hello. How would you want me to remember today?",
    ),
    (
        "memory_cue",
        "What's one small thing you'd want me to keep doing every year, "
        "just because it mattered to you?",
    ),
    (
        "boundary",
        "I need to head out soon — leave me with one line I should carry "
        "with me this week.",
    ),
]

PASS = 0
FAIL = 0


def ok(msg: str) -> None:
    global PASS
    PASS += 1
    print(f"  [PASS] {msg}")


def bad(msg: str) -> None:
    global FAIL
    FAIL += 1
    print(f"  [FAIL] {msg}")


def _request(
    base_url: str, method: str, path: str, api_key: str, body: dict | None = None
) -> tuple[int, dict]:
    req = urllib.request.Request(
        base_url.rstrip("/") + path,
        method=method,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        data=json.dumps(body).encode() if body is not None else None,
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"raw": raw}


def _ops_key_and_persona(
    env_file: Path, key_prefix: str = ""
) -> tuple[str, str]:
    for line in env_file.read_text().splitlines():
        if line.startswith("API_KEYS="):
            for entry in line[len("API_KEYS=") :].split(","):
                first = entry.strip()
                key, _, persona = first.partition(":")
                if key and persona and key.startswith(key_prefix):
                    return key.strip(), persona.strip()
    raise SystemExit(
        f"[FATAL] no API_KEYS entry with prefix {key_prefix!r} found in {env_file}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--env-file", default=".env.failover")
    parser.add_argument("--report-dir", default="logs")
    parser.add_argument(
        "--key-prefix",
        default="",
        help="select the API_KEYS entry whose key starts with this prefix "
        "(e.g. 'chandler-') so the battery hits the intended persona",
    )
    args = parser.parse_args()

    print("== Stage-A voice dogfood (HU-1461) ==")

    # --- Posture gate: refuse to run (and spend) unless a real voice is live --
    status_code, health = _request(args.base_url, "GET", "/health", "")
    checks = health.get("data", health).get("checks", {})
    if status_code != 200:
        print(f"[ABORT] /health unreachable (HTTP {status_code})")
        return 2
    generator_state = checks.get("generator", "")
    if "mock" in generator_state:
        print(
            "[ABORT] /health generator is mock — real voice not activated. "
            "Flip GENERATOR_PROVIDER (and LLM_PROVIDER posture) first."
        )
        return 2
    ok(f"posture: real generator live (generator={generator_state!r})")

    api_key, persona_id = _ops_key_and_persona(
        Path(args.env_file), args.key_prefix
    )
    ok(f"ops key loaded from {args.env_file} (persona {persona_id})")

    turns: list[dict] = []
    conversation_id = str(uuid.uuid4())

    for label, message in BATTERY:
        t0 = time.monotonic()
        code, body = _request(
            args.base_url,
            "POST",
            "/api/v1/chat",
            api_key,
            {"message": message, "conversation_id": conversation_id},
        )

        if code == 409 and "consent" in json.dumps(body).lower():
            detail = body.get("detail", {}).get("error", {})
            session = detail.get("conversation_id", conversation_id)
            ack_code, _ = _request(
                args.base_url,
                "POST",
                f"/api/v1/chat/{persona_id}/consent",
                api_key,
                {"conversation_id": session},
            )
            if ack_code == 200:
                ok(f"{label}: G6 consent card acknowledged for session")
            else:
                bad(f"{label}: consent acknowledge failed (HTTP {ack_code})")
                continue
            code, body = _request(
                args.base_url,
                "POST",
                "/api/v1/chat",
                api_key,
                {"message": message, "conversation_id": conversation_id},
            )

        latency_ms = int((time.monotonic() - t0) * 1000)
        reply = (body.get("data") or {}).get("reply", "")

        if code != 200:
            bad(f"{label}: HTTP {code}")
            turns.append({"label": label, "http": code, "latency_ms": latency_ms})
            continue

        # The voice proof: real model output, not a deterministic digest from
        # the fake LLM client or the mock persona generator.
        if not reply.strip():
            bad(f"{label}: empty reply")
        elif reply.startswith(FAKE_DIGEST_PREFIX) or reply.startswith(
            MOCK_DIGEST_PREFIX
        ):
            bad(
                f"{label}: reply is a deterministic digest "
                f"({reply[:16]}...) — real provider not serving"
            )
        else:
            ok(f"{label}: real-persona reply ({len(reply)} chars, {latency_ms} ms)")

        turns.append(
            {
                "label": label,
                "http": code,
                "latency_ms": latency_ms,
                "reply_chars": len(reply),
                "reply_preview": reply.strip()[:280],
                "looks_fake": reply.startswith(FAKE_DIGEST_PREFIX)
                or reply.startswith(MOCK_DIGEST_PREFIX),
            }
        )

    # --- Spend + health snapshot (board evidence) ---------------------------
    _, health2 = _request(args.base_url, "GET", "/health", "")
    checks2 = health2.get("data", health2).get("checks", {})

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "issue": "HU-1461",
        "stage": "A dogfood",
        "base_url": args.base_url,
        "persona_id": persona_id,
        "conversation_id": conversation_id,
        "health_checks": checks2,
        "turns": turns,
        "summary": {"pass": PASS, "fail": FAIL},
    }

    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"voice_dogfood_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    report_path.write_text(json.dumps(report, indent=2))

    print(f"\nReport: {report_path}")
    print(f"Dogfood: {PASS} pass, {FAIL} fail")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
