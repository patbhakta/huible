#!/usr/bin/env python3
"""
Huible Onboarding — Environment-context layer, productized per client (HU-2194).

Implements Pat's directive (2026-08-28): environment context (time / weather /
area awareness) is an ONBOARDING BASIC for every HUible client — a persona
without it makes onboarding feel dumb and untrustworthy.

Doctrine (REVISION 1, Pat's correction 2026-08-29 — the HUible memory thesis:
imperfect gist, perfect-on-demand detail):

* Durable facts (home base, timezone, travel caveat) live in the client's
  Obsidian bio note — never here.
* This module owns the **ephemeral TencentDB atom**: one short monthly
  environment gist per client (season + ballpark temperatures), atom id
  ``env_context_<key>``, scoped to the client's user_id.
* Time-of-day is NEVER stored or refreshed — it is computed live at
  conversation time from the timezone in the gist directive.
* Exact temperatures and weather alerts are fetched on demand only when
  conversationally relevant, never pushed.

Registry: ``onboarding/env-context-clients.json`` — one entry per onboarded
client. The intake path derives an entry from the bio questionnaire home base
via ``client_from_bio`` (open-meteo geocoding); the monthly cron refreshes all
registered clients through ``scripts/env_context_refresh.py``.

Usage (library):
  from modules_onboarding_env_context import load_registry, refresh

Usage (CLI, cron-compatible — silent on success):
  python3 scripts/env_context_refresh.py --all
  python3 scripts/env_context_refresh.py --client pat --dry-run
"""

from __future__ import annotations

import datetime
import json
import os
import sqlite3
import urllib.request
from dataclasses import dataclass, asdict
from pathlib import Path

DEFAULT_BASE_URL = "http://127.0.0.1:8420"
DEFAULT_SERVICE_ID = "default"

# Gist length budget: keeps the atom short enough to stay top-3 recallable in
# v3/atomic/search (the live env_context_pat atom is ~430 chars).
GIST_MAX_CHARS = 600

MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


@dataclass
class Client:
    """One registered client of the environment-context layer."""

    key: str                      # registry key + atom suffix (env_context_<key>)
    user_id: str                  # TencentDB scope, e.g. "185293546254362@lid"
    display_name: str             # how the gist addresses the client ("Pat")
    home_label: str               # human label, e.g. "Phoenix AZ"
    latitude: float
    longitude: float
    timezone: str                 # IANA, e.g. "America/Phoenix"
    tz_note: str = ""             # short directive note, e.g. "UTC-7 no DST"
    seasonal_note: str = ""       # parenthetical gist style, e.g. "hot summer / mild winter / monsoon"
    registered: str = ""          # ISO date the client entered the registry

    @property
    def atom_id(self) -> str:
        return f"env_context_{self.key}"


# -- registry ----------------------------------------------------------------


