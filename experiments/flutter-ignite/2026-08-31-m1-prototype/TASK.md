# FlutterIgnite 2026-08-31 — M1 Text-Chat Client Prototype (agent-fleet Dart velocity test)

You are building a SMALL Flutter web prototype of the Huible M1 text-chat client.
This is a velocity measurement: build it clean, conventional, and FAST. No extra
features beyond the acceptance list. Do not refactor beyond the task. No new
dependencies beyond the ones named below.

## Environment

- Flutter SDK: being installed at `/opt/flutter` (binary: `/opt/flutter/bin/flutter`).
  If `flutter` is not on PATH yet, poll for `/opt/flutter-dl/FLUTTER_VERSION.txt`
  containing `INSTALL_OK` (checks every 30s, max 20 minutes) before creating the project.
- Work ONLY inside this directory (`experiments/flutter-ignite/2026-08-31-m1-prototype/`).
  Create the Flutter project in the `app/` subdirectory (`flutter create --project-name huible_m1 --platforms web,android app` from this directory).
- Linux headless box: do NOT attempt to launch a browser or `flutter run`.

## House pattern (mandatory — this measures our real stack velocity)

- State management: `flutter_riverpod` (use `StateNotifier` or generated providers — plain, no codegen).
- Routing: `go_router` with two routes: `/` (chat screen) and `/settings` (connection settings).
- HTTP: use `package:http` (do NOT add dio).
- Null safety, sound, `flutter analyze` must return ZERO issues.

## Features (acceptance list — nothing more)

1. **Chat screen** (`/`):
   - Message list (user right / persona left bubbles), auto-scroll to newest.
   - Text input field + send button; disabled while a reply is streaming.
   - While streaming: a "typing" indicator and the persona bubble renders text
     incrementally (word-by-word) as chunks arrive.
2. **Streaming reply rendering**:
   - `ChatDataSource` abstraction with two implementations:
     a. `RemoteChatDataSource` — POSTs to the persona chat API (contract below),
        with `streamMode` handling: when the response is `application/x-ndjson`
        or `text/event-stream`, parse chunks and emit deltas as they arrive;
        when it is plain JSON (the CURRENT server behavior), emit the full
        `response` string as a sequence of word-chunks (typewriter) so the
        streaming render path is exercised end-to-end.
     b. `FakeChatDataSource` — deterministic offline persona that streams a
        fixed multi-sentence reply word-by-word (used by tests + offline demo).
   - Settings toggle (in `/settings`): data source `fake | remote`.
3. **FastAPI auth handshake** (`RemoteChatDataSource`):
   - Settings screen stores: base URL (default `http://localhost:8000`),
     persona UUID, API key (obscured field).
   - On first send (or on "Test connection" button in settings), validate the
     key by calling the chat endpoint with the Bearer token and render outcomes:
     - HTTP 401 with `{"error":{"code":"AUTH_REQUIRED",...}}` → friendly
       "Sign-in required — check your API key" card, link to `/settings`.
     - HTTP 403 (`FORBIDDEN`) → "This key is not scoped to that persona" card.
     - HTTP 409 (`CONSENT_REQUIRED`) → render the consent card: title/body
       from response `consent_card` if present, with an "Acknowledge" button
       that POSTs to `/api/v1/chat/{persona_id}/consent` then retries the send.
     - HTTP 503 → "Service temporarily unavailable" card.
     - 200 → stream the reply per (2a).
   - Bearer token attached as `Authorization: Bearer <key>` on every request.

## Server contract (real API, do not invent fields)

- `POST {base}/api/v1/chat/{persona_id}` with JSON body:
  `{"message": "<text>", "conversation_id": "<uuid-string, optional>"}`
  Auth: `Authorization: Bearer <api key>` (persona-scoped).
- Success 200 JSON: `{"response": "<reply text>", "trace": {...ignore...}}`
- Error JSON shape: `{"error": {"code": "AUTH_REQUIRED"|"FORBIDDEN"|..., "status": <int>, "message": "<str>"}}`
- 409 consent body may carry a `consent_card` object; acknowledge via
  `POST {base}/api/v1/chat/{persona_id}/consent` (same auth).

## Tests (mandatory)

- At least one widget test: fake source, send a message, expect user bubble +
  streamed persona bubble to complete with the full text.
- At least one unit test per: NDJSON chunk parser, word-chunk typewriter emitter,
  401/403/409 error-mapping to UI states.
- `flutter test` must pass (headless `flutter_tester` — no browser needed).

## Verification commands (must all pass before you finish)

    /opt/flutter/bin/flutter analyze   # zero issues
    /opt/flutter/bin/flutter test      # all pass
    /opt/flutter/bin/flutter build web --release  # produces app/build/web

## Deliverables in this directory

- `app/` — the complete Flutter project (committable; exclude build artifacts
  via a `.gitignore` that ignores `app/build/` and `.dart_tool/`).
- `app/README.md` — 20 lines max: what it is, how to run (`flutter run -d chrome`
  or serve `build/web`), where settings live.
- `BUILD_LOG.md` — a plain log of what you did in order with rough per-step time,
  every command you ran and its outcome, and any friction hit (this is velocity
  data, be honest). Include the model you ran under if known.

Do NOT commit to git — the Tech Lead reviews and commits.
