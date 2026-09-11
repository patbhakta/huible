#!/usr/bin/env python3
"""HU-2828 live probe: 20+ turn stranger conversation via the portal (:8777).

Probes the three done-when classes:
  - time-of-day (correct local time context, Chandler = NYC),
  - real-world lookup (rent where he lives -> live SearXNG-backed answer),
  - out-of-world trivia (the Mayans -> honest ignorance preserved).

Runs against the same surface a stranger uses: portal session -> consent ->
sequential /api/chat turns. Transcript lands in runs/hu2828/transcript.json.
"""
import json
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

PORTAL = "http://127.0.0.1:8777"
PORTAL_KEY = "chandler-portal-7f3k9"

TURNS = [
    "Hey, is this thing on?",
    "I just moved to the city and I don't know anyone yet.",
    "So what do you do around here all day?",
    "What time is it right now?",
    "Is it that late already? Feels earlier.",
    "Do you have plans tonight?",
    "How much is rent where you live?",
    "Seriously? I've been looking at places and crying at the prices.",
    "Is your neighborhood at least nice?",
    "What's the weather like right now?",
    "Okay, completely random question: tell me about the Mayans.",
    "Like the pyramids and the calendar and all that?",
    "You really don't know anything about that?",
    "Fair enough. What do YOU actually know about?",
    "What's your favorite thing about living where you do?",
    "Do you get decent pizza around there?",
    "Did you catch the game last night? Who won the game?",
    "I'm more of a baseball guy myself.",
    "Any good coffee places near you?",
    "What time is it there now?",
    "This has been oddly comforting. Thanks.",
    "Anything you want to ask me before I disappear forever?",
    "Ha. Good enough. Take care, Chandler.",
]


def call(path: str, payload: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        PORTAL + path,
        method="POST",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "X-Portal-Key": PORTAL_KEY},
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {}


def main() -> None:
    status, sess = call("/api/session", {})
    assert status == 200, (status, sess)
    sid = sess["sid"]
    status, ack = call("/api/consent", {"sid": sid})
    assert status == 200, (status, ack)

    transcript = {
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "sid": sid,
        "turns": [],
    }
    for i, msg in enumerate(TURNS, 1):
        t0 = time.time()
        status, body = call("/api/chat", {"sid": sid, "message": msg})
        dt = time.time() - t0
        reply = body.get("reply") or json.dumps(body)
        trace = body.get("trace") or {}
        transcript["turns"].append(
            {
                "n": i,
                "user": msg,
                "reply": reply,
                "status": status,
                "s": round(dt, 1),
                # HU-2828 r6 evidence: gate firings + what memory lines the
                # model actually saw on each turn.
                "exclusion_counts": trace.get("exclusion_counts"),
                "wall": trace.get("competence_wall"),
                "activated": [
                    (m.get("content") or "")[:110]
                    for m in (trace.get("activated_memories") or [])[:5]
                ],
            }
        )
        print(f"[{i:02d}] ({status} {dt:4.1f}s) {msg}\n     -> {reply[:180]}")
        time.sleep(1.0)

    transcript["ended_utc"] = datetime.now(timezone.utc).isoformat()
    out = "/root/repos/huible/runs/hu2828/transcript.json"
    with open(out, "w") as f:
        json.dump(transcript, f, indent=2)
    print(f"\nwrote {out} ({len(transcript['turns'])} turns)")


if __name__ == "__main__":
    main()
