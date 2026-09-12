# HU-2774 r12 verdict — INVALID RUN (budget fake-voice, mid-battery abort)

Recorded 2026-09-12T~04:10Z by Huible Tech Lead from `runs/hu2774/r12_oneshot.log`.

## Verdict

**r12 produced no engine-quality signal.** The battery must be re-fired (r12b) after
the OpenRouter monthly cap is raised/topped up. Do not score r12 as a pass/fail round;
the r9→r11 trend (0/6 → 2/6 → 3/6) is unchanged as the last valid state.

## Finding 1 — every turn ran on the budget-exhausted fake voice

- Every logged turn carries `zai->fake(budget)`: the hosted call raised
  `LLMBudgetExceededError` and the handler (src/huible/api/app.py:1838-1853) served the
  deterministic `_FakeLLMClient` per the board-approved HU-1774 posture
  ($50/mo hard cap, fake voice as rollback; `OPENROUTER_MONTHLY_BUDGET_USD=50` in `.env`).
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

## Unblock (board action)

Raise or top up the OpenRouter monthly cap (HU-1774 reserves cap changes to the board),
restart the engine so the env change lands, then re-fire the battery (r12b) — engine deploy
(d571eb6), one-shot + flock guard (2ce3d2f) and dynamics v3 are already in place.
