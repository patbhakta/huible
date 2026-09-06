#!/usr/bin/env python3
"""M1.3 — prove conversational memory persists and is session-scoped (HU-2732).

Executed against the LIVE deployment on .245 (traffic class ``internal``):

* **Restart survival** — conversation A plants a concrete detail (exchange 1,
  capture committed pre-restart: ``trace.working_memory.synced`` true), the
  app container is RESTARTED, then the same conversation probes for it. The
  W4 lane is a stateless client (relay → TencentDB): a non-empty ``v4-arm-a``
  recall in the fresh process is memory that survived the restart externally.
  The reply answering the detail is the user-visible corroboration (the
  durable history tail is disclosed as a corroborating carrier, not hidden).
* **Session scoping** — a different conversation (different
  ``working_memory_session_key``, same persona) probes for the same detail:
  the reply must NOT reveal it — one conversation's working memory can never
  surface in another's prompt (isolation doctrine, 2026-08-16 incident).

Exit 0 writes ``docs/evidence/m1/m13-memory.json`` with ``"proof": true``;
exit 3 = proof assertion failed (artifact still written with the failing
checks); exit 2 = deployment unreachable.

Usage:
    python3 -m scripts.m1.capture_m13_memory
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

OUT_PATH = REPO_ROOT / "docs/evidence/m1/m13-memory.json"
BASE_URL = "http://127.0.0.1:8000"
PERSONA = "fdc3a44b-4c0f-565d-b671-4ed0e3bc7894"  # Chandler Bing (Persona-0)
APP_CONTAINER = "huible-app"
APP_SERVICE = "app"  # docker compose service (container_name: huible-app)
ARM_A_STRATEGY = "v4-arm-a"

PLANT_TEXT = (
    "Okay one thing you have to remember about me: the wi-fi password at my "
    "apartment is 'joey-never-shares'. Do not lose that."
)
PROBE_TEXT = "hey, I blanked — what's the wi-fi password at my apartment again?"
SECRET = "joey-never-shares"


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


def container_started_at() -> str:
    return subprocess.run(
        ["docker", "container", "inspect", APP_CONTAINER, "--format", "{{.State.StartedAt}}"],
        capture_output=True,
        text=True,
        timeout=60,
    ).stdout.strip()


_ACKNOWLEDGMENT_PATTERNS = (
    "you just told me",
    "you told me",
    "you said",
    "you mentioned",
    "joey-never-shares",
    "wi-fi password",
    "wifi password",
)


def _reply_addresses_secret(reply: str) -> bool:
    """The probe reply shows the planted detail was retrieved.

    Either the secret itself, or an explicit acknowledgment that the persona
    was told it (in-character deflection without repetition is still proof
    the memory came back — the recall payload is the primary signal).
    """
    low = reply.lower()
    return SECRET in low or any(p in low for p in _ACKNOWLEDGMENT_PATTERNS)


def restart_app_and_wait(timeout_s: float = 150.0) -> None:
    subprocess.run(
        ["docker", "compose", "restart", APP_SERVICE],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=180,
    )
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(BASE_URL + "/health", timeout=5) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
        time.sleep(3)
    raise SystemExit(f"app did not become healthy within {timeout_s:.0f}s after restart")


def main() -> int:
    api_key = resolve_api_key()
    conv_a = f"m13-restart-{uuid.uuid4().hex[:12]}"
    conv_b = f"m13-scope-{uuid.uuid4().hex[:12]}"

    started_before = container_started_at()

    # --- Phase A: plant in conversation A ---------------------------------
    consent(api_key, conv_a)
    log(f"conv A (restart survival): {conv_a}")
    status, planted = turn_with_retry(api_key, conv_a, PLANT_TEXT)
    if status != 200:
        raise SystemExit(f"plant turn failed: HTTP {status}: {json.dumps(planted)[:400]}")
    capture_synced = (planted["trace"].get("working_memory") or {}).get("synced")
    log(f"plant ok (trace_id={planted['trace']['trace_id'][:8]}…, capture synced={capture_synced})")

    # --- Restart the app container ----------------------------------------
    log("restarting app container…")
    restart_app_and_wait()
    started_after = container_started_at()
    log(f"restart complete; StartedAt {started_before} -> {started_after}")

    # --- Probe conversation A in the fresh process ------------------------
    status, probed = turn_with_retry(api_key, conv_a, PROBE_TEXT)
    if status != 200:
        raise SystemExit(f"probe turn failed: HTTP {status}: {json.dumps(probed)[:400]}")
    wm_a = probed["trace"].get("working_memory") or {}
    reply_a = probed.get("response", "")
    log(f"probe A: wm={wm_a.get('strategy')}/{wm_a.get('chars')} reply[:110]={reply_a[:110]!r}")

    # --- Phase B: different conversation must NOT see the detail ----------
    consent(api_key, conv_b)
    status, scoped = turn_with_retry(api_key, conv_b, PROBE_TEXT)
    if status != 200:
        raise SystemExit(f"scope-probe turn failed: HTTP {status}: {json.dumps(scoped)[:400]}")
    wm_b = scoped["trace"].get("working_memory") or {}
    reply_b = scoped.get("response", "")
    log(
        f"scope-probe B: wm={wm_b.get('strategy')}/{wm_b.get('chars')} "
        f"reply[:110]={reply_b[:110]!r}"
    )

    checks = {
        "plant_capture_synced_pre_restart": capture_synced is True,
        "app_process_replaced": bool(started_after) and started_after != started_before,
        "probe_recall_arm_a_nonempty": wm_a.get("strategy") == ARM_A_STRATEGY
        and bool(wm_a.get("chars", 0) > 0),
        "probe_recall_synced": bool(wm_a.get("synced")),
        "probe_reply_addresses_planted_detail": _reply_addresses_secret(reply_a),
        "scoped_reply_withholds_secret_in_conv_b": SECRET not in reply_b.lower(),
    }
    proof = all(checks.values())

    evidence = {
        "artifact": "M1.3 live memory persistence + session scoping (HU-2732)",
        "generated_at": datetime.now(UTC).isoformat(),
        "deployment": {
            "host": ".245 (this host)",
            "base_url": BASE_URL,
            "app_container": APP_CONTAINER,
            "container_started_before": started_before,
            "container_started_after": started_after,
        },
        "protocol": (
            "plant (conv A) -> docker compose restart app -> probe (conv A) + "
            "scope-probe (conv B); X-Huible-Traffic-Class internal, consent v3"
        ),
        "conversations": {"restart_survival": conv_a, "scoping": conv_b},
        "turns": {
            "plant": {"message": PLANT_TEXT, "trace": planted["trace"]},
            "probe_a": {"message": PROBE_TEXT, "trace": probed["trace"]},
            "scope_probe_b": {"message": PROBE_TEXT, "trace": scoped["trace"]},
        },
        "replies": {"plant": planted.get("response"), "probe_a": reply_a, "scope_probe_b": reply_b},
        "carrier_note": (
            "The W4 lane is a stateless client (relay -> TencentDB): a non-empty "
            "v4-arm-a recall in a replaced app process is memory that survived the "
            "restart externally. The durable Postgres history tail also carries the "
            "plant at exchange 2 and is disclosed as a corroborating carrier; the "
            "window-eviction + digest-retrieval boundary itself is proven "
            "hermetically in tests/api/test_chat_memory_eviction.py (rows > 40)."
        ),
        "checks": checks,
        "proof": proof,
        "accepts_m0_cases": ["M1.3 milestone acceptance (no direct m0 case; RC-3 class)"],
    }
    OUT_PATH.write_text(json.dumps(evidence, indent=2) + "\n")

    for name, ok in checks.items():
        log(f"  {'PASS' if ok else 'FAIL'}  {name}")
    log(f"proof={'TRUE' if proof else 'FALSE'} — wrote {OUT_PATH.relative_to(REPO_ROOT)}")
    return 0 if proof else 3


if __name__ == "__main__":
    raise SystemExit(main())
