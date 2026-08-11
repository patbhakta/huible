"""Tests for the grounded-truth SLM tools (date/time/location/calculator)."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS_PKG = "scripts.tools"


def _run(module: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", f"{TOOLS_PKG}.{module}", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )


def _parse_stdout(proc: subprocess.CompletedProcess[str]) -> dict:
    assert proc.returncode == 0, f"process failed: stderr={proc.stderr}"
    return json.loads(proc.stdout)


def test_get_date_default_utc() -> None:
    payload = _parse_stdout(_run("get_date"))
    today_utc = datetime.now(UTC).date().isoformat()
    assert payload["date"] == today_utc
    assert payload["timezone"] == "UTC"
    assert payload["iso"] == today_utc


def test_get_date_chicago_format() -> None:
    payload = _parse_stdout(
        _run("get_date", "--timezone", "America/Chicago", "--format", "%Y/%m/%d")
    )
    today_chicago = datetime.now(ZoneInfo("America/Chicago")).strftime("%Y/%m/%d")
    assert payload["date"] == today_chicago
    assert payload["timezone"] == "America/Chicago"


def test_get_date_bad_timezone_errors() -> None:
    proc = _run("get_date", "--timezone", "Not/A/Zone")
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert "error" in payload


def test_get_time_default_utc() -> None:
    payload = _parse_stdout(_run("get_time"))
    assert payload["timezone"] == "UTC"
    assert payload["utc_offset"] == "+00:00"
    # ISO should be parseable
    datetime.fromisoformat(payload["iso"])


def test_get_time_chicago_offset() -> None:
    payload = _parse_stdout(_run("get_time", "--timezone", "America/Chicago"))
    assert payload["timezone"] == "America/Chicago"
    # Chicago is either -05:00 (CST) or -06:00 (CDT). Both accepted.
    assert payload["utc_offset"] in {"-05:00", "-06:00"}


def test_get_location_default_fallback() -> None:
    payload = _parse_stdout(_run("get_location"))
    assert payload["source"] == "fallback"
    assert payload["city"] == "Bartlett, IL"
    assert payload["timezone"] == "America/Chicago"
    assert isinstance(payload["lat"], float)
    assert isinstance(payload["lon"], float)


def test_get_location_override_wins() -> None:
    payload = _parse_stdout(
        _run("get_location", "--lat", "40.71", "--lon", "-74.01", "--default-city", "New York, NY")
    )
    assert payload["source"] == "override"
    assert payload["city"] == "New York, NY"
    assert payload["lat"] == pytest.approx(40.71)
    assert payload["lon"] == pytest.approx(-74.01)
    # NY longitude -> Eastern time band
    assert payload["timezone"] == "America/New_York"


def test_calculator_basic_precedence() -> None:
    payload = _parse_stdout(_run("calculator", "--expr", "2 + 3 * 4"))
    assert payload["result"] == 14
    assert payload["type"] == "int"


def test_calculator_float_division() -> None:
    payload = _parse_stdout(_run("calculator", "--expr", "10 / 4"))
    assert payload["result"] == 2.5
    assert payload["type"] == "float"


def test_calculator_power_and_constant() -> None:
    payload = _parse_stdout(_run("calculator", "--expr", "pi ** 2"))
    assert payload["result"] == pytest.approx(9.869604401089358, rel=1e-6)
    assert payload["type"] == "float"


def test_calculator_rejects_call() -> None:
    proc = _run("calculator", "--expr", "__import__('os').system('echo p0wned')")
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert "error" in payload


def test_calculator_rejects_attribute_access() -> None:
    proc = _run("calculator", "--expr", "(1).__class__")
    assert proc.returncode == 1
    assert "error" in json.loads(proc.stdout)


def test_calculator_rejects_unknown_name() -> None:
    proc = _run("calculator", "--expr", "sin(0)")
    assert proc.returncode == 1
    assert "error" in json.loads(proc.stdout)
