"""Durable telemetry file-sink tests (HU-1945).

The daily-review runbook reads the stdout telemetry surfaces
(``chat.trace`` / ``consent.record`` / ``handoff.page``) over a trailing 24h
window, but docker json-file history dies with the container on every
recreate. HU-1945 mirrors those lines to a rotating file under the
bind-mounted app-state volume via :func:`configure_logging`. These tests
assert the sink captures exactly the telemetry surfaces, stays idempotent,
and degrades to stdout-only when the path is disabled or unwritable.
"""

from __future__ import annotations

import json
import logging

import pytest

from huible.api.app import configure_logging
from huible.api.settings import Settings


@pytest.fixture()
def root_logger_restored():
    """Snapshot/restore root handlers so the global sink never leaks tests."""
    root = logging.getLogger()
    before = list(root.handlers)
    yield root
    for handler in list(root.handlers):
        if handler not in before:
            handler.close()
            root.removeHandler(handler)


def _sink_handlers(root: logging.Logger) -> list[logging.Handler]:
    return [h for h in root.handlers if getattr(h, "_huible_telemetry_sink", False)]


def _settings(tmp_path, **overrides) -> Settings:
    return Settings(telemetry_log_path=str(tmp_path / "logs" / "telemetry.log"), **overrides)


class TestTelemetryFileSink:
    def test_sink_mirrors_only_telemetry_lines(self, tmp_path, root_logger_restored):
        configure_logging(_settings(tmp_path))
        sink = _sink_handlers(root_logger_restored)
        assert len(sink) == 1, "expected exactly one telemetry sink handler"

        logging.getLogger("huible.test").info("chat.trace session=s action=handoff")
        logging.getLogger("huible.test").info("consent.record session=s card_version=3")
        logging.getLogger("huible.test").critical("handoff.page ticket=t sev-1")
        logging.getLogger("huible.test").info("persona registry hydrated: 1 persona(s)")
        sink[0].flush()

        lines = (tmp_path / "logs" / "telemetry.log").read_text().splitlines()
        messages = [json.loads(line)["message"] for line in lines]
        assert messages == [
            "chat.trace session=s action=handoff",
            "consent.record session=s card_version=3",
            "handoff.page ticket=t sev-1",
        ], "non-telemetry lines must not reach the durable sink"
        # each mirrored line is the same JSON-line shape as stdout
        assert set(json.loads(lines[0])) >= {"ts", "level", "logger", "message"}

    def test_stdout_handler_still_attached(self, tmp_path, root_logger_restored):
        configure_logging(_settings(tmp_path))
        from huible.api.app import _JsonLineFormatter

        assert any(
            isinstance(h, logging.StreamHandler)
            and isinstance(h.formatter, _JsonLineFormatter)
            for h in root_logger_restored.handlers
        ), "stdout telemetry surface must remain alongside the durable sink"

    def test_configure_logging_is_idempotent(self, tmp_path, root_logger_restored):
        settings = _settings(tmp_path)
        configure_logging(settings)
        configure_logging(settings)
        assert len(_sink_handlers(root_logger_restored)) == 1

    def test_empty_path_disables_sink(self, root_logger_restored):
        configure_logging(Settings(telemetry_log_path=""))
        assert _sink_handlers(root_logger_restored) == []

    def test_unwritable_path_degrades_to_stdout_only(self, tmp_path, caplog, root_logger_restored):
        blocker = tmp_path / "not-a-dir"
        blocker.write_text("occupied")
        bad = blocker / "logs" / "telemetry.log"
        with caplog.at_level(logging.WARNING):
            configure_logging(Settings(telemetry_log_path=str(bad)))
        assert _sink_handlers(root_logger_restored) == []
        assert any(
            "telemetry file sink disabled" in r.getMessage() for r in caplog.records
        ), "startup must warn once, never raise, when the sink path is unwritable"

    def test_rotation_keeps_newest_window_readable(self, tmp_path, root_logger_restored):
        # tiny files force rollover after a handful of lines; backupCount=2
        # bounds total retention exactly like the 20 MB x 4 prod sizing.
        settings = _settings(tmp_path, telemetry_log_max_bytes=1024, telemetry_log_backup_count=2)
        configure_logging(settings)
        for n in range(40):
            logging.getLogger("huible.test").info("chat.trace session=s turn=%d", n)
        log_dir = tmp_path / "logs"
        rotated = sorted(p.name for p in log_dir.iterdir())
        assert any(name.endswith(".1") for name in rotated), f"expected rollover: {rotated}"
        # rotation must never drop the tail: the newest telemetry line stays
        # readable (prod sizing 20 MB x 4 backups holds months of telemetry vs
        # a ~KB/day real rate, so the trailing 24h window always survives).
        active = (log_dir / "telemetry.log").read_text()
        assert "turn=39" in active
        total = sum(p.read_text().count("chat.trace") for p in log_dir.iterdir())
        assert total > 10, f"rotation discarded too much history: {total} lines"
