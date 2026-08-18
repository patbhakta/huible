# Runbook: Closing Synthetic/Verification Handoff Tickets (HU-1866, HU-1428 item 3)

**Audience:** on-call operator / Tech Lead heartbeat closing deploy-check, drill,
or verification tickets that escalated into the §7.4.1 human-handoff queue.

**One command:** `bash scripts/resolve_handoff_ticket.sh <ticket_id> --note "deploy-check HU-18xx"`
(uses outcome `abandoned`, adds the `SYNTHETIC:` provenance prefix, refuses
unsafe closures — details below).

## Why this runbook exists

Verification harnesses (deploy checks like HU-1435, rollback drills, future
§7.4 drills) create **real** handoff tickets via the admin risk-intake path.
They are indistinguishable from user escalations at the queue level, so closing
them wrongly either:

- leaves them `ENQUEUED` forever → `huible_handoff_pending_breached` climbs and
  the ack-SLA Sev-1 re-page (`escalate_sla_breaches`) fires on every queue
  read, or
- resolves them as `answered` → inflates the ramp-gate metric
  `huible_handoff_answered_within_sla_rate` (≥ 0.9 at Stage 1 is a launch
  gate — a synthetic `answered` ticket is faked compliance evidence).

Incident of record: 2026-08-18 15:40–16:05Z, synthetic ticket
`hh-ec0fa426a37b43bd` (HU-1435 deploy verification) pinned
`HuibleHandoffDegradeRate` at 100% for 25 min (Tech Lead monitor pass 16:06Z;
window fix HU-1865). It was closed ad-hoc with the correct
outcome but no documented procedure — this runbook is that procedure.

## Outcome semantics (grounded in `compute_handoff_telemetry`)

| Ticket state | What to do | Metric effect |
|---|---|---|
| `enqueued` (synthetic) | Resolve as **`abandoned`** with `SYNTHETIC:` note | Clears `pending`/`pending_breached` + stops re-pages; does **not** touch `answered_within_sla_rate`; counts honestly in `total` |
| `degraded` (terminal) | **Nothing.** Degrade is terminal fail-safe evidence | Ages out of the 24h alerting window (`HANDOFF_TELEMETRY_WINDOW_SECONDS`, HU-1865); stays in the all-time audit forever |
| `answered` / `abandoned` (terminal) | **Never re-resolve.** `resolve()` overwrites `outcome` + `clinical_review_note` on *any* ticket | Re-resolving erases the original audit evidence — this is how the 16:06Z incident ticket's `degraded` outcome became `abandoned` in the audit log |

**The rule: resolve only `ENQUEUED` synthetic tickets, always `abandoned`,
never `answered`.** Real user tickets are closed by the staffed responder in
the normal queue flow — this runbook is not for them.

## Provenance convention

Every synthetic closure note starts with `SYNTHETIC:` and names its source,
e.g. `SYNTHETIC: deploy-check HU-1435 verification, no user involved`. The
helper enforces the prefix; future tooling can grep the audit trail with
`clinical_review_note LIKE 'SYNTHETIC:%'`.

## Procedure

```bash
# 1. See what's open (also triggers the ack-SLA re-page path)
bash scripts/resolve_handoff_ticket.sh --list

# 2. Close the synthetic ticket
bash scripts/resolve_handoff_ticket.sh hh-xxxxxxxx \
    --note "deploy-check HU-18xx verification" \
    [--responder huible-tech-lead]   # default

# 3. Verify: pending queue empty, telemetry clean
bash scripts/resolve_handoff_ticket.sh --list          # expect {"data": []}
curl -s http://127.0.0.1:8000/metrics | grep huible_handoff_pending
#    huible_handoff_pending 0.0 ; zero firing alerts in Prometheus
```

Auth: the helper reads the first `API_KEYS` entry from `.env.failover`
(override with `ENV_FILE`/`HUIBLE_BASE_URL`). Without a key the API fails
closed with 401. Every resolve logs to `logs/resolve-handoff-<ts>.log`.

## Guards in the helper (do not bypass casually)

- **Terminal-outcome refusal (exit 2):** resolving a `degraded`/`answered`/
  `abandoned` ticket is refused because `resolve()` overwrites the audit row
  wholesale. `--force --reason "…"` is the documented escape hatch and the
  reason is appended to the note.
- **Missing ticket (exit 1):** ids not in the audit log are typos or wrong env.
- **Note required:** no silent closures; the note carries the provenance.

## Pre-staffing note (before HU-1428 AC #2 wires `available_responders > 0`)

With the fail-safe roster (`HANDOFF_AVAILABLE_RESPONDERS=0`) every synthetic
escalation **degrades at enqueue** — there is nothing to close. This runbook's
resolve path becomes live exactly when the roster is staffed; the windowing fix
(HU-1865) already bounds how long a pre-staffing degrade can pin the gauge.