def load_registry(path: str | Path) -> list[Client]:
    """Load ``env-context-clients.json`` into Client entries."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    entries = raw["clients"] if isinstance(raw, dict) else raw
    clients = []
    for e in entries:
        client = Client(
            key=e["key"],
            user_id=e["user_id"],
            display_name=e["display_name"],
            home_label=e["home_label"],
            latitude=float(e["latitude"]),
            longitude=float(e["longitude"]),
            timezone=e["timezone"],
            tz_note=e.get("tz_note", ""),
            seasonal_note=e.get("seasonal_note", ""),
            registered=e.get("registered", ""),
        )
        if e.get("atom_id") and e["atom_id"] != client.atom_id:
            raise ValueError(
                f"client {client.key}: atom_id {e['atom_id']} != derived {client.atom_id}"
            )
        clients.append(client)
    return clients


def save_registry(clients: list[Client], path: str | Path) -> None:
    """Write the registry back (used by the intake path)."""
    Path(path).write_text(
        json.dumps(
            {"clients": [asdict(c) for c in clients]},
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


# -- intake derivation ---------------------------------------------------------


def geocode(home_base: str, opener=None, timeout: int = 15) -> dict:
    """Resolve a home-base label to lat/lon/timezone via open-meteo geocoding."""
    url = (
        "https://geocoding-api.open-meteo.com/v1/search?count=1&name="
        + urllib.request.quote(home_base)
    )
    open_ = opener or urllib.request.urlopen
    with open_(urllib.request.Request(url), timeout=timeout) as r:
        out = json.loads(r.read())
    results = out.get("results") or []
    if not results:
        raise ValueError(f"geocode: no result for {home_base!r}")
    hit = results[0]
    return {
        "latitude": hit["latitude"],
        "longitude": hit["longitude"],
        "timezone": hit.get("timezone", "UTC"),
        "home_label": hit.get("name", home_base),
    }


def client_from_bio(
    key: str,
    display_name: str,
    home_base: str,
    user_id: str,
    geocoder=None,
) -> Client:
    """Derive a registry entry from the bio questionnaire home base."""
    geo = (geocoder or geocode)(home_base)
    return Client(
        key=key,
        user_id=user_id,
        display_name=display_name,
        home_label=geo["home_label"],
        latitude=geo["latitude"],
        longitude=geo["longitude"],
        timezone=geo["timezone"],
        registered=datetime.date.today().isoformat(),
    )


# -- monthly gist --------------------------------------------------------------


def fetch_weather_overview(
    latitude: float, longitude: float, timezone: str, opener=None, timeout: int = 15
) -> tuple[int, int]:
    """7-day hi/lo in °F — a sanity check for the seasonal gist, nothing more."""
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={latitude}&longitude={longitude}"
        "&daily=temperature_2m_max,temperature_2m_min"
        f"&timezone={urllib.request.quote(timezone)}&forecast_days=7"
    )
    open_ = opener or urllib.request.urlopen
    with open_(urllib.request.Request(url), timeout=timeout) as r:
        wx = json.loads(r.read())
    hi_f = max(round(c * 9 / 5 + 32) for c in wx["daily"]["temperature_2m_max"])
    lo_f = min(round(c * 9 / 5 + 32) for c in wx["daily"]["temperature_2m_min"])
    return hi_f, lo_f


def build_gist(client: Client, month: str, hi_f: int, lo_f: int) -> str:
    """Build the doctrine-correct monthly environment gist for one client.

    Doctrine locks (see module docstring): live time directive (never stored),
    human-level gist, on-demand-only exact detail, location-override note.
    """
    tz_clause = f", {client.tz_note}" if client.tz_note else ""
    seasonal = f" ({client.seasonal_note})" if client.seasonal_note else ""
    gist = (
        f"{client.display_name} environment gist (monthly refresh, {month}): "
        f"{client.home_label} home base{tz_clause} — "
        f"always compute current local time live, never guess time-of-day. "
        f"This week's ballpark: highs ~{hi_f}F / lows ~{lo_f}F. "
        f"Keep it human-level awareness{seasonal}, NOT weather-station precision — "
        f"exact temps and alerts are fetched on demand only if conversationally "
        f"relevant. Location = {client.home_label} unless a newer conversation "
        f"says otherwise."
    )
    if len(gist) > GIST_MAX_CHARS:
        raise ValueError(
            f"gist for {client.key} is {len(gist)} chars (budget {GIST_MAX_CHARS}) "
            "— shorten seasonal_note/tz_note to keep the atom recallable"
        )
    return gist


def build_atom_payload(client: Client, gist: str, background: str | None = None) -> dict:
    """v3/atomic/update body for the client's env-context atom."""
    return {
        "team_id": DEFAULT_SERVICE_ID,
        "agent_id": DEFAULT_SERVICE_ID,
        "user_id": client.user_id,
        "id": client.atom_id,
        "content": gist,
        "background": background
        or (
            "monthly env-gist refresh (env-context-clients registry, "
            f"key={client.key}); source: open-meteo 7-day overview, condensed to gist"
        ),
    }


# -- push ----------------------------------------------------------------------


def resolve_api_key(explicit: str | None = None) -> str:
    """TDAI key: explicit arg > env TDAI_API_KEY > local metadata DB > 'local'."""
    if explicit:
        return explicit
    env = os.environ.get("TDAI_API_KEY")
    if env:
        return env
    try:
        db = sqlite3.connect(
            "/root/.memory-tencentdb/memory-tdai/metadata/"
            "tdai_metadata_default/metadata.db"
        )
        row = db.execute("SELECT key_value FROM meta_user_keys LIMIT 1").fetchone()
        db.close()
        if row and row[0]:
            return row[0]
    except Exception:
        pass
    return "local"


def push_atom(
    payload: dict,
    api_key: str | None = None,
    base_url: str = DEFAULT_BASE_URL,
    service_id: str = DEFAULT_SERVICE_ID,
    transport=None,
    timeout: int = 20,
) -> dict:
    """Upsert the atom via the TencentDB v3/atomic/update endpoint."""
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{base_url}/v3/atomic/update",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key or resolve_api_key()}",
            "x-tdai-service-id": service_id,
            "Content-Type": "application/json",
        },
    )
    send = transport or urllib.request.urlopen
    with send(req, timeout=timeout) as r:
        out = json.loads(r.read())
    if not (out.get("code") in (0, 1) or out.get("status")):
        raise RuntimeError(f"atomic/update unexpected response: {json.dumps(out)[:200]}")
    return out


# -- full refresh ---------------------------------------------------------------


def refresh(
    client: Client,
    api_key: str | None = None,
    dry_run: bool = False,
    base_url: str = DEFAULT_BASE_URL,
    weather_fetch=None,
    transport=None,
    now: datetime.datetime | None = None,
) -> str:
    """Refresh one client's env-context atom; returns the gist.

    ``weather_fetch``/``transport`` are injectable for tests (no network).
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    month = MONTHS[now.month - 1]
    fetch = weather_fetch or (
        lambda c: fetch_weather_overview(c.latitude, c.longitude, c.timezone)
    )
    hi_f, lo_f = fetch(client)
    gist = build_gist(client, month, hi_f, lo_f)
    if not dry_run:
        push_atom(
            build_atom_payload(client, gist),
            api_key=api_key,
            base_url=base_url,
            transport=transport,
        )
    return gist
