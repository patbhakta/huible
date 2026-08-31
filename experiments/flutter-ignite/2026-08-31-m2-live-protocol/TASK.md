# FlutterIgnite M2 — Live 409→consent→turn protocol (real FastAPI bodies)

Executor: Antigravity. Working dir: `app/` (M1 prototype, Flutter 3.32.2, riverpod + go_router + package:http — keep these, no new deps).

## Context

M1 passed analyze/test/build against an ASSUMED error shape (`{"error":{...}}` at top level). Live probing of the real FastAPI server on Aug 31 shows the actual bodies differ. M2 fixes the client to match the REAL protocol, proven by regression fixtures captured from the live server.

## REAL protocol (captured from live server, non-negotiable)

### 1. 409 CONSENT_REQUIRED — first message of a new session

Status 409, body (see `../fixture-409.json` for the full real capture):

```json
{"detail":{"error":{
  "code":"CONSENT_REQUIRED","status":409,
  "message":"Reality-framing consent is required before this session can proceed.",
  "conversation_id":"<server-issued NEW session uuid>",
  "acknowledge_url":"/api/v1/chat/<persona_id>/consent",
  "consent_card":{
    "version":3,
    "title":"Before we begin — please read",
    "body":"<card markdown text>",
    "acknowledge_instructions":"<instructions text>"
  }
}}}
```

FastAPI wraps raised dicts in `detail`. The card is at `detail.error.consent_card`.
The 409 carries the **server-issued conversation_id** — the client MUST adopt it for the consent POST and the retry (it is not the client's id).

### 2. POST consent acknowledge (use the `acknowledge_url` from the 409)

`POST /api/v1/chat/<persona_id>/consent`, Bearer auth, body `{"conversation_id":"<from 409>","card_version":<from card>}`.
Success 200: `{"data":{"acknowledged":true,"conversation_id":"...","persona_id":"...","card_version":3,"acknowledged_at":"...","acknowledgment_id":"consent-..."}}`

### 3. Retry the message with the adopted conversation_id

`POST /api/v1/chat/<persona_id>`, Bearer auth, body `{"message":"...","conversation_id":"<adopted>"}`.
Success 200, content-type `application/json` (plain JSON, NOT ndjson): `{"response":"<persona reply>","trace":{...}}` — M1's typewriter fallback path is correct; no change needed there.

## Required changes

1. `remote_chat_data_source.dart::_throwMappedError`: unwrap `detail` first — if decoded body has a `detail` key holding a map with `error`, use that; else fall back to top-level `error` (M1 shape). Applies to ALL status codes.
2. `chat_error.dart::ConsentRequiredError`: carry `conversationId`, `acknowledgeUrl` (relative path; default `/api/v1/chat/<persona>/consent` if absent), `cardVersion` (int), plus existing title/body. Add `acknowledgeInstructions` too.
3. New `Future<bool> acknowledgeConsent({required String conversationId, required int cardVersion, String? acknowledgeUrl})` on the remote data source (fake source: return true). POST per §2, Bearer auth, expect `data.acknowledged == true`. Map 401/403/404/409 errors through the same mapper.
4. `chat_provider.dart`: on `ConsentRequiredError` keep enough state for the ack; `acknowledgeConsent()` must now (a) POST the real consent via the data source using the error's conversationId/cardVersion/url, (b) on success ADOPT that conversationId as `_conversationId`, (c) re-send the last user message (existing retry logic). On failed ack, surface a `GenericChatError` with the server message; do NOT retry-loop.
5. Fixtures in `test/fixtures/`: write `consent_409.json` (copy the real capture at `../fixture-409.json` — you may shorten the card `body`/`acknowledge_instructions` strings to one line each but keep all keys), `consent_ack_200.json`, `chat_200.json` (with a short `trace` containing `memory_refs` as an array of uuid strings and `provenance_tiers`).
6. Tests (flutter_test only, no network):
   - 409 parse: real fixture → ConsentRequiredError with correct conversationId, acknowledgeUrl, cardVersion, title.
   - detail-unwrap fallback: a body with top-level `error` still parses (M1 compat).
   - 401/403/403-shape with `detail.error` unwrap → correct error classes and messages from the body.
   - acknowledgeConsent happy path: MockClient-style stub (http package `MockClient` from `package:http/testing.dart`) asserting the POST hits the acknowledge path with correct JSON body + Bearer header, returns true on `data.acknowledged`.
   - acknowledgeConsent failure: 409 response → returns false / throws mapped error, provider does not resend.
   - Provider flow widget or state test: sendMessage → 409 → consent card state → acknowledgeConsent (stubbed true) → retry uses adopted conversationId (assert the second POST body contains it).
7. Do not change: fake data source behavior for chat, router, settings screen, widget visuals.

## Gates (must all pass, run them yourself)

```
cd app && flutter analyze   # no issues
cd app && flutter test      # all pass, headless flutter_tester
cd app && flutter build web --release   # succeeds
```

Write `BUILD_LOG.md` at `../` (sibling of `app/`) with per-step commands + timings as you go.
