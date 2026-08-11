#!/usr/bin/env python3
"""
verify_tencentdb_memory.py — Independent verification of TencentDB Memory health.

Confirms each layer (L0/L1/L2/L3) actually returns data when queried with the
correct isolation headers. Use this to refute false "memory is broken" alarms
that come from testing v3 endpoints without team/agent/user headers.

Run:
    python scripts/verify_tencentdb_memory.py
    python scripts/verify_tencentdb_memory.py --gateway http://127.0.0.1:8420
    python scripts/verify_tencentdb_memory.py \\
        --team default --agent default --user 185293546254362@lid

Exit codes:
    0  — all layers returning data
    1  — at least one layer empty (real bug)
    2  — gateway/proxy unreachable
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime

DEFAULT_GATEWAY = "http://127.0.0.1:8420"
DEFAULT_SERVICE_ID = "default"
DEFAULT_TEAM = "default"
DEFAULT_AGENT = "default"
DEFAULT_USER = "185293546254362@lid"
DEFAULT_API_KEY = "local"
HTTP_TIMEOUT_SEC = 5


def post_json(url: str, body: dict, headers: dict) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    for k, v in headers.items():
        req.add_header(k, v)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SEC) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except Exception:
            payload = {"raw": str(exc)}
        return exc.code, payload
    except urllib.error.URLError as exc:
        print(f"[unreachable] {url}: {exc}", file=sys.stderr)
        sys.exit(2)


def check_health(gateway: str) -> dict:
    url = f"{gateway}/health"
    try:
        with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT_SEC) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        print(f"[unreachable] {url}: {exc}", file=sys.stderr)
        sys.exit(2)


def query_layer(gateway: str, path: str, body: dict, headers: dict, label: str) -> int:
    """Return number of entries returned for the layer."""
    status, payload = post_json(f"{gateway}{path}", body, headers)
    if status != 200:
        print(f"  [{label}] HTTP {status}: {payload}")
        return -1
    data = payload.get("data", {})
    # layer shapes vary: messages (L0), items (L1), entries (L2), or direct
    if "messages" in data:
        count = len(data["messages"])
    elif "items" in data:
        count = len(data["items"])
    elif "entries" in data:
        count = len(data["entries"])
    elif "total" in data:
        count = int(data["total"])
    else:
        count = len(data)
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway", default=DEFAULT_GATEWAY)
    parser.add_argument("--service-id", default=DEFAULT_SERVICE_ID)
    parser.add_argument("--team", default=DEFAULT_TEAM)
    parser.add_argument("--agent", default=DEFAULT_AGENT)
    parser.add_argument("--user", default=DEFAULT_USER)
    parser.add_argument("--api-key", default=DEFAULT_API_KEY)
    args = parser.parse_args()

    print(f"=== TencentDB Memory verification @ {datetime.now(UTC).isoformat()} ===")
    print(f"Gateway: {args.gateway}")
    print(f"Isolation: team={args.team} agent={args.agent} user={args.user}")
    print()

    health = check_health(args.gateway)
    print(f"[health] status={health.get('status')} uptime={health.get('uptime')}s")
    services = health.get("services", {})
    ts = services.get("timerScanner", {})
    pw = services.get("pipelineWorker", {})
    print(
        f"[health] timerScanner.scans={ts.get('scansCompleted')} "
        f"enqueued={ts.get('tasksEnqueued')} (0 is NORMAL for local backend)"
    )
    print(
        f"[health] pipelineWorker.consumed={pw.get('tasksConsumed')} "
        f"completed={pw.get('tasksCompleted')} failed={pw.get('tasksFailed')}"
    )
    print()

    isolation_headers = {
        "Authorization": f"Bearer {args.api_key}",
        "x-tdai-service-id": args.service_id,
        "x-tdai-team-id": args.team,
        "x-tdai-agent-id": args.agent,
        "x-tdai-user-id": args.user,
    }

    # Negative control: query WITHOUT isolation headers. This MUST return 422
    # if V3_STRICT_ISOLATION is enabled. If it returns 200 with empty data, the
    # gateway is in lax mode and false-empty queries are possible.
    print("[negative control] v3/conversation/query with NO isolation headers")
    no_iso_headers = {
        "Authorization": f"Bearer {args.api_key}",
        "x-tdai-service-id": args.service_id,
    }
    status, payload = post_json(
        f"{args.gateway}/v3/conversation/query", {"limit": 1}, no_iso_headers
    )
    if status == 200:
        empty_total = payload.get("data", {}).get("total", 0)
        print(
            f"  HTTP {status} total={empty_total}  "
            "BUG: V3_STRICT_ISOLATION is off; missing headers silently returns empty"
        )
    elif status == 422:
        print(f"  HTTP {status}  (strict isolation on — good)")
    else:
        print(f"  HTTP {status} {payload}")
    print()

    # Positive checks: each layer with proper isolation.
    layers = [
        ("L0 conversations", "/v3/conversation/query", {"limit": 5, "order": "desc"}),
        ("L1 atomic records", "/v3/atomic/query", {"limit": 5}),
        ("L2 scene blocks", "/v3/scenario/ls", {}),
        ("L3 persona", "/v3/core/read", {}),
    ]
    failures: list[str] = []
    for label, path, body in layers:
        count = query_layer(args.gateway, path, body, isolation_headers, label)
        if count < 0:
            failures.append(label)
            print(f"  [{label}] REQUEST FAILED")
        elif count == 0:
            failures.append(label)
            print(f"  [{label}] {count} entries  <-- EMPTY (real bug if reproducible)")
        else:
            print(f"  [{label}] {count} entries OK")
    print()

    if failures:
        print(f"FAIL: {len(failures)} layer(s) empty: {', '.join(failures)}")
        return 1
    print("PASS: all layers returning data")
    return 0


if __name__ == "__main__":
    sys.exit(main())
