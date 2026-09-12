# HU-2774 r12 verdict — INVALID RUN (budget fake-voice, mid-battery abort)

Recorded 2026-09-12T~04:10Z by Huible Tech Lead from `runs/hu2774/r12_oneshot.log`.

## Verdict

**r12 produced no engine-quality signal.** The battery is re-fired automatically (r12b)
after the zai daily token ledger auto-resets — **no board action needed**. Do not score r12
as a pass/fail round; the r9→r11 trend (0/6 → 2/6 → 3/6) is unchanged as the last valid state.

**Correction (2026-09-12T04:12Z, per reviewer comment 3e700ca3):** the budget that fired is
the **zai DAILY token ledger** (`ZAI_DAILY_TOKEN_LIMIT=2000000`, `.env:92`; ledger
`docker/runtime/app-state/zai-tokens.json` shows 2026-09-11: 2,001,990 and 2026-09-12: 2,001,329),
not the $50/mo OpenRouter cap. The OpenRouter USD lane is a different provider and was not
involved; the zai lane is $0 incremental. The reset is automatic at 2026-09-13T00:00Z
(`DailyTokenTracker` buckets on `utc_day_key`) — no restart, no cap raise.

## Finding 1 — every turn ran on the budget-exhausted fake voice

- Every logged turn carries `zai->fake(budget)`: the hosted call raised
  `LLMDailyTokenLimitExceededError` (src/huible/llm/client.py:113), which **subclasses**
  `LLMBudgetExceededError` and therefore lands in the same handler
  (src/huible/api/app.py:1838-1853) that served the deterministic `_FakeLLMClient`.
  The handler's comment mentions only the HU-1774 OpenRouter posture ($50/mo hard cap,
  `OPENROUTER_MONTHLY_BUDGET_USD=50`, `.env:34) — that is how the initial diagnosis drifted
  to the wrong budget lane. The ledger actually crossed is the zai daily token ceiling
  (`ZAI_DAILY_TOKEN_LIMIT=2000000`, `.env:92`).
- r11 by comparison ran real `zai` replies (`runs/hu2774/r11_oneshot.log` line 11+).
- Consequence: all four failing checks are stub artifacts, not engine regressions:
  - `identity` — stub text cannot greet a friend by name.
  - `engagement` — qrate 0.0 (stubs ask no questions).
  - `memory_recall` — stub replies cannot echo recalled content.
  - `no_ai_tells` — the stub string `[fake-llm:*] Deterministic response.` is itself the
    provider tell on every scanned line.
  - `grounded_wit` PASS (echo 0.88 vs floor 0.23) is likewise meaningless — a checker
    artifact of repeated stub strings.
- The r12 engine changes under test (deterministic ordinal-index lane, dynamics v3) were
  therefore never judged.

## Finding 2 — battery aborted mid-run

- Only 3/6 slots ran (friends-1/2/3, each a full 25 turns, all FAIL-by-stub). The wrapper
  died immediately after the friends-3 verdict printed: no `confirm-friends-3 rc=` line,
  no stranger slots, no battery-gate, no `R12_HOST_ONESHOT_DONE` marker.
- Moot for scoring (Finding 1 already invalidates the run) but the re-fire procedure should
  confirm the launcher detaches cleanly; r10/r11 completed under the same pattern.

## Plumbing observations that DO carry (from telemetry counters, stub-independent)

- 25-turn loops completed with working set mem=20; per-slot resets clean (rc=0,
  `crisis_sessions=0` at every reset).
- Ordinal-recall lane visibly injected on the t12/t24 recall probes in friends-1 (wm=466)
  and friends-2 (wm=393; s2-Chandler-01 wm=295) — the r11 "predigest/0 silent-lane" bug did
  not recur in those slots.
- friends-3 t12 recall probe showed **wm=0** (lane did not inject) — variance to verify in
  r12b; possible residual lane bug or reset ordering effect.
- The r11 friends-1 crisis "alone" cascade did NOT recur — inconclusive: stub voice never
  generates the comedy line that armed it. The crisis.py narrowing + anti-repeat guard
  remain unvalidated until a real-voice round.

## Unblock (corrected 04:12Z — no board action)

- **No board/founder action required.** `DailyTokenTracker` buckets per-call on `utc_day_key`;
  at **2026-09-13T00:00Z** the zai daily ceiling clears itself. No restart, no cap raise.
- **r12b is already armed**: transient `hu2774-r12-refire.timer`, `OnCalendar=2026-09-13 00:05:00 UTC`
  (≈5 min after reset), hardened (`TimeoutStartSec=0`, `RuntimeMaxSec=0`, `KillMode=control-group`),
  runs `runs/hu2774/host_oneshot_r12.sh` unmodified (engine deploy d571eb6, flock guard 2ce3d2f,
  dynamics v3 in place). Timer verified active 04:16Z (`Trigger: Sun 2026-09-13 00:05:00 UTC`).
- **Killed 03:59:17Z battery**: its slot JSONs are stub-polluted and will be overwritten by r12b;
  `r12_oneshot.log` frozen 04:01:53, no `R12_HOST_ONESHOT_DONE`, no battery processes remain.
- **No battery may fire before 00:00Z** — one 6-slot real battery ≈ 400–500K tokens fits the
  fresh day's 2M budget.
- The detach/reap concern in Finding 2 is covered by the timer's hardened
  `KillMode=control-group` + `RuntimeMaxSec=0` and the existing flock guard.
- Post-completion (~00:30Z): arm the issue monitor (`nextCheckAt≈2026-09-13T00:40:00Z`) and
  confirm non-null `monitorNextCheckAt` in the PATCH response before claiming it; verify zero
  fake-voice markers across all 6 slots from `r12_oneshot.log`; land the verdict here and on
  HU-2712. Bar unchanged: 6/6 → founder card path.
