# Runbook: C6 Daily Clinical Review — Stage-1 Week-1 Cohort (HU-2244 verdict)

**Audience:** Clinical Advisor (CA) during the week-1 cohort window.
**Binding source:** HU-2244 final verdict, condition C6 (2026-08-30T17:37Z):
daily CA review of cohort guardrail telemetry — consent fires, crisis/handoff
events, ungrounded claims, kill-switch/rollback state, page ack latencies.
**One sentence:** every day of week-1 cohort operation, the CA reviews the five
C6 telemetry classes plus the C2/C3/C4 boundaries and posts a dated review
record; a missed review day is itself a reversion event under C8.

## When this runbook is live

- Starts: first cohort session (the same event that starts the C7 7-day clock).
- Ends: C7 expiry (7 days after first session), first real-user crisis event
  (C5 hard stop), or C8 reversion — whichever is earliest.
- While live, the review is **daily, no skip days**. If the CA cannot review on
  a given day, that fact must be posted and treated per C8 (reversion), not
  silently rolled over.

## Daily review checklist

Post the record (format below) each day before 22:30 ET (inside the C2
coverage window, so anomalies found same-day are actionable while the CA seat
can still act). Sources mirror the Stage-1 ops-watch digest surfaces.

### 1. Consent fires (G6)

- Surface: `consent.record` grouped by `card_version` (live G6 consent-card
  flow).
- Checks: every real-user consent event maps to a **screened, invited** user
  (C4 roster on file — pre-consent risk-exclusion screen passed); no consent
  events for unknown/sixth users (C3); consent recorded at session start, not
  retroactively.
- Anomaly: any consent fire without a matching C4 screen record → C8 reversion
  review + hold that user's sessions pending CA clearance.

### 2. Crisis / handoff events (G1)

- Surface: `chat.trace` risk actions (`continue` / `handoff` / `tighten`),
  handoff ticket outcomes (`no_responder_available` degrades, abandoned), live
  pending tickets.
- Checks: **zero ceiling-tier events involving real users** is the only GREEN
  state. Any real-user ceiling/handoff event is a C5 hard stop — execute C5
  (i)–(iv) immediately, before completing this review; synthetic/demo traffic
  is reviewed for regression signals only.
- Anomaly: real-user crisis → C5 hard stop (session held, same-hour founder
  escalation, same-day option-(a) initiation, full-cohort suspension until CA
  written resumption clearance).

### 3. Ungrounded claims (G9)

- Surface: `chat.trace` ungrounded counts (`ungrounded=x/y`).
- Checks: real-user sessions show `0` ungrounded claims; nonzero counts on
  real-user sessions get per-claim inspection (what leaked, from which vault
  layer). Synthetic traffic is trended, not gated.
- Anomaly: any real-user ungrounded claim > 0 → after-action review; repeated
  (>1 day or >1 per user) → tighten or suspend pending fix.

### 4. Kill-switch / rollback state

- Surface: kill-switch hits (503s), rollback executions, current prod epoch vs
  the last CA-verified epoch (deploy-drift check — the HU-2285 lesson: a merged
  fix is not a shipped fix; verify the running container, not the git log).
- Checks: zero unexpected activations; any activation has a documented trigger
  and restore; running epoch still contains all crisis-lexicon fixes
  (HU-2216 `79c7203` lineage).
- Anomaly: epoch drift without re-verification → suspend cohort persona-chat
  until CA re-runs the 5-probe crisis coverage check on the new epoch.

### 5. Page ack latencies (C1)

- Surface: `handoff.page` CRITICAL events with delivery transport ≠ log,
  `clinical_always` resolution, and ack timestamps.
- Checks: every ceiling-tier page shows CA-seat wake + ack within the 15-min
  SLA; transport is a real device path, never log-only. Synthetic drill pages
  count for wiring verification, not for SLA credit once real users are live.
- Anomaly: any missed/late ack on a real-user page → C5-equivalent response;
  log-mode fallback ever observed on real-user pages → immediate reversion
  (log-mode-only real-user paging is unacceptable per the HU-2244 verdict).

### 6. Boundary checks (C2/C3/C7)

- C2: all real-user persona-chat sessions started/ended inside CA-seat
  coverage ≤ 08:00–22:00 ET; no unsupervised hours.
- C3: distinct consented real users ≤ 5; sixth user or non-consented exposure
  is an immediate reversion event.
- C7: days remaining on the 7-day clock; post-expiry traffic requires
  explicit CA re-authorization (no renewal by default).

## Review record format (post daily)

```
### C6 daily clinical review — YYYY-MM-DD (day N/7)
Scope: real users N/5; sessions M; coverage window honored: yes/no
1. Consent fires: <counts + C4 match result>
2. Crisis/handoff: <real-user events = 0 expected; synthetic trend note>
3. Ungrounded claims: <real-user 0 expected; anomalies>
4. Kill-switch/rollback: <state + epoch = <sha> (drift since last review: yes/no)>
5. Page acks: <pages, transport, ack latencies vs 15-min SLA>
6. Boundaries: C2 ok / C3 ok / C7 day N
Verdict: GREEN | AMBER (after-action opened) | RED (C5/C8 invoked)
Actions: <none | list with owners>
```

## Anomaly handling

- **After-action review (AMBER):** CA-owned, opened same day, root cause +
  disposition recorded in the cohort C6 thread; anomaly classes: ungrounded
  claim on real user, late page ack on synthetic drill, boundary near-miss
  (session grazing the coverage edge), consent-record gap.
- **C5 hard stop (RED):** real-user crisis event — execute C5 (i)–(iv);
  cohort-wide suspension is default-on; resumption only via CA written
  clearance posted after after-action review.
- **C8 reversion:** any failed/lapsed/violated C-condition voids the HU-2244
  revision; original condition 1 re-operates automatically (real-user
  persona-chat gated on HU-1425 until option (a) staffed) — no further CA
  comment required, but the reversion event itself is posted to the record.

## Relationship to other watches

- The Stage-1 ops-watch daily digest (telemetry, e.g. HU-2301) is the **data
  source**; this C6 review is the **clinical interpretation and disposition**
  layer. GREEN ops telemetry does not by itself imply C6 GREEN — the CA record
  above is the C6 artifact of record for the C8 question "was review done".
