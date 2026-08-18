"""Alert-rule contract tests (HU-1880 — §7.4 alert-enablement point).

Keeps ``examples/prometheus-alerts.yml`` honest against the /metrics gauges
the app actually emits: the handoff degrade-rate **page** rule must be gated
on roster staffing (``huible_handoff_available_responders > 0``), and the
pre-staffing degrade signal must exist at ticket severity so the expected G1
fail-safe never pages an unactionable 24/7 alert (incident of record
2026-08-18: one pre-staffing degrade paged for 25 minutes).
"""

from __future__ import annotations

from pathlib import Path

import yaml

RULES_PATH = Path(__file__).resolve().parents[2] / "examples" / "prometheus-alerts.yml"


def _rules() -> dict[str, dict]:
    loaded = yaml.safe_load(RULES_PATH.read_text())
    alerts: dict[str, dict] = {}
    for group in loaded["groups"]:
        for rule in group["rules"]:
            alerts[rule["alert"]] = rule
    return alerts


def test_degrade_rate_page_rule_gated_on_roster_staffing():
    rule = _rules()["HuibleHandoffDegradeRate"]
    assert rule["labels"]["severity"] == "page"
    expr = rule["expr"]
    assert "huible_handoff_degrade_rate > 0" in expr
    # HU-1880: paging arms exactly at roster staffing — pre-staffing degrades
    # (the clinically correct G1 fail-safe) must not page.
    assert "huible_handoff_available_responders > 0" in expr


def test_unstaffed_degrade_rule_is_ticket_severity_not_page():
    rule = _rules()["HuibleHandoffDegradeRateUnstaffed"]
    assert rule["labels"]["severity"] == "ticket"
    expr = rule["expr"]
    assert "huible_handoff_degrade_rate > 0" in expr
    assert "huible_handoff_available_responders == 0" in expr


def test_page_and_unstaffed_rules_are_mutually_exclusive():
    """Staffed page + unstaffed ticket partition the degrade signal: every
    degrade is visible, only staffed-roster degrades page."""
    page = _rules()["HuibleHandoffDegradeRate"]["expr"]
    ticket = _rules()["HuibleHandoffDegradeRateUnstaffed"]["expr"]
    assert "> 0" in page.split("huible_handoff_available_responders")[1]
    assert "== 0" in ticket.split("huible_handoff_available_responders")[1]
