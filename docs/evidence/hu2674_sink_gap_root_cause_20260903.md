# HU-2674 — Durable-sink telemetry mirror gap (Sep 1–2): root cause + guards

Investigated 2026-09-03. Question: why did the HU-1945 durable telemetry sink
(`docker/runtime/app-state/logs/telemetry.log`) write **0 lines on 2026-09-01
and 2026-09-02** after 182 lines on Aug 31, resuming only after the W1-cutover
recreate (2026-09-03T08:59Z)?

## Verdict

**The sink never failed. There was no platform chat/consent/handoff traffic on
Sep 1–2 at all.** The listed recreate/misconfig candidates are all ruled out by
the docker daemon journal; the DB-backed mirrors of the same telemetry surfaces
show the exact same zero-rows gap, which a broken sink could not explain.

## Evidence

### 1. The container serving Sep 1–2 was created Aug 31 15:34:42Z and killed Sep 3 08:59:01Z

`journalctl -u docker` (single boot, covers Aug 30 15:42 → present) shows every
`huible-app` recreate as a `stopping restart-manager` + `task-delete` +
`sbJoin ep=huible-app` triplet:

| Recreate (UTC) | Deploy (from git/ops record) |
|---|---|
| 08-31 00:53:18 | 00:52Z fix deploy (image 3bc512c1, HU-2245) |
| 08-31 09:21:17 | 09:21Z deploy epoch |
| 08-31 14:15:54 | 40dda40cfcca (799b145, HU-2285 sev-1 gap closed) |
| 08-31 15:24:53 | (same deploy window) |
| 08-31 15:34:42 | **last pre-gap recreate — served Sep 1–2** |
| 09-03 08:59:02 | W1 cutover (9ae81ea) |

**Zero container lifecycle events between 08-31 15:34:42Z and 09-03 08:59:02Z**
(the only Sep 2 entries are image-signature/DNS resolver noise). Therefore:

- *"Recreate without the app-state bind mount"* — **ruled out**: there was no
  recreate in the gap; the serving container wrote 182 sink lines (through
  19:38:54Z Aug 31) after its own creation, proving the mount + sink were live
  in exactly that container.
- *"Image predating the HU-1945 sink"* — **ruled out**: same image
  (40dda40cfcca, deployed 08-31 14:15Z) demonstrably had the sink (post-deploy
  lines exist).
- *"Deploy path emptied `telemetry_log_path`"* — **ruled out**: no recreate
  means no env re-read; `.env` contains no `TELEMETRY_LOG_PATH` at all, so the
  default path was in force throughout.
- *"Rotation swallowed the lines"* — **ruled out**: no `.1` backup files; file
  is 90 KB vs the 20 MB rotate threshold.
- *"Orphan sink file elsewhere on the host"* — **ruled out**: `find / -name
  'telemetry.log*'` finds exactly one real file (the canonical app-state one).

### 2. The DB mirrors show the same zero gap — there was no traffic to mirror

`conversation_turns` / `consent_records` / `llm_usage` per-day row counts
(psql on prod):

| day (UTC) | turns | consent | llm_usage | sink lines |
|---|---|---|---|---|
| 08-31 | 196 | 36 | 26 | 182 |
| 09-01 | **0** | **0** | **0** | **0** |
| 09-02 | **0** | **0** | **0** | **0** |
| 09-03 | 6 | 3 | 3 | 6 |

Every durable mirror of the stdout telemetry surfaces went to zero together
with the sink. A sink failure would leave the DB tables populated; instead the
platform was genuinely quiet Sep 1–2 (git history is likewise empty
08-31 20:29Z → 09-03 08:05Z). The "stdout carried traffic" seen in the interim
sweep was non-telemetry output (healthchecks / access logs), which the sink
filter (`chat.trace|consent.record|handoff.page` prefixes) correctly ignores.

### 3. The real defect: the digest cannot tell "quiet" from "sink blind"

GREEN-on-zero-lines is only valid when traffic is provably zero too. A future
misconfigured recreate (sink fails to attach while traffic flows) still
produces the false-GREEN the issue worried about — so the guards below assert
the cross-check, not just the file.

## Guards shipped (this issue)

1. **`scripts/telemetry_window.py --assert-live`** (digest liveness gate):
   GREEN only when the running container's log confirms
   `telemetry file sink active` AND the sink has fresh window lines OR the DB
   traffic tables (`conversation_turns` / `consent_records` /
   `handoff_tickets`) confirm a genuinely quiet window. Sink-empty + DB
   traffic = exit 1 (false-GREEN trap). Sink-empty + DB unreachable = exit 1
   (unverified ≠ GREEN). Runbook: docs/09 §7.4.
2. **`GET /api/v1/health` `telemetry_sink` check** (loud attach failure): the
   sink attach outcome is recorded at startup; a failed (unwritable) path now
   reports `checks.telemetry_sink = "failed (...)"` and overall `degraded`
   (→ `HuibleHealthDegraded` alert) instead of one stdout warning. Explicit
   `TELEMETRY_LOG_PATH=""` reports `disabled` and stays `ok`.

## Surfaces (2)–(5) for Sep 1–2

Unrecoverable from the sink (nothing was ever written — and nothing was lost:
the DB mirrors are equally empty). No data recovery action is possible or
needed; the guard prevents the equivalent silent gap going forward.
