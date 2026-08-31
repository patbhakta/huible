# FlutterIgnite 2026-08-31 — M1 Text-Chat Prototype (Agent-Fleet Dart Velocity Test)

**Issue:** HU-2162 (CEO work order Aug 31, item 1) · **Executor:** Antigravity (`agy` CLI, model `claude-sonnet-4-6`) · **Supervising review:** Huible Tech Lead
**Scope (CEO-ordered, subset of plan §3a.4):** chat screen, streaming reply rendering, FastAPI auth handshake. Prototype IS the velocity measurement.

## Result: PASS — all gates green, first shot, zero re-prompts

| Gate | Result | Time |
|---|---|---|
| `flutter analyze` | **No issues found** | 1.4s |
| `flutter test` | **18/18 passed** (16 unit + 2 widget, headless `flutter_tester`) | 8s |
| `flutter build web --release` | **built** (24MB incl. canvaskit) | 3.6s |

## Velocity datapoint (the deliverable)

| Phase | Owner | Elapsed |
|---|---|---|
| Flutter SDK 3.32.2 install on .245 (1.4GB tar.xz download + extract + first-run Dart bootstrap) | Tech Lead | ~5 min wall (one-time, scripted) |
| Task spec (TASK.md, self-contained API contract) | Tech Lead | ~10 min (concurrent with download) |
| **Prototype build: 1,385 lines Dart (lib+test), full feature set** | **Antigravity (Claude Sonnet 4.6)** | **10 min 28 s (628s), single dispatch, exit 0** |
| Independent verification + code review (analyze/test/build + file-by-file read) | Tech Lead | ~5 min |

**Total work-order-receipt → verified-committable: ~20 minutes.** Agent time dominated by waiting-on-nothing: no iteration loops, no human rescue, no re-prompts. Build log with per-step times: `BUILD_LOG.md` (written by the agent as it worked).

## What was built (verified by review, not just claimed)

- `app/` Flutter 3.32.2 project, house pattern per plan rev 8: `flutter_riverpod` + `go_router` + `package:http` (no dio, no codegen).
- Chat screen: bubble list, auto-scroll, input disabled while streaming, typing indicator.
- Streaming: `ChatDataSource` abstraction; NDJSON/SSE true-streaming path + plain-JSON typewriter fallback (current server shape); deterministic offline fake source.
- FastAPI auth handshake: Bearer key on every call; 401 `AUTH_REQUIRED` → sign-in card; 403 → persona-scope card; **409 `CONSENT_REQUIRED` → consent card render + acknowledge POST to `/api/v1/chat/{persona_id}/consent` + retry**; 503 → unavailable card.
- Tests: NDJSON parser, typewriter emitter, error mapping (401/403/409/503), full streaming widget flow, spinner-state widget test.

## Environment findings (ops-relevant, new)

1. **Antigravity Gemini backends are GEO-BLOCKED from .245**: `FAILED_PRECONDITION: User location is not supported for the API use`. The "home relay" requirement is real for Gemini-class. **Claude Sonnet 4.6 backend works directly from .245.** Standing-order implication: agy tasks on .245 run `--model claude-*`; Gemini-class needs the home relay (pat-w11pc is on the tailnet, idle/online) — wiring it is a follow-up task, not a blocker.
2. Flutter on .245 as root needs `git config --global --add safe.directory /opt/flutter` (recorded in BUILD_LOG). Disk cost ~4GB. Zero Node services — consistent with rev 8's "no Node on .245" rule.
3. agy CLI pitfall: `--print` consumes the next token as its prompt; attach the prompt to the flag and place `--print-timeout` elsewhere.

## Review notes (Tech Lead, review-then-merge)

- Mergeable as-is for prototype purposes. One integration nit for M1: 409 `consent_card` is currently read from the nested `error` object (`error.consent_card`); the live FastAPI 409 body shape (`detail.consent_card` vs inline) must be confirmed at first live integration and adjusted in one place (`remote_chat_data_source.dart:_throwMappedError`).
- Remaining §3a.4 spike items NOT covered by this CEO-scoped subset (queued as next Antigravity tasks): magic-link login against the real auth seed, one streamed turn through the live §7.4 pipeline, Caddy static serve on a dev hostname, CI build step, `flutter analyze/test` pre-commit hook.

## Verdict (evidence-based client-stack input for HU-2162)

The reframed question (Aug 30 addendum): *Flutter/Dart rebuild cost-value vs thin React shell, measured against agent-fleet Dart velocity.* This experiment answers the velocity half empirically: **a fleet coding agent produced a tested, analyzer-clean, release-buildable Flutter web client against our real API contract in 10.5 agent-minutes with zero human iteration.** Dart is not a fleet-velocity blocker at prototype scale; the standing risk stays where rev 8 already flags it (long-tail ecosystem freshness, web load size). Combined with the founder's stack-split directive, this supports **plan rev 8's ruling: Flutter now for the SaaS shell (M1 freeze), with the §3a.4 remainder executed as bounded Antigravity tasks under Tech-Lead review.**
