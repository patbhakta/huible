#!/usr/bin/env python3
"""MONTHLY environment-gist refresh for Pat (Phoenix base).
Human-level gist only: season + rough temps. NOT a weather feed.
Time-of-day is computed live at conversation time (deterministic, never stored).
Exact temps/alerts = on-demand lookups only when conversationally relevant.
Silent on success; errors print + nonzero exit.
"""
import json, sqlite3, sys, urllib.request

TDAI, SERVICE = "http://127.0.0.1:8420", "default"
ATOM_ID, USER_ID = "env_context_pat", "185293546254362@lid"

def resolve_key():
    try:
        db = sqlite3.connect("/root/.memory-tencentdb/memory-tdai/metadata/tdai_metadata_default/metadata.db")
        row = db.execute("SELECT key_value FROM meta_user_keys LIMIT 1").fetchone()
        db.close()
        if row and row[0]: return row[0]
    except Exception:
        pass
    return "local"

MONTHS = ["January","February","March","April","May","June","July","August","September","October","November","December"]

def main():
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc)
    # one 7-day open-meteo pull to sanity-check the seasonal gist (not stored precisely)
    wx = json.loads(urllib.request.urlopen(urllib.request.Request(
        "https://api.open-meteo.com/v1/forecast?latitude=33.4484&longitude=-112.0740"
        "&daily=temperature_2m_max,temperature_2m_min&timezone=America%2FPhoenix&forecast_days=7"),
        timeout=15).read())
    hi_f = max(round(c * 9/5 + 32) for c in wx["daily"]["temperature_2m_max"])
    lo_f = min(round(c * 9/5 + 32) for c in wx["daily"]["temperature_2m_min"])
    month = MONTHS[now.month - 1]
    content = (f"Pat environment gist (monthly refresh, {month}): Phoenix AZ home base, UTC-7 no DST — "
               f"always compute current local time live, never guess time-of-day. "
               f"This week's ballpark: highs ~{hi_f}F / lows ~{lo_f}F. "
               f"Keep it human-level awareness (hot summer / mild winter / monsoon), NOT weather-station precision — "
               f"exact temps and alerts are fetched on demand only if conversationally relevant. "
               f"Location = Phoenix unless a newer conversation says otherwise.")
    body = {"team_id": "default", "agent_id": "default", "user_id": USER_ID, "id": ATOM_ID,
            "content": content,
            "background": "monthly env-gist refresh (cron env-context-refresh); source: open-meteo 7-day overview, condensed to gist"}
    req = urllib.request.Request(f"{TDAI}/v3/atomic/update", data=json.dumps(body).encode(),
        method="POST", headers={"Authorization": f"Bearer {resolve_key()}",
        "x-tdai-service-id": SERVICE, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        out = json.loads(r.read())
    if not (out.get("code") in (0, 1) or out.get("status")):
        print(f"unexpected: {json.dumps(out)[:200]}"); sys.exit(1)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"env-context-refresh failed: {e}"); sys.exit(1)
