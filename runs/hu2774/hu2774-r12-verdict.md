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

---

# Addendum (2026-09-13 ~01:55Z) — r12b fired, r13 iterated; NEW provider-side wall found

## A1 — the r12b refire unit was dead on arrival (root cause + workaround)

- `hu2774-r12-refire.service` was created with `RuntimeMaxSec=0` **believing 0 = unlimited**.
  This host's systemd treats `RuntimeMaxSec=0` as an **immediate** runtime limit: the service
  was TERM'd **3 ms** after its 00:05:00Z start (`Result: timeout`, Duration: 3ms). The
  battery never ran; log stayed frozen at the Sep 12 stub state, no done marker.
- Workaround that works: launch `host_oneshot_r12.sh` directly, detached, under its flock
  guard (`setsid nohup ... &`) from a monitor wake. Used for both r12 and r13 today.
- Engine pre-flight was verified green before relaunch: image rebuilt 23:59:16Z contains
  `get_conversation_index_memories` (grep-verified in-container); zai day ledger fresh
  (23,868 tokens at 00:41Z); health ok. Health label `generator: ready (mock)` cleared as a
  non-blocker: the persona chat path serves via the zai `llm_client` (app.py:1806) with
  fake-voice only on `LLMBudgetExceededError`; `state.generator` is the two-tier voice
  abstraction whose mock is the board-mandated default (`.env:19`'s `GENERATOR_PROVIDER=
  openrouter` was never a valid enum value — cosmetic only).

## A2 — r12 (dynamics v3, 00:46:46Z→01:02:05Z): gate 2/6

| slot | qrate (band .20–.45) | echo (floor .23) | failed |
|---|---|---|---|
| friends-1 | 0.32 ✓ | 0.16 ✗ | grounded_wit |
| friends-2 | 0.16 ✗ | 0.08 ✗ | engagement, grounded_wit, no_ai_tells |
| friends-3 | 0.24 ✓ | 0.36 ✓ | — PASS |
| stranger-1 | 0.40 ✓ | 0.16 ✗ | grounded_wit |
| stranger-2 | 0.32 ✓ | 0.28 ✓ | — PASS |
| stranger-3 | 0.64 ✗ | 0.32 ✓ | engagement |

- **Progress that held**: identity 6/6 (v3 name-greeting fixed r11's recognition miss),
  memory_recall 6/6 (ordinal-index lane + registry hydration validated end-to-end).
- **Failure modes**: echo is bimodal (0.08–0.16 fail vs 0.28–0.36 pass) — the reuse rule sat
  mid-block and diluted; stranger-3 qrate 0.64 — the block header said "habits with a
  *friend*", leaving strangers unframed; friends-2 tell = self-surname gag ("the Bing Flex").
- → dynamics **v4**: positional echo opener (open the reply with their exact word),
  scenario-neutral header, one-in-three rhythm anchor, surname/accusation-word habit line.
  Applied + app restarted 01:07Z.

## A3 — r13 (dynamics v4, 01:11Z→01:36Z): gate 1/6 + provider 429 wall

| slot | qrate | echo | failed |
|---|---|---|---|
| friends-1 | 0.40 ✓ | 0.32 ✓ | no_ai_tells ("Ms. Geller-Bing" — hyphenate surname gag) |
| friends-2 | 0.36 ✓ | 0.24 ✓ | — PASS |
| friends-3 | 0.40 ✓ | 0.12 ✗ | grounded_wit, no_ai_tells ("my programmer wrote better comebacks") |
| stranger-1 | 0.20 (boundary) | 0.24 ✓ | engagement |
| stranger-2 | 0.48 (over top) | 0.36 ✓ | engagement |
| stranger-3 | — | — | INFRA: zai HTTP 429 code 1308, slot aborted |

- Echo improved where v4's positional opener was adopted; stranger qrates moved from blowout
  (0.64) to boundary misses (0.20/0.48). Two single-line tells decided two slots.
- **NEW blocker class — provider-side rolling window**: at 01:34:46Z zai returned
  `HTTP 429 {"code":"1308","message":"Usage limit reached for 5 hour. Your limit will reset
  at 2026-09-13 10:00:36"}`. This is the subscription's **5-hour rolling usage window**,
  distinct from both the app daily ledger (day usage ~0.6M/8M at the time) and the
  OpenRouter USD lane. The harness's ~200s retry cannot outlast it; the engine correctly
  refuses to fake-voice a transient error (503, `retryable: true`). **No battery can run
  until ~10:00:36Z.**
- → dynamics **v5**: first-names-only habit (kills hyphenate gags) +
  creator/programmer/developer added to banned vocabulary. Applied + app restarted 01:52Z.
  This is this session's last dynamics revision (3-revision cap); r14 scores v5 cold.

## A4 — governance flag (CEO visibility)

- `.env:92` now reads `ZAI_DAILY_TOKEN_LIMIT=8000000` — the corrected verdict above (and the
  CEO issue text: "z.ai lane budget now 2M/day") cite **2,000,000**. The 23:59:16Z image
  rebuild picked the raised value up. No approval is visible on the issue thread. Either a
  board approval exists off-thread, or this should be reverted to 2M. Batteries fit under
  either cap (~0.4–0.6M each); flagged, not actioned.
- Also noted: the provider 5h window is now the binding constraint for battery pacing —
  one battery per window, ~10:00Z and ~15:00Z are the next two reset-adjacent slots.

## A5 — next

- **r14 armed autonomously**: transient `hu2774-r14-refire.timer` → **2026-09-13 10:05:00 UTC**
  (4½ min after the 10:00:36Z window reset), service `RuntimeMaxSec=3h` (real limit — the
  `=0` mistake that 3ms-killed the r12b unit is documented in A1), flock-guarded, run tag
  `hu2774-r14`. **New preflight** (`scripts/personas_preflight_probe.py`, wired into
  `host_oneshot_r12.sh`): one cheap real turn before slot 1; aborts the whole battery if the
  429 wall is somehow still up — no slots burned into a closed window.
- If a monitor wake lands instead/also: the flock guard makes double-arm a no-op; check
  `R12_HOST_ONESHOT_DONE` (gate_rc=8 ⇒ preflight abort, 0 ⇒ all-pass battery, 1 ⇒ gate
  failure with full per-slot evidence) and `runs/hu2774/r12_oneshot.log`.
- Gate → verdict on HU-2774 + HU-2712. Bar unchanged: 6/6 → founder card path. If r14 fails
  gate: failure analysis → ONE targeted lever → re-run in the next 5h window (~15:00Z).
- Run 589e27a2 lost its control-plane writes mid-heartbeat (run JWT went 401 ~02:00Z, after
  two posted comments landed: 29b470fa ack + r12/r13 verdict comment was rejected). The
  committed verdict doc (this file, `dfa355f`+) plus the timer are the durable record;
  the adapter's run-response channel relays the rest.

