# HU-2830 — tester-local time-of-day: cause, fix, live evidence (2026-09-12)

## Cause of the 8777 wrong-time moment (lead #1 confirmed)

- Founder session `portal-ab1c710de1af2291` ran 2026-09-11 20:24–20:35Z (verdict posted 20:45Z). The
  caretaker-tz fix (HU-2828) landed 23:13–00:03Z — after the session.
- Pre-HU-2828 `caretaker_reply()` rendered the server clock (host = Etc/UTC) as "the local time": a
  ~20:3xZ probe told a NYC tester it was ~20:30 — 4h off.
- No wrong-time exchange appears in `conversation_turns` because the caretaker lane never records
  turns (`src/huible/api/app.py` caretaker branch returns before `_record_turn`) — the moment is
  invisible in the DB by design, matching "wrong at one point".
- Lead #2 confirmed at the data layer: Chandler's live row persists
  `metadata.in_world_clock = "noon"` (no repo writer; provision-side). Only the
  `X-Huible-Client: portal-human` header kept it off the human path.
- Lead #3 confirmed by shape: embedded clock forms ("do you know what time it is?") missed
  `_TEMPORAL_PATTERNS`, leaving the persona to answer from its (pinned) in-world clock line.

## Fix (commit 122be6c, branch w1-local-onnx-embeddings)

1. Caretaker classifier: embedded real-clock shapes added (end-anchored so autobiographical forms
   like "tell me the time of our final?" stay persona-voiced).
2. Noon pin is machine OPT-IN (`X-Huible-Client: battery-flow`): headerless human surfaces get the
   persona's real location time. Battery harness (`scripts/personas_dual_converse.py`) sends the
   header. Portal (8777) path unchanged (portal-human + internal traffic class).
3. Regression probes: `tests/api/test_chat_realworld_and_transient.py`
   (`test_headerless_caller_ignores_noon_pin`, `test_battery_flow_header_keeps_noon_pin`,
   `test_caretaker_catches_embedded_clock_question`), classifier positives/negatives in
   `tests/persona/test_persona_tools.py`. Era wall untouched (boundary still pinned 2004-05-06).

## Verification

- Suites: `tests/api + tests/persona + tests/safety` → 1137 passed, 3 skipped, 1 pre-existing
  env-sensitive failure (`test_chat_memory_eviction.py::test_planted_turn_leaves_prompt_context`;
  fails on clean HEAD too — host `.env` GENERATOR_PROVIDER=openrouter falls back to mock and drops
  recorded rows; not a HU-2830 regression).
- Live engine (redeployed after the r10 verdict read; r10 finished 01:36:47Z, verdict read from
  `runs/hu2774/r10_oneshot.log` = FAIL 2/6, HU-2774's own bar):

```
UTC now: 02:03 / NYC now: 22:03 EDT
message: "quick check — do you know what time it is over there?"   (embedded form, missed pre-fix)
reply: [Caretaker — out of character, not Chandler Bing]: Today is Friday, September 11, 2026;
       the local time is 22:03 EDT. ...
caretaker: {'kind': 'temporal', 'era_boundary': '2004-05-06',
            'gates_cleared': ['g1_crisis', 'g6_consent', 'g8_risk']}

battery-flow turn (X-Huible-Client: battery-flow): HTTP 200, provider zai (pin opt-in path intact)
```

## Residual notes

- `in_world_clock: "noon"` remains persisted on the persona rows (battery energy lever); it is now
  inert for any caller that does not explicitly identify as a battery flow.
- Eviction test host-env drift should be pinned like HU-2828's 32a7fb8 (Settings env-proofing) —
  follow-up candidate, not a HU-2830 blocker.
