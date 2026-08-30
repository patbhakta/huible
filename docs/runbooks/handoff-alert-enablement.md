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

## Precondition: coverage-shape confirmation (added 2026-08-25)

Operator input (Pat, 2026-08-25T13:53Z) describes the responder arrangement as
**as-needed with no set schedule** — differing from the board-approved Option A
shape (2 grief responders + 1 on-call clinician, fixed 08:00–22:00 US Eastern,
7d/wk; approval 6334d570). Before step 1 below, the activator must hold one of:

- **Shape match:** responders are engaged as-needed **but committed to the
  configured coverage window** (on shift for the full 08:00–22:00 ET window).
  As-needed *engagement* plus scheduled *coverage* is not a conflict —
  activate with the Option A defaults.
- **Re-baseline:** a superseding board approval re-baselining the coverage
  model (windows / counts / mode). Activate with `--responders/--open/--close`
  (or a coverage-mode change) matching **that** approval, and the coverage
  change is re-reviewed by the Clinical Advisor (HU-1428 AC #5) before any
  real-user traffic.

A purely as-needed arrangement with **no committed window must not be
activated** with `HANDOFF_COVERAGE_MODE=hours` defaults (nor `=always`): the
configured window would claim staffed coverage that does not exist — the same
never-lie rule that keeps `available_responders=0` pre-staffing.

## Re-baseline on file: week-1 posture (b) (added 2026-08-30)

Board approval **b18e611a** ("Roster re-raise at cohort key release…",
approved 2026-08-30T16:41:14Z) is the superseding re-baseline this section
requires. Approved week-1 shape, scoped to cohort 1/2 (5–10 consenting
adults, fictional persona):

- **Posture (b) G1-degrade + CA ceiling paging** — as-needed coverage, **no
  committed responder windows**. Consequence: **do not run
  `activate_responder_roster.sh --activate` in week 1** — `available_responders`
  stays `0` by design, escalations degrade to the honest G1 safe response, and
  `HuibleHandoffDegradeRateUnstaffed` (ticket severity, never pages) is the
  *expected* firing state.
- **Crisis page policy stays armed and audited** at HEAD (crisis-enqueue +
  Sev-1 triggers → `handoff.page` CRITICAL lines + telemetry). Device-level
  paging transport (`HANDOFF_PAGER_PROVIDER`≠log, `HANDOFF_ONCALL_CONTACTS`)
  is grouped by the approval under option (a)'s remaining deploy/env steps.
- **Binding commitments (activation triggers for option (a), approval
  6334d570 defaults):** staff option (a) **before any scale-up beyond cohort
  1/2**, and **immediately upon the first real-user crisis event**. Either
  trigger fires this runbook's go-live sequence (same-day turnkey;
  `--check` re-verified CHECK_OK 9/9 on 2026-08-30T16:50Z).
- The coverage change is re-reviewed by the Clinical Advisor (HU-1428 AC #5)
  before any real-user traffic; real-user persona-chat traffic remains gated
  on HU-1425 until option (a) is staffed.

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
