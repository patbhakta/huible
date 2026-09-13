#!/usr/bin/env python3
"""HU-2774 battery preflight: verify the zai lane actually serves before firing.

The provider enforces a rolling 5-hour usage window (HTTP 429, code 1308)
separate from the app-side daily token ledger; a battery launched into a
closed window burns ~200s of retries per turn and dies mid-slot (r13
stranger-3, 2026-09-13 01:34Z). This probe spends one cheap real turn to
prove the window is open before the 6-slot battery commits.

Exit 0 = lane serving, battery may fire. Non-zero = abort (wall still up,
engine down, or auth broken). The probe conversation uses the hu2774- id
prefix so the battery's per-slot reset purges it.
"""
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ENGINE = "http://127.0.0.1:8000"
ENV_FILE = Path("/root/repos/huible/.env")
PROBE_CONV = "hu2774-preflight"
PROBE_MSG = "preflight ping — reply with just: ok"


def first_persona_key():
    text = ENV_FILE.read_text()
    m = re.search(r"^API_KEYS=(.*)$", text, re.M)
    if not m:
        sys.exit("preflight: no API_KEYS in .env")
    for part in m.group(1).split(","):
        key, _, pid = part.strip().partition(":")
        if key and pid:
            return key, pid
    sys.exit("preflight: API_KEYS empty")


def probe_turn(key, pid):
    body = json.dumps({"message": PROBE_MSG, "relationship": "close_friend",
                       "conversation_id": PROBE_CONV}).encode()
    req = urllib.request.Request(
        ENGINE + f"/api/v1/chat/{pid}", data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}",
                 "X-Huible-Traffic-Class": "internal",
                 "X-Huible-Client": "battery-flow"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {}
    except Exception as e:
        return None, {"error": str(e)}


def main():
    try:
        with urllib.request.urlopen(ENGINE + "/health", timeout=10) as r:
            health = json.loads(r.read())
        if health.get("data", {}).get("status") != "ok":
            sys.exit(f"preflight: engine health not ok: {health}")
    except Exception as e:
        sys.exit(f"preflight: engine unreachable: {e}")

    key, pid = first_persona_key()
    status, data = probe_turn(key, pid)
    detail = json.dumps(data)[:300]
    if status == 200 and (data.get("response") or "").strip():
        print(f"preflight: lane serving (persona={pid} turn ok) — window open")
        return
    if status == 503:
        sys.exit(f"preflight: engine 503 (window walled or transient): {detail}")
    sys.exit(f"preflight: probe failed status={status}: {detail}")


if __name__ == "__main__":
    main()
