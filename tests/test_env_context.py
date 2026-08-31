"""Tests for the per-client environment-context layer (HU-2194 productization).

Doctrine under test (Pat's REVISION 1, 2026-08-29):
- time-of-day is never stored — the gist carries a live-compute directive
- the gist is a monthly human-level season/temperature ballpark, nothing more
- exact temps/alerts are on-demand only; the gist says so
- location override comes only from newer conversation
- the atom payload is scoped to the client's user_id with a stable atom id
- the gist fits the recall length budget

No network: weather fetch, transport, and geocoder are injected fakes.
"""

from __future__ import annotations

import datetime
import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTRY = REPO_ROOT / "onboarding" / "env-context-clients.json"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "modules_onboarding_env_context",
        REPO_ROOT / "modules" / "onboarding" / "env_context.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _pat():
    return _load_module().Client(
        key="pat",
        user_id="185293546254362@lid",
        display_name="Pat",
        home_label="Phoenix AZ",
        latitude=33.4484,
        longitude=-112.074,
        timezone="America/Phoenix",
        tz_note="UTC-7 no DST",
        seasonal_note="hot summer / mild winter / monsoon",
    )


# -- registry -----------------------------------------------------------------


def test_registry_loads_and_derives_atom_ids():
    env_context = _load_module()
    clients = env_context.load_registry(REGISTRY)
    assert [c.key for c in clients] == ["pat"]
    pat = clients[0]
    assert pat.atom_id == "env_context_pat"
    assert pat.timezone == "America/Phoenix"
    assert pat.seasonal_note  # keeps the Phoenix-flavored gist wording


def test_registry_round_trip(tmp_path):
    env_context = _load_module()
    clients = env_context.load_registry(REGISTRY)
    out = tmp_path / "clients.json"
    env_context.save_registry(clients, out)
    again = env_context.load_registry(out)
    assert [c.atom_id for c in again] == [c.atom_id for c in clients]


# -- doctrine -------------------------------------------------------------------


def test_gist_carries_live_time_directive_not_stored_time():
    env_context = _load_module()
    gist = env_context.build_gist(_pat(), "August", 112, 77)
    assert "compute current local time live" in gist
    assert "never guess time-of-day" in gist
    # no stored clock time anywhere in the gist
    for token in ("am", "pm", "AM", "PM", "o'clock"):
        assert token not in gist


def test_gist_is_monthly_ballpoint_with_on_demand_note():
    env_context = _load_module()
    gist = env_context.build_gist(_pat(), "August", 112, 77)
    assert "monthly refresh, August" in gist
    assert "highs ~112F / lows ~77F" in gist
    assert "on demand only if conversationally relevant" in gist
    assert "NOT weather-station precision" in gist
    assert "Location = Phoenix AZ unless a newer conversation says otherwise" in gist


def test_gist_fits_recall_budget():
    env_context = _load_module()
    gist = env_context.build_gist(_pat(), "August", 112, 77)
    assert len(gist) <= env_context.GIST_MAX_CHARS


def test_gist_without_optional_notes_still_doctrinal():
    env_context = _load_module()
    bare = env_context.Client(
        key="t", user_id="u@lid", display_name="T",
        home_label="Testville", latitude=0.0, longitude=0.0,
        timezone="UTC",
    )
    gist = env_context.build_gist(bare, "January", 40, 20)
    assert "Testville home base —" in gist  # no dangling tz note
    assert "human-level awareness, NOT" in gist  # no dangling parenthetical


# -- atom payload -----------------------------------------------------------------


def test_atom_payload_scoped_to_client():
    env_context = _load_module()
    pat = _pat()
    payload = env_context.build_atom_payload(pat, "gist body")
    assert payload["id"] == "env_context_pat"
    assert payload["user_id"] == pat.user_id
    assert payload["content"] == "gist body"
    assert "env-context-clients registry" in payload["background"]


# -- refresh / push (injected fakes) -------------------------------------------------


class _FakeResponse:
    def __init__(self, body):
        self._body = json.dumps(body).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_refresh_dry_run_never_pushes():
    env_context = _load_module()
    pushed = []

    def fake_transport(req, timeout=None):
        pushed.append(req)
        return _FakeResponse({"code": 0})

    gist = env_context.refresh(
        _pat(), dry_run=True,
        weather_fetch=lambda c: (112, 77),
        transport=fake_transport,
        now=datetime.datetime(2026, 8, 31, tzinfo=datetime.timezone.utc),
    )
    assert "August" in gist
    assert pushed == []


def test_refresh_pushes_doctrinal_payload():
    env_context = _load_module()
    sent = {}

    def fake_transport(req, timeout=None):
        sent["url"] = req.full_url
        sent["body"] = json.loads(req.data.decode())
        sent["auth"] = req.headers.get("Authorization")
        return _FakeResponse({"code": 0})

    gist = env_context.refresh(
        _pat(),
        api_key="test-key",
        weather_fetch=lambda c: (112, 77),
        transport=fake_transport,
        now=datetime.datetime(2026, 8, 31, tzinfo=datetime.timezone.utc),
    )
    assert sent["url"].endswith("/v3/atomic/update")
    assert sent["auth"] == "Bearer test-key"
    assert sent["body"]["content"] == gist
    assert sent["body"]["id"] == "env_context_pat"


def test_push_rejects_unexpected_response():
    env_context = _load_module()
    import pytest

    def bad_transport(req, timeout=None):
        return _FakeResponse({"code": 500, "message": "boom"})

    with pytest.raises(RuntimeError):
        env_context.push_atom(
            {"id": "x", "content": "y"}, api_key="k", transport=bad_transport
        )


# -- intake derivation -----------------------------------------------------------


def test_client_from_bio_uses_geocoder():
    env_context = _load_module()

    def fake_geocoder(home_base):
        assert home_base == "Phoenix"
        return {
            "latitude": 33.4484, "longitude": -112.074,
            "timezone": "America/Phoenix", "home_label": "Phoenix",
        }

    client = env_context.client_from_bio(
        key="pat", display_name="Pat", home_base="Phoenix",
        user_id="185293546254362@lid", geocoder=fake_geocoder,
    )
    assert client.atom_id == "env_context_pat"
    assert client.timezone == "America/Phoenix"
    assert client.registered == datetime.date.today().isoformat()
