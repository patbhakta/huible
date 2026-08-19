"""Regression gate for ``scripts/telemetry_window.py`` (HU-1945 runbook reader).

The daily-review runbook greps the durable telemetry log over a trailing 24h
window via this helper; a silent breakage would blank the consent /
chat.trace / handoff.page surfaces at the next daily review. No network, no
secrets.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "telemetry_window.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("_telemetry_window", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _line(message: str, *, age_hours: float) -> str:
    stamp = datetime.now(UTC) - timedelta(hours=age_hours)
    return json.dumps(
        {
            "ts": stamp.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "level": "INFO",
            "logger": "huible.test",
            "message": message,
        }
    )


class TestTelemetryWindow:
    def test_keeps_trailing_window_and_drops_old_lines(self, tmp_path):
        mod = _load_module()
        log = tmp_path / "telemetry.log"
        log.write_text(
            "\n".join(
                [
                    _line("chat.trace session=old turn=0", age_hours=30),
                    _line("chat.trace session=new turn=1", age_hours=2),
                    _line("consent.record session=new card_version=3", age_hours=23),
                    _line("consent.record session=old card_version=1", age_hours=48),
                    "not json at all",
                ]
            )
            + "\n"
        )
        kept = mod.window_lines(log, timedelta(hours=24))
        messages = [json.loads(line)["message"] for line in kept]
        assert messages == [
            "chat.trace session=new turn=1",
            "consent.record session=new card_version=3",
        ]

    def test_missing_file_is_quiet_not_an_error(self, tmp_path):
        mod = _load_module()
        assert mod.window_lines(tmp_path / "absent.log", timedelta(hours=24)) == []

    def test_main_prints_window_lines(self, tmp_path, capsys):
        mod = _load_module()
        log = tmp_path / "telemetry.log"
        log.write_text(_line("handoff.page ticket=t severity=sev-1", age_hours=1) + "\n")
        rc = mod.main(["--file", str(log), "--since", "24h"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "handoff.page" in out

    def test_invalid_since_rejects(self):
        mod = _load_module()
        with pytest.raises(SystemExit):
            mod._parse_since("24x")
