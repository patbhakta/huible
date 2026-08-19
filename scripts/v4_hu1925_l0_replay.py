#!/usr/bin/env python3
"""HU-1925 — replay real hermes history into gateway L0 via /v3/conversation/add.

The hermes mirror (memory_tencentdb.sync_turn) only syncs a turn when the
assistant reply lands in the normal turn flow: turns that failed on z.ai
rate limits (Aug 18-19 incident) and roster-sent proactive replies are
skipped by design (#15218) and never retried. That left the two active
WhatsApp sessions far below the 40-L0-row gist-settle threshold:
  20260818_062408_4a0e0c61  105 hermes turns -> 14 L0 rows
  20260819_053442_23781ad3   38 hermes turns ->  2 L0 rows

This script replays the real (role, content, timestamp) history through the
same production API path, so:
  - 4a0e0c61 crosses its 40/80-row block boundaries -> gists become possible
  - 23781ad3 lands just under 40 so the NEXT real completed exchange settles
    block 0 naturally (write-path dogfood evidence on a live session)

Safety: dry-run by default; --apply posts. Skips messages already in L0
(role + 96-char normalized prefix match). Content truncated to 8000 chars
(same limit as the mirror). Reversible: L0 rows are deletable by session_key.
"""
import argparse
import json
import re
import sqlite3
import sys
import time
import urllib.request

HERMES_DB = "/root/.hermes/state.db"
VDB = "/root/.memory-tencentdb/memory-tdai/vectors.db"
GATEWAY = "http://127.0.0.1:8420"
GW_CONFIG = "/opt/tencentdb-memory/.config/tdai-gateway.yaml"
MAX_CONTENT = 8000
PREFIX_LEN = 96
BATCH = 6          # messages per add call
PAUSE_SECS = 2.0   # between calls (server does per-message embedding + L1)


def gateway_api_key():
    txt = open(GW_CONFIG).read()
    m = re.search(r'apiKey:\s*"([^"]+)"', txt)
    return m.group(1) if m else None


def norm(s):
    return re.sub(r"\s+", " ", s or "").strip()[:PREFIX_LEN]


def hermes_turns(session):
    db = sqlite3.connect(f"file:{HERMES_DB}?mode=ro", uri=True)
    rows = db.execute(
        "select role, timestamp, content from messages "
        "where session_id = ? and role in ('user','assistant') "
        "and content is not null and trim(content) != '' "
        "order by timestamp, rowid",
        (session,),
    ).fetchall()
    db.close()
    return [(r, ts, c) for r, ts, c in rows]


def existing_l0(session):
    db = sqlite3.connect(f"file:{VDB}?mode=ro", uri=True)
    rows = db.execute(
        "select role, message_text from l0_conversations where session_key = ?",
        (session,),
    ).fetchall()
    uid = db.execute(
        "select user_id from l0_conversations where session_key = ? limit 1",
        (session,),
    ).fetchone()
    db.close()
    seen = {(r, norm(t)) for r, t in rows}
    return seen, (uid[0] if uid else "default")


def pair_turns(msgs):
    """Keep hermes fidelity: adjacent user->assistant stay a pair; orphan
    users (failed turns) are sent as single user messages; an assistant
    following several orphan users pairs with the latest one."""
    out, pending_user = [], None
    for m in msgs:
        role, ts, content = m
        if role == "user":
            if pending_user is not None:
                out.append(("single", pending_user))
            pending_user = m
        else:  # assistant
            if pending_user is not None:
                out.append(("pair", (pending_user, m)))
                pending_user = None
            else:
                out.append(("single", m))
    if pending_user is not None:
        out.append(("single", pending_user))
    return out


def iso(ts):
    # hermes timestamps are unixepoch seconds (sometimes with ms precision)
    ts = float(ts)
    if ts > 1e12:
        ts /= 1000.0
    whole = int(ts)
    ms = int(round((ts - whole) * 1000))
    t = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(whole))
    return f"{t}.{ms:03d}Z"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", action="append", required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    key = gateway_api_key()
    hdrs = {"Content-Type": "application/json", "x-tdai-service-id": "default"}
    if key:
        hdrs["Authorization"] = f"Bearer {key}"

    total_new = 0
    for session in args.session:
        turns = hermes_turns(session)
        seen, user_id = existing_l0(session)
        pairs = pair_turns(turns)
        todo = []
        for kind, payload in pairs:
            msgs = payload if kind == "pair" else (payload,)
            fresh = [
                (r, ts, c)
                for r, ts, c in msgs
                if (r, norm(c)) not in seen
            ]
            if fresh:
                todo.append((kind, fresh))
        n_new = sum(len(f) for _, f in todo)
        n_skip = len(turns) - n_new
        print(f"[{session}] hermes={len(turns)} already-in-L0~{n_skip} to-replay={n_new} "
              f"(pairs={sum(1 for k,_ in todo if k=='pair')} singles={sum(1 for k,_ in todo if k=='single')})")
        if not args.apply:
            for kind, fresh in todo[:3]:
                for r, ts, c in fresh:
                    print(f"   would add {iso(ts)} {r}: {norm(c)[:70]}")
            continue

        sent = 0
        for kind, fresh in todo:
            body = {
                "team_id": "default",
                "agent_id": "default",
                "user_id": user_id,
                "session_id": session,
                "messages": [
                    {"role": r, "content": c[:MAX_CONTENT], "timestamp": iso(ts)}
                    for r, ts, c in fresh
                ],
            }
            req = urllib.request.Request(
                f"{GATEWAY}/v3/conversation/add",
                data=json.dumps(body).encode(),
                headers=hdrs,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = json.loads(resp.read().decode())
            if raw.get("code", 0) != 0:
                print(f"  !! gateway code={raw.get('code')} msg={raw.get('message')}")
                sys.exit(1)
            sent += len(fresh)
            print(f"   sent {kind} ({len(fresh)} msg, {iso(fresh[-1][1])}) -> total {sent}/{n_new}")
            time.sleep(PAUSE_SECS)
        total_new += sent
    print(f"done: replayed {total_new} messages" + ("" if args.apply else " (DRY RUN)"))


if __name__ == "__main__":
    main()
