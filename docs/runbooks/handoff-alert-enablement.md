# Runbook: Alert-Enablement Point at Roster Staffing (HU-1880, HU-1428 item 2)

**Audience:** operator executing the §7.4.1 staffing decision (HU-1432) — the
moment `HANDOFF_AVAILABLE_RESPONDERS` goes from `0` to `> 0` in production.

**One sentence:** degrade-rate **paging arms itself** the moment the roster is
staffed — no alert-rule edit, no baseline-reset command; the only operator
action is the staffing env change plus the go-live checks below.

## Why this runbook exists

`HuibleHandoffDegradeRate` (§4.1 halt-the-ramp trigger) used to page on any
degrade — but pre-staffing, `no_responder_available` degrades are the
**clinically correct G1 fail-safe**, i.e. the expected state that the §7.4
operational gate exists to close. Incident of record: 2026-08-18 15:40–16:05Z,
one synthetic pre-staffing degrade pinned the (then all-time) gauge at 100% and
paged for 25 minutes with no possible operator action (HU-1865 window fix;
HU-1880 enablement gate).

## The mechanism (no timing decision baked in)

| Signal | Rule | Severity | When it fires |
|---|---|---|---|
| `huible_handoff_degrade_rate > 0` **and** `huible_handoff_available_responders > 0` | `HuibleHandoffDegradeRate` | **page** | A responder was configured but escalations still failed — genuine incident, halt the ramp |
| `huible_handoff_degrade_rate > 0` **and** `huible_handoff_available_responders == 0` | `HuibleHandoffDegradeRateUnstaffed` | ticket | Expected pre-staffing fail-safe; triage in ops review, never pages |

The staffing gauge (`huible_handoff_available_responders`) mirrors the live
queue on every `/metrics` scrape, so the page rule arms at the first scrape
after the env change and cannot drift from what the queue actually does.

## Go-live sequence (roster staffing day)

1. **Set the staffing env** per the HU-1432 decision (values pinned in the
   HU-1428 AC #2 config table):
   `HANDOFF_AVAILABLE_RESPONDERS`, `HANDOFF_COVERAGE_MODE/TZ/OPEN/CLOSE`,
   `HANDOFF_RESPONDER_POOL`. Redeploy/restart so the queue is reconstructed.
2. **Verify the enablement signal:**
   `curl -s http://127.0.0.1:8000/metrics | grep huible_handoff_available_responders`
   → must show the staffed count (not `0.0`).
3. **Confirm the page rule is armed** in Prometheus: `HuibleHandoffDegradeRate`
   expr now evaluates the staffing clause against a non-zero gauge;
   `HuibleHandoffDegradeRateUnstaffed` goes permanently inactive.
4. **Baseline reset:** nothing to reset by hand — pre-staffing degrades age out
   of the rolling 24h window (`HANDOFF_TELEMETRY_WINDOW_SECONDS`, HU-1865)
   within at most 24h of staffing. §8 "zero alerts" evidence should be dated
   **from the staffing timestamp**: the 24h after staffing is the honest
   baseline window while old degrades roll off.
5. **Answered-SLA check:** `HuibleHandoffAnsweredSLABurn` (< 0.9) is *not*
   staffing-gated — a real pending ticket past SLA is an emergency at any
   staffing level. After staffing, confirm the answered rate climbs with real
   answers; if synthetics depress it, close them per the
   [synthetic-ticket closure runbook](handoff-synthetic-ticket-closure.md)
   (`abandoned`, never `answered`).

## Rollback

Set `HANDOFF_AVAILABLE_RESPONDERS=0` (staffing withdrawn). Paging disarms
itself on the next scrape; degrades return to the ticket-severity pre-staffing
rule. No alert-rule edits in either direction.
