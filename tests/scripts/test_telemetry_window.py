"""Regression gate for ``scripts/telemetry_window.py`` (HU-1945 runbook reader).

The daily-review runbook greps the durable telemetry log over a trailing 24h
window via this helper; a silent breakage would blank the consent /
chat.trace / handoff.page surfaces at the next daily review. No network, no
secrets.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
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

    def test_parses_real_formatter_output(self):
        """Lock parser compatibility with the app's actual ``ts`` shape.

        ``_JsonLineFormatter`` stamps lines via ``logging.formatTime`` where
        ``%f`` is emitted literally (no microseconds) — the reader must accept
        exactly what the deployed formatter writes, not an idealized stamp.
        """
        import logging
        import re

        from huible.api.app import _JsonLineFormatter

        mod = _load_module()
        record = logging.LogRecord(
            name="huible.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="chat.trace session=s action=handoff",
            args=(),
            exc_info=None,
        )
        line = _JsonLineFormatter().format(record)
        ts = json.loads(line)["ts"]
        # guard the premise: the deployed formatter really emits literal %f
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.%fZ", ts), ts
        parsed = mod._parse_ts(ts)
        assert parsed is not None, f"reader must parse the real stamp: {ts}"
        assert parsed.tzinfo is not None


class TestAssertLive:
    """HU-2674 liveness gate: zero sink lines must not false-GREEN a digest."""

    def test_green_with_fresh_sink_lines(self, tmp_path, monkeypatch):
        mod = _load_module()
        log = tmp_path / "telemetry.log"
        log.write_text(_line("chat.trace session=s action=continue", age_hours=1) + "\n")
        rc = mod.assert_live(log, timedelta(hours=24), skip_startup_line=True)
        assert rc == 0

    def test_false_green_fails_when_db_shows_traffic(self, tmp_path, monkeypatch):
        mod = _load_module()
        empty = tmp_path / "telemetry.log"
        empty.write_text("")
        monkeypatch.setattr(mod, "_resolve_db_dsn", lambda explicit: "postgresql://x")
        monkeypatch.setattr(mod, "_db_traffic_rows", lambda dsn, seconds: 14)
        rc = mod.assert_live(empty, timedelta(hours=24), skip_startup_line=True)
        assert rc == 1

    def test_green_on_confirmed_quiet_window(self, tmp_path, monkeypatch):
        mod = _load_module()
        empty = tmp_path / "telemetry.log"
        empty.write_text("")
        monkeypatch.setattr(mod, "_resolve_db_dsn", lambda explicit: "postgresql://x")
        monkeypatch.setattr(mod, "_db_traffic_rows", lambda dsn, seconds: 0)
        rc = mod.assert_live(empty, timedelta(hours=24), skip_startup_line=True)
        assert rc == 0

    def test_fails_unverifiable_when_no_dsn(self, tmp_path, monkeypatch):
        mod = _load_module()
        empty = tmp_path / "telemetry.log"
        empty.write_text("")
        monkeypatch.setattr(mod, "_resolve_db_dsn", lambda explicit: "")
        rc = mod.assert_live(empty, timedelta(hours=24), skip_startup_line=True)
        assert rc == 1

    def test_fails_when_db_unreachable_and_sink_empty(self, tmp_path, monkeypatch):
        mod = _load_module()
        empty = tmp_path / "telemetry.log"
        empty.write_text("")
        monkeypatch.setattr(mod, "_resolve_db_dsn", lambda explicit: "postgresql://x")
        monkeypatch.setattr(mod, "_db_traffic_rows", lambda dsn, seconds: None)
        rc = mod.assert_live(empty, timedelta(hours=24), skip_startup_line=True)
        assert rc == 1

    def test_startup_line_gate(self, tmp_path, monkeypatch):
        mod = _load_module()
        log = tmp_path / "telemetry.log"
        log.write_text(_line("chat.trace session=s action=continue", age_hours=1) + "\n")

        def _docker_ok(cmd, **kwargs):
            return subprocess.CompletedProcess(
                cmd, 0, stdout=f"app-1 | {mod.SINK_ACTIVE_MARKER}: /x\n", stderr=""
            )

        def _docker_no_marker(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, stdout="up and fine\n", stderr="")

        monkeypatch.setattr(mod.subprocess, "run", _docker_ok)
        assert mod._startup_line_confirmed() is True
        assert mod.assert_live(log, timedelta(hours=24)) == 0

        monkeypatch.setattr(mod.subprocess, "run", _docker_no_marker)
        assert mod._startup_line_confirmed() is False
        assert mod.assert_live(log, timedelta(hours=24)) == 1

    def test_resolve_db_dsn_translates_service_host(self, monkeypatch):
        mod = _load_module()
        monkeypatch.delenv("DATABASE_URL", raising=False)
        dsn = mod._resolve_db_dsn(
            "postgresql+asyncpg://huible:pw@postgres:5432/huible"
        )
        assert dsn == "postgresql://huible:pw@127.0.0.1:5433/huible"
