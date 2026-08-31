# FlutterIgnite 2026-08-31 — M2 Live Protocol Integration

**Issue:** HU-2162 (sprint lane: TL owns Antigravity utilization + Flutter prototype) · **Executor:** Antigravity (`agy`, `claude-sonnet-4-6`) · **Supervising review:** Huible Tech Lead

## Why M2 existed (M1 → live gap)

M1 passed all gates against an ASSUMED error body shape. On Aug 31 the Tech Lead probed the **live prod FastAPI app** (huible-app container, 127.0.0.1:8000) with the canary-allowlisted `demo-pat` key (persona `fdc3a44b…`, Chandler demo) and captured the REAL protocol:

| Step | Live result | M1 assumption | Gap |
|---|---|---|---|
| First msg, new session | **409**, card at `detail.error.consent_card`, plus server-issued `conversation_id` + `acknowledge_url` | card at `error.consent_card` | parse bug: `detail` unwrap missing |
| 409 card fields | `version: 3`, `title`, `body`, `acknowledge_instructions` | title/body only | card_version needed for the ack POST |
| POST `/consent` | `{"data":{"acknowledged":true,…}}` with `conversation_id` + `card_version` body | client never POSTed consent at all | **protocol bug: M1's ack was a blind resend → infinite 409 loop live** |
| Retry turn | **200 plain JSON** `{"response":…,"trace":{memory_refs, provenance_tiers, …}}` | typewriter fallback | M1 path correct, confirmed |

**Protocol insight (design-relevant):** the 409 *creates the session server-side* and hands the client its `conversation_id`. A correct client must adopt that id for the ack + retry. This is the consent-card ↔ session binding the onboarding platform must preserve on every channel (web or WhatsApp) — worth carrying into the platform recommendation verbatim.

One live §7.4 turn was consumed for evidence (canary persona, by design — the dogfood surface). Ops note: `100.101.235.117:8098` is NOT a raw FastAPI instance — it is the hermes auth-gated WhatsApp demo bridge (401 on `/`, 404 on `/docs`); live-protocol evidence therefore came from the prod container.

## Fix scope handed to Antigravity

Self-contained TASK.md (see repo) with the real captured 409 fixture (`fixture-409.json`): detail-unwrap in `_throwMappedError`, `ConsentRequiredError` carrying `conversationId`/`acknowledgeUrl`/`cardVersion`/`acknowledgeInstructions`, a real `acknowledgeConsent` POST on the data source, provider ack-then-retry with adopted conversation id, regression fixtures + tests for all of the above including M1-shape compat fallback.

## Result: PASS — gates green AND proven live by the production Dart client

| Gate | Result |
|---|---|
| `flutter analyze` | No issues found (1.4s) |
| `flutter test` | **24/24 passed** (12 new M2 tests: real-fixture 409 parse, detail-unwrap + M1-shape fallback, ack happy/failure, provider 3-call flow with adopted conversationId) |
| `flutter build web --release` | built |
| **`dart run tool/live_probe.dart` against the LIVE prod server** | **LIVE END-TO-END PASS**: real 409 parsed (conversation `f969b1c9…`, card v3, ack URL) → consent POST accepted → retry streamed the persona reply in 14 typewriter chunks |

Live turns consumed for evidence: 2 (curl shape-capture + Dart end-to-end), both on the canary-allowlisted `demo-pat` dogfood persona — the designed surface.

**Velocity datapoint (M2):** Antigravity did the full fix (5 files touched, 12 new tests against real fixtures) in **~410 s single dispatch, exit 0, zero re-prompts**; total work-order-receipt → live-verified ~10 min including TL probing, spec, and independent verification. One dispatch failure first (Go-flag syntax: `--print` needs `=`, not glued — recorded in BUILD_LOG).

## What changed in the app (TL-verified by file read)

- `_throwMappedError`: unwraps `detail.error` (real shape) with top-level `error` fallback (M1 compat) — all status codes.
- `ConsentRequiredError` now carries `conversationId`, `acknowledgeUrl`, `cardVersion`, `acknowledgeInstructions` alongside title/body.
- `RemoteChatDataSource.acknowledgeConsent()`: real POST with Bearer auth, body `{conversation_id, card_version}`, success = `data.acknowledged`.
- `ChatNotifier.acknowledgeConsent()`: POST ack → **adopt server-issued conversationId** → resend last message; failed ack surfaces a generic error with NO retry loop (fixes the M1 infinite-409 bug).
- `tool/live_probe.dart`: repeatable live proof harness (not a test — consumes a real turn).

## Standing input for the R&D verdict (Flutter-vs-thin-shell)

The 409→ack→turn protocol round-trips cleanly through a compiled Dart client against the live §7.4 stack with zero server changes. The session-binding insight above (consent gates a SERVER-issued conversation id, channel-agnostic) belongs in the platform recommendation regardless of which client stack wins.
